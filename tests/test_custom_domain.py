"""Tests for custom domains: connect -> DNS instructions -> verify -> Host routing.

The DNS TXT lookup is mocked (no real resolver). Verifies ownership-token flow
and that a verified domain serves the business's landing page at '/'.
"""
import uuid

import pytest
from sqlalchemy import select

import app.api.domains as domains_api
from app.db.models import Business, CustomDomain, LandingPage, User


async def _seed(db, user_id, business_id, slug="acme"):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Acme", slug=slug,
                    description="We sell things"))
    await db.flush()


@pytest.mark.asyncio
async def test_connect_returns_dns_instructions(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.post(
        f"/api/v1/businesses/{sample_business_id}/domain",
        json={"domain": "Shop.Example.com"},   # mixed case → normalized
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["domain"] == "shop.example.com"
    assert body["is_verified"] is False
    types = {r["type"] for r in body["dns_records"]}
    assert types == {"TXT", "CNAME"}
    txt = next(r for r in body["dns_records"] if r["type"] == "TXT")
    assert txt["host"] == "_ethiogram-verify.shop.example.com"
    assert txt["value"].startswith("ethiogram-verify=")
    cname = next(r for r in body["dns_records"] if r["type"] == "CNAME")
    assert cname["value"] == "ghs.googlehosted.com"
    assert "gcloud run domain-mappings create" in body["setup_command"]
    assert body["live_url"] == "https://shop.example.com"


@pytest.mark.asyncio
async def test_invalid_domain_rejected(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.post(f"/api/v1/businesses/{sample_business_id}/domain",
                             json={"domain": "not a domain"},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_domain_taken_by_other_business(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    other = uuid.uuid4()
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Other", slug="other"))
    db.add(CustomDomain(business_id=other, domain="taken.com", dns_verification_token="t"))
    await db.flush()
    resp = await client.post(f"/api/v1/businesses/{sample_business_id}/domain",
                             json={"domain": "taken.com"},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_verify_flips_when_txt_present(client, db, monkeypatch, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/businesses/{sample_business_id}/domain",
                      json={"domain": "shop.example.com"}, headers=hdr)

    # simulate the TXT record being present at the registrar
    monkeypatch.setattr(domains_api, "_txt_has_token", lambda name, token: True)
    resp = await client.post(f"/api/v1/businesses/{sample_business_id}/domain/verify", headers=hdr)
    assert resp.status_code == 200
    assert resp.json()["is_verified"] is True

    cd = (await db.execute(select(CustomDomain).where(
        CustomDomain.business_id == sample_business_id))).scalar_one()
    assert cd.is_verified is True and cd.is_active is True and cd.verified_at is not None


@pytest.mark.asyncio
async def test_verify_stays_false_when_txt_absent(client, db, monkeypatch, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/businesses/{sample_business_id}/domain",
                      json={"domain": "shop.example.com"}, headers=hdr)
    monkeypatch.setattr(domains_api, "_txt_has_token", lambda name, token: False)
    resp = await client.post(f"/api/v1/businesses/{sample_business_id}/domain/verify", headers=hdr)
    assert resp.json()["is_verified"] is False


@pytest.mark.asyncio
async def test_disconnect_domain(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/businesses/{sample_business_id}/domain",
                      json={"domain": "shop.example.com"}, headers=hdr)
    resp = await client.delete(f"/api/v1/businesses/{sample_business_id}/domain", headers=hdr)
    assert resp.status_code == 204
    assert (await db.execute(select(CustomDomain))).first() is None


@pytest.mark.asyncio
async def test_cannot_manage_unowned_domain(client, db, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=sample_user_id))
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs-d"))
    await db.flush()
    resp = await client.post(f"/api/v1/businesses/{other}/domain",
                             json={"domain": "x.com"},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404


# ── Host routing: a verified domain serves the landing page at '/' ───────────

@pytest.mark.asyncio
async def test_root_serves_landing_for_verified_host(client, db):
    uid = uuid.uuid4()
    biz = uuid.uuid4()
    db.add(User(id=uid))
    db.add(Business(id=biz, owner_id=uid, name="Selam Store", slug="selam", description="Fashion"))
    db.add(LandingPage(business_id=biz, is_published=True))
    db.add(CustomDomain(business_id=biz, domain="selam.com", dns_verification_token="t",
                        is_verified=True, is_active=True))
    await db.flush()

    # request '/' with the custom Host → that business's page
    resp = await client.get("/", headers={"host": "selam.com"})
    assert resp.status_code == 200
    assert "Selam Store" in resp.text
    assert "selam.com" in resp.text          # canonical/links use the custom domain


@pytest.mark.asyncio
async def test_root_placeholder_for_unknown_host(client, db):
    resp = await client.get("/", headers={"host": "unmapped.example.com"})
    assert resp.status_code == 200
    assert "Ethiogram" in resp.text          # platform placeholder, not a store


@pytest.mark.asyncio
async def test_unverified_host_does_not_route(client, db):
    uid = uuid.uuid4(); biz = uuid.uuid4()
    db.add(User(id=uid))
    db.add(Business(id=biz, owner_id=uid, name="Pending Co", slug="pending"))
    db.add(CustomDomain(business_id=biz, domain="pending.com", dns_verification_token="t",
                        is_verified=False, is_active=False))
    await db.flush()
    resp = await client.get("/", headers={"host": "pending.com"})
    assert "Pending Co" not in resp.text     # not routed until verified
