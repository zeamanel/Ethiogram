"""Key-free geo helpers: coordinate parsing + directions URL."""
import pytest

from app.utils.geo import directions_url, parse_coordinates


@pytest.mark.parametrize("text, expected", [
    ("9.0123, 38.7456", (9.0123, 38.7456)),
    ("9.0123,38.7456", (9.0123, 38.7456)),
    ("https://www.google.com/maps/@9.03,38.74,15z", (9.03, 38.74)),
    ("https://maps.google.com/?q=9.05,38.70", (9.05, 38.70)),
    # both viewport (@) and pin (!3d!4d) → prefer the pin
    ("https://www.google.com/maps/place/X/@9.00,38.00,17z/data=!3d9.0123!4d38.7456",
     (9.0123, 38.7456)),
])
def test_parse_coordinates(text, expected):
    assert parse_coordinates(text) == expected


@pytest.mark.parametrize("text", [None, "", "no coords here", "999,999", "abc,def"])
def test_parse_coordinates_invalid(text):
    assert parse_coordinates(text) is None


def test_directions_url_prefers_coords():
    assert directions_url(9.01, 38.74) == "https://www.google.com/maps/search/?api=1&query=9.01,38.74"


def test_directions_url_falls_back_to_address():
    url = directions_url(None, None, "Bole Rd, Addis Ababa")
    assert url == "https://www.google.com/maps/search/?api=1&query=Bole%20Rd%2C%20Addis%20Ababa"


def test_directions_url_none_when_empty():
    assert directions_url(None, None, None) is None
    assert directions_url(None, None, "   ") is None
