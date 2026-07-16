"""Regression: ADMIN_TELEGRAM_IDS must parse in every env-var shape without
crashing Settings() at import time. A bare int once bricked the container
(pydantic decoded "959519454" to int, failing the list[int] type check)."""
import pytest

from app.core.config import Settings


@pytest.mark.parametrize("raw, expected", [
    ("959519454", [959519454]),          # bare int — the value that bricked prod
    ("[959519454]", [959519454]),        # JSON list (cloudbuild default)
    ("959519454,123", [959519454, 123]),  # comma-separated
    ("[959519454, 123]", [959519454, 123]),
    ("", []),                            # empty string
    ("[]", []),                          # empty list
    ("  959519454  ", [959519454]),      # surrounding whitespace
])
def test_admin_telegram_ids_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", raw)
    assert Settings().admin_telegram_ids == expected


def test_admin_telegram_ids_default_empty(monkeypatch):
    monkeypatch.delenv("ADMIN_TELEGRAM_IDS", raising=False)
    assert Settings().admin_telegram_ids == []
