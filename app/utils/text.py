"""Text manipulation helpers."""
import json
import re
import unicodedata


def truncate(text: str, max_chars: int, suffix: str = "…") -> str:
    """Truncate text to max_chars, appending suffix if truncated."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix


def slugify(text: str) -> str:
    """
    Convert text to a URL-safe slug.
    Supports ASCII + Ethiopic (transliterated via NFD normalisation).
    """
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "untitled"


def safe_json_loads(value: str | None, default=None):
    """Parse JSON without raising — return default on any error."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
