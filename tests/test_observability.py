"""Logs must carry the tenant id so one tenant's activity can be isolated."""

import io
import json
import logging

from mtappconfig.observability import JsonFormatter, get_logger


def _capture(record_call):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = get_logger("test.observability")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    record_call(logger)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_emits_json_with_level_and_message():
    (entry,) = _capture(lambda log: log.info("loaded tenant config"))

    assert entry["level"] == "INFO"
    assert entry["message"] == "loaded tenant config"
    assert "time" in entry


def test_includes_tenant_id_when_supplied():
    (entry,) = _capture(
        lambda log: log.info(
            "loaded tenant config",
            extra={"tenant_id": "tenant-a", "event": "config.load"},
        )
    )

    assert entry["tenant_id"] == "tenant-a"
    assert entry["event"] == "config.load"


def test_omits_tenant_id_when_absent():
    (entry,) = _capture(lambda log: log.info("started"))

    assert "tenant_id" not in entry


def test_includes_the_exception_when_logging_an_error():
    def emit(log):
        try:
            raise ValueError("store unreachable")
        except ValueError:
            log.warning("refresh failed", extra={"tenant_id": "tenant-a"}, exc_info=True)

    (entry,) = _capture(emit)

    assert "ValueError: store unreachable" in entry["error"]
