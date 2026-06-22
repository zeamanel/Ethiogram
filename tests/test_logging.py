"""Structured logger must not crash when a custom field name collides with a
reserved LogRecord attribute (filename, module, lineno, ...)."""
from app.core.logging import get_logger

logger = get_logger("test.logging")


def test_reserved_field_names_do_not_crash():
    # Each of these would raise "Attempt to overwrite 'X' in LogRecord" if the
    # wrapper didn't remap them.
    logger.info("doc", filename="menu.txt", module="m", lineno=10, name="n")
    logger.error("err", filename="x.pdf")
    logger.warning("warn", process="p", funcName="f", levelname="x")


def test_normal_fields_still_pass_through():
    # Sanity: non-reserved fields are unaffected (no exception).
    logger.info("ok", business_id="b1", document_id="d1", chunks=3)
