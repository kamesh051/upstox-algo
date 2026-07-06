"""structlog configuration: JSON lines for production, pretty console for dev.

Optionally mirrors every log record onto the event bus as a ``log`` event
(PHASE-UI live log tail). The bridge coerces values to JSON-safe primitives
and never lets a bus problem break logging.
"""

from __future__ import annotations

import logging
import sys

import structlog

_PRIMITIVES = (str, int, float, bool, type(None))


def _bus_processor(bus):
    from trading_system.events import make_event

    def processor(logger, method_name, event_dict):
        try:
            payload = {
                k: (v if isinstance(v, _PRIMITIVES) else str(v))
                for k, v in event_dict.items()
                if k != "timestamp"
            }
            payload["level"] = method_name
            bus.publish(make_event("log", payload))
        except Exception:  # logging must never fail because of the bus
            pass
        return event_dict

    return processor


def setup_logging(level: str = "INFO", json_output: bool = True, bus=None) -> None:
    """Configure structlog. Re-callable: pass ``bus`` to attach the event bridge."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if bus is not None:
        shared_processors.append(_bus_processor(bus))

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        # allow re-configuration (the paper session re-runs setup with a bus)
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
