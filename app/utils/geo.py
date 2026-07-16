# app/utils/geo.py
"""Lightweight, key-free geo helpers.

We deliberately avoid any Maps API key: owners set a pin by pasting a Google
Maps link (or "lat,lng"), and customers open directions via a plain Google Maps
query URL that works on every device. No geocoding, no billing.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

# A Google Maps place link usually carries BOTH the viewport centre (@lat,lng)
# and the precise pin (!3d<lat>!4d<lng>). Prefer the pin, then explicit query
# params, then the viewport, then a bare "lat,lng".
_PIN_3D4D = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
_QUERY = re.compile(r"[?&](?:q|ll|query|destination|daddr)=(-?\d{1,3}\.\d+),\s*(-?\d{1,3}\.\d+)")
_AT = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
_BARE = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")


def parse_coordinates(text: Optional[str]) -> Optional[tuple[float, float]]:
    """Extract (lat, lng) from a "lat,lng" string or a Google Maps URL.

    Returns None when nothing parseable / in-range is found."""
    if not text:
        return None
    s = str(text).strip()
    for rx in (_PIN_3D4D, _QUERY, _AT, _BARE):
        m = rx.search(s)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
                return (round(lat, 6), round(lng, 6))
    return None


def directions_url(lat: Optional[float] = None, lng: Optional[float] = None,
                   address: Optional[str] = None) -> Optional[str]:
    """A key-free Google Maps link that opens directions/search. Prefers exact
    coordinates, falling back to the address text. None when neither is set."""
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    if address and address.strip():
        return f"https://www.google.com/maps/search/?api=1&query={quote(address.strip())}"
    return None
