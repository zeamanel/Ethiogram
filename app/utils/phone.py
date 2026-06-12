"""Ethiopian phone number normalisation."""
import re

_DIGITS_RE = re.compile(r"\D")

# Ethiopian mobile leading digits after country code (9 = Ethio Telecom, 7 = Safaricom ET)
_ET_MOBILE_LEADING = {"9", "7"}


def normalize_ethiopian_phone(raw: str) -> str | None:
    """
    Normalise a raw Ethiopian phone string to E.164 (+251XXXXXXXXX).

    Handles:
      - 0912345678  → +251912345678
      - +251912345678  → +251912345678
      - 251912345678   → +251912345678
      - 912345678      → +251912345678  (9-digit, assume ET)

    Returns None if the number doesn't look valid.
    """
    if not raw:
        return None

    digits = _DIGITS_RE.sub("", raw)

    # Strip leading country code variants
    if digits.startswith("251") and len(digits) == 12:
        local = digits[3:]
    elif digits.startswith("0") and len(digits) == 10:
        local = digits[1:]
    elif len(digits) == 9:
        local = digits
    else:
        return None

    # Validate: 9 digits, leading 9 (Ethio Telecom) or 7 (Safaricom ET)
    if len(local) != 9 or local[0] not in _ET_MOBILE_LEADING:
        return None

    return f"+251{local}"
