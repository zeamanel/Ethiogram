"""
Utility helpers shared across the Ethiogram codebase.
"""
from .language import detect_language, is_amharic, normalize_language_code
from .phone import normalize_ethiopian_phone
from .text import truncate, slugify, safe_json_loads

__all__ = [
    "detect_language",
    "is_amharic",
    "normalize_language_code",
    "normalize_ethiopian_phone",
    "truncate",
    "slugify",
    "safe_json_loads",
]
