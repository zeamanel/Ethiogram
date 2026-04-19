"""Language detection utilities for Ethiogram."""
import re
import unicodedata

# Amharic Unicode block: U+1200–U+137F (Ethiopic script)
_AMHARIC_RE = re.compile(r"[\u1200-\u137f\u1380-\u139f\u2d80-\u2ddf\uab00-\uab2f]")

# Common Amharic greetings / filler words for boosting confidence
_AMHARIC_KEYWORDS = {
    "ሰላም", "እንደምን", "ምን", "ነው", "አለ", "አይ", "ደህና", "ስም",
    "ልዩ", "አዎ", "አይደለም", "ያስፈልጋል", "ወዴት", "መቼ", "እንዴት",
}


def is_amharic(text: str) -> bool:
    """Return True if the text contains Amharic (Ethiopic) characters."""
    return bool(_AMHARIC_RE.search(text))


def detect_language(text: str) -> str:
    """
    Lightweight language detector — returns ISO 639-1 code.
    Covers Amharic (am) vs English (en) for now; extend as needed.
    Intentionally avoids external deps so it runs in the webhook hot-path.
    """
    if not text or not text.strip():
        return "en"

    amharic_chars = len(_AMHARIC_RE.findall(text))
    total_chars = len(text.replace(" ", ""))

    if total_chars == 0:
        return "en"

    ratio = amharic_chars / total_chars

    # Hard rule: any Amharic keyword → am
    words = set(text.split())
    if words & _AMHARIC_KEYWORDS:
        return "am"

    # Ratio rule: >15% Ethiopic script → Amharic
    if ratio > 0.15:
        return "am"

    return "en"


def normalize_language_code(code: str) -> str:
    """Normalise BCP-47 tags like 'en-US' → 'en', 'am-ET' → 'am'."""
    if not code:
        return "en"
    return code.split("-")[0].split("_")[0].lower()
