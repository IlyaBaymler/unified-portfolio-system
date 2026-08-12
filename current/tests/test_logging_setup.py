from trading_robot.logging_setup import redact_sensitive_text


def test_sensitive_tokens_are_redacted():
    text = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 "
        "TBANK_SANDBOX_TOKEN=secret-token-value"
    )
    redacted = redact_sensitive_text(text)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "secret-token-value" not in redacted
    assert redacted.count("<REDACTED>") == 2


def test_compact_and_debug_logs_are_separated_and_redacted(tmp_path):
    import logging

    from trading_robot.logging_setup import configure_file_logging

    root = logging.getLogger()
    managed_names = {"moex_compact_file", "moex_debug_file", "moex_console"}

    # Keep the global test process clean even if the assertion fails.
    previous = [
        handler
        for handler in list(root.handlers)
        if getattr(handler, "name", None) in managed_names
    ]
    for handler in previous:
        root.removeHandler(handler)
        handler.close()

    try:
        paths = configure_file_logging(tmp_path, debug_enabled=True)
        logger = logging.getLogger("tests.observability")
        logger.debug("debug-only payload")
        logger.info(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        )

        for handler in root.handlers:
            if getattr(handler, "name", None) in managed_names:
                handler.flush()

        compact = paths.compact.read_text(encoding="utf-8")
        debug = paths.debug.read_text(encoding="utf-8")

        assert "debug-only payload" not in compact
        assert "debug-only payload" in debug
        assert "abcdefghijklmnopqrstuvwxyz" not in compact
        assert "abcdefghijklmnopqrstuvwxyz" not in debug
        assert "Bearer <REDACTED>" in compact
        assert "Bearer <REDACTED>" in debug
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "name", None) in managed_names:
                root.removeHandler(handler)
                handler.close()
