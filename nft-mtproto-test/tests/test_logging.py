import logging

from app.utils.log import MASK, BOT, mask, setup_logging

BOT_TOKEN = "123456789:AAH_example-token-value-xyz"


def test_mask_known_secret_and_token_patterns():
    text = f"hash=myhash token {BOT_TOKEN} url=https://x/v1/ping?token=12:abc&a=1"
    out = mask(text, ["myhash"])
    assert "myhash" not in out and BOT_TOKEN not in out and "12:abc" not in out
    assert out.count(MASK) == 3


def test_file_log_is_masked_including_traceback(tmp_path):
    setup_logging("DEBUG", tmp_path, secrets=["supersecret"])
    log = logging.getLogger(BOT)
    log.info("using supersecret and %s", BOT_TOKEN)
    try:
        raise RuntimeError("failed with supersecret")
    except RuntimeError:
        log.exception("boom")
    for h in logging.getLogger().handlers:
        h.flush()
    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "supersecret" not in content and BOT_TOKEN not in content
    assert "app.bot" in content and "RuntimeError" in content
    setup_logging("INFO")  # detach the file handler


def test_log_file_is_text_rotated_every_6_hours(tmp_path):
    from logging.handlers import TimedRotatingFileHandler
    setup_logging("INFO", tmp_path)
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler))
    assert handler.interval == 6 * 3600 and handler.utc and handler.baseFilename.endswith("app.log")
    logging.getLogger(BOT).info("hidden owner logged")
    handler.flush()
    assert "hidden owner logged" in (tmp_path / "app.log").read_text(encoding="utf-8")
    setup_logging("INFO")


def test_plain_numbers_are_not_masked():
    assert mask("price 8755 at 12:30") == "price 8755 at 12:30"
