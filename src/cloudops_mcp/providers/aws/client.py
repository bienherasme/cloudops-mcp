"""Shared botocore client configuration for AWS providers.

Conservative timeouts for an interactive MCP tool call: no 60-second default
read timeout, and a short retry budget for transient failures rather than a
long hidden backoff. We rely entirely on botocore's own retry handling
(mode="standard"), no retry loop of our own on top of it.
"""

from __future__ import annotations

from botocore.config import Config  # type: ignore[import-untyped]

CONNECT_TIMEOUT_SECONDS = 4
READ_TIMEOUT_SECONDS = 10
TOTAL_MAX_ATTEMPTS = 2


def build_botocore_config() -> Config:
    return Config(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        retries={"total_max_attempts": TOTAL_MAX_ATTEMPTS, "mode": "standard"},
    )
