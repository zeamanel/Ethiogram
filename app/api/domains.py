# app/api/domains.py
"""Custom domain (.com) connection for a business's website.

Flow:
  1. POST   /businesses/{id}/domain         add a domain -> get DNS instructions
  2.        owner adds the TXT + CNAME records at their registrar
  3. POST   /businesses/{id}/domain/verify  we DNS-lookup the TXT token -> verified
  4.        platform operator runs the per-domain `gcloud run domain-mappings`
            command (provisions TLS); traffic to the domain then hits this app,
            which routes by Host header to the business's landing page.

We verify OWNERSHIP (the TXT token) before routing, so one business can't claim
another's domain. TLS for the domain is provisioned at the edge (Cloud Run
domain mapping / Google-managed cert) — that's the one step outside app code.
"""
import asyncio
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.businesses import _get_owned_business
from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.core.logging import get_logger
from app.db.models import CustomDomain
from app.db.session import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/businesses", tags=["domains"])

_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.[a-z0-9-]{1,63})+$")
_VERIFY_HOST = "_ethiogram-verify"


class DomainConnectRequest(BaseModel):
    domain: str

    @field_validator("domain")
    @classmethod
    def _valid(cls, v: str) -> str:
        v = (v or "").strip().lower().rstrip(".")
        if v.startswith("http://") or v.startswith("https://"):
            v = v.split("://", 1)[1]
        v = v.split("/")[0]
        if not _DOMAIN_RE.fullmatch(v) or len(v) > 255:
            raise ValueError("Enter a valid domain like shop.example.com or example.com")
        return v


class DnsRecord(BaseModel):
    type: str
    host: str
    value: str
    purpose: str


class DomainResponse(BaseModel):
    domain: Optional[str]
    is_verified: bool
    verified_at: Optional[str]
    dns_records: list[DnsRecord]
    setup_command: Optional[str]      # the per-domain edge/TLS step (operator runs)
    live_url: Optional[str]


def _records_for(domain: str, token: str) -> list[DnsRecord]:
    return [
        DnsRecord(type="TXT", host=f"{_VERIFY_HOST}.{domain}",
                  value=f"ethiogram-verify={token}",
                  purpose="Proves you own this domain"),
        DnsRecord(type="CNAME", host=domain, value=settings.custom_domain_target,
                  purpose="Routes your domain to your site (with HTTPS)"),
    ]


def _to_response(cd: Optional[CustomDomain]) -> DomainResponse:
    if cd is None:
        return DomainResponse(domain=None, is_verified=False, verified_at=None,
                              dns_records=[], setup_command=None, live_url=None)
    cmd = (f"gcloud run domain-mappings create --service {settings.cloud_run_service} "
           f"--domain {cd.domain} --region {settings.cloud_run_region}")
    return DomainResponse(
        domain=cd.domain,
        is_verified=cd.is_verified,
        verified_at=cd.verified_at.isoformat() if cd.verified_at else None,
        dns_records=_records_for(cd.domain, cd.dns_verification_token),
        setup_command=cmd,
        live_url=f"https://{cd.domain}",
    )


async def _get_domain(business_id: uuid.UUID, db: AsyncSession) -> Optional[CustomDomain]:
    return (await db.execute(
        select(CustomDomain).where(CustomDomain.business_id == business_id)
    )).scalar_one_or_none()


@router.get("/{business_id}/domain", response_model=DomainResponse)
async def get_domain(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DomainResponse:
    await _get_owned_business(business_id, current_user.id, db)
    return _to_response(await _get_domain(business_id, db))


@router.post("/{business_id}/domain", response_model=DomainResponse, status_code=201)
async def connect_domain(
    business_id: uuid.UUID,
    body: DomainConnectRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DomainResponse:
    """Add (or replace) the business's custom domain and return DNS setup steps."""
    await _get_owned_business(business_id, current_user.id, db)

    # the domain must not already belong to a DIFFERENT business
    taken = (await db.execute(
        select(CustomDomain).where(CustomDomain.domain == body.domain)
    )).scalar_one_or_none()
    if taken is not None and taken.business_id != business_id:
        raise AlreadyExistsError("CustomDomain")

    cd = await _get_domain(business_id, db)
    token = secrets.token_hex(16)
    if cd is None:
        cd = CustomDomain(business_id=business_id, domain=body.domain,
                          dns_verification_token=token,
                          domain_type=("apex" if body.domain.count(".") == 1 else "subdomain"))
        db.add(cd)
    else:
        # re-point to a new domain → reset verification
        cd.domain = body.domain
        cd.dns_verification_token = token
        cd.is_verified = False
        cd.verified_at = None
        cd.is_active = False
    await db.flush()
    logger.info("Custom domain added", business_id=str(business_id), domain=body.domain)
    return _to_response(cd)


@router.post("/{business_id}/domain/verify", response_model=DomainResponse)
async def verify_domain(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DomainResponse:
    """Look up the TXT verification record and mark the domain verified if found."""
    await _get_owned_business(business_id, current_user.id, db)
    cd = await _get_domain(business_id, db)
    if cd is None:
        raise NotFoundError("CustomDomain", str(business_id))

    ok = await asyncio.get_event_loop().run_in_executor(
        None, _txt_has_token, f"{_VERIFY_HOST}.{cd.domain}", cd.dns_verification_token
    )
    if ok:
        cd.is_verified = True
        cd.is_active = True
        if cd.verified_at is None:
            cd.verified_at = datetime.now(timezone.utc)
        logger.info("Custom domain verified", business_id=str(business_id), domain=cd.domain)
    return _to_response(cd)


@router.delete("/{business_id}/domain", status_code=204)
async def disconnect_domain(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_owned_business(business_id, current_user.id, db)
    cd = await _get_domain(business_id, db)
    if cd is not None:
        await db.delete(cd)


def _txt_has_token(name: str, token: str) -> bool:
    """Blocking DNS TXT lookup (run in an executor). True if any record carries
    the token. Network/NXDOMAIN failures → False (not verified)."""
    try:
        import dns.resolver
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except Exception:
        return False
    for rdata in answers:
        try:
            txt = b"".join(rdata.strings).decode("utf-8", "ignore")
        except Exception:
            txt = str(rdata).strip('"')
        if token in txt:
            return True
    return False
