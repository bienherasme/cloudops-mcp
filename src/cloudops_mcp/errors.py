"""Sanitized error taxonomy for tool execution.

Raised by the services layer, translated to MCP tool errors by the tools
layer. Messages here must always be safe to show a caller: no raw SDK
exceptions, no credentials, no internal stack details.
"""

from __future__ import annotations


class InvalidArgumentError(Exception):
    """Semantically invalid tool argument, e.g. start >= end or an unknown metric name."""
