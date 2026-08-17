"""Runtime configuration for the local stdio server.

Two modes, selected via CLOUDOPS_MCP_MODE:

- "fake" (default): CLOUDOPS_MCP_SCENARIO selects healthy|bad_deploy|partial.
- "aws": CLOUDOPS_MCP_CONFIG must point at a TOML file describing real
  services (see examples/aws-cloudwatch.toml). AWS credentials are never read
  or configured here: boto3's standard credential/config provider chain
  (AWS_PROFILE, AWS_REGION/AWS_DEFAULT_REGION, environment credentials, IAM
  role) handles that entirely on its own.
"""

from __future__ import annotations

import os
from pathlib import Path

MODE_ENV_VAR = "CLOUDOPS_MCP_MODE"
SCENARIO_ENV_VAR = "CLOUDOPS_MCP_SCENARIO"
CONFIG_PATH_ENV_VAR = "CLOUDOPS_MCP_CONFIG"

DEFAULT_MODE = "fake"
DEFAULT_SCENARIO = "healthy"
VALID_MODES = ("fake", "aws")


def mode() -> str:
    raw = os.environ.get(MODE_ENV_VAR, DEFAULT_MODE)
    if raw not in VALID_MODES:
        raise RuntimeError(f"{MODE_ENV_VAR}={raw!r} is invalid, expected one of {VALID_MODES}")
    return raw


def scenario_name() -> str:
    return os.environ.get(SCENARIO_ENV_VAR, DEFAULT_SCENARIO)


def aws_config_path() -> Path:
    raw = os.environ.get(CONFIG_PATH_ENV_VAR)
    if not raw:
        raise RuntimeError(f"{CONFIG_PATH_ENV_VAR} is required when {MODE_ENV_VAR}=aws")
    return Path(raw)
