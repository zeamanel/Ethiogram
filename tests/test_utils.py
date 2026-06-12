# tests/test_utils.py
import pytest
from app.utils.language import detect_language, is_amharic, normalize_language_code
from app.utils.phone import normalize_ethiopian_phone
from app.utils.text import truncate, slugify, safe_json_loads


class TestLanguageDetection:
    def test_detects_english(self):
        assert detect_language("Hello, how can I help you today?") == "en"

    def test_detects_amharic_script(self):
        assert detect_language("ሰላም እንደምን ነህ?") == "am"

    def test_detects_amharic_keyword(self):
        assert detect_language("ሰላም hello") == "am"

    def test_empty_string_defaults_to_english(self):
        assert detect_language("") == "en"
        assert detect_language("   ") == "en"

    def test_is_amharic_true(self):
        assert is_amharic("ሰላም") is True

    def test_is_amharic_false(self):
        assert is_amharic("Hello world") is False

    def test_normalize_bcp47(self):
        assert normalize_language_code("en-US") == "en"
        assert normalize_language_code("am-ET") == "am"
        assert normalize_language_code("") == "en"


class TestPhoneNormalization:
    def test_local_format(self):
        assert normalize_ethiopian_phone("0912345678") == "+251912345678"

    def test_international_format(self):
        assert normalize_ethiopian_phone("+251912345678") == "+251912345678"

    def test_no_plus_international(self):
        assert normalize_ethiopian_phone("251912345678") == "+251912345678"

    def test_nine_digit(self):
        assert normalize_ethiopian_phone("912345678") == "+251912345678"

    def test_safaricom_prefix(self):
        assert normalize_ethiopian_phone("0712345678") == "+251712345678"

    def test_invalid_prefix(self):
        assert normalize_ethiopian_phone("0512345678") is None

    def test_empty(self):
        assert normalize_ethiopian_phone("") is None
        assert normalize_ethiopian_phone(None) is None

    def test_with_spaces_dashes(self):
        assert normalize_ethiopian_phone("091 234 5678") == "+251912345678"
        assert normalize_ethiopian_phone("091-234-5678") == "+251912345678"


class TestTextUtils:
    def test_truncate_no_change(self):
        assert truncate("hello", 10) == "hello"

    def test_truncate_cuts(self):
        result = truncate("hello world", 8)
        assert result == "hello w…"
        assert len(result) == 8

    def test_slugify_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_slugify_special_chars(self):
        assert slugify("  Hello & World! ") == "hello-world"

    def test_slugify_multiple_spaces(self):
        assert slugify("foo   bar") == "foo-bar"

    def test_slugify_empty(self):
        assert slugify("") == "untitled"

    def test_safe_json_loads_valid(self):
        assert safe_json_loads('{"key": 1}') == {"key": 1}

    def test_safe_json_loads_invalid(self):
        assert safe_json_loads("not-json") is None
        assert safe_json_loads("not-json", default=[]) == []

    def test_safe_json_loads_none(self):
        assert safe_json_loads(None) is None
