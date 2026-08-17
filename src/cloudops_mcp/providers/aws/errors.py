"""Maps known-external AWS/botocore failures to ProviderFailureReason.

This only classifies exceptions the caller already knows are external
failures (it never decides what to catch). logs.py and metrics.py catch a
fixed, narrow tuple of botocore exception types; ParamValidationError, and
anything else (a bug in our own request construction), is deliberately left
out of that tuple so it propagates instead of being reported as telemetry.
"""

from __future__ import annotations

from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from cloudops_mcp.domain.models import ProviderFailureReason

_AUTH_ERROR_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "AuthFailure",
    "Forbidden",
}
_RATE_LIMITED_CODES = {
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "RequestThrottledException",
}
_UNAVAILABLE_CODES = {
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "InternalError",
    "InternalFailure",
    "InternalServerError",
}

# Exception types this module knows how to classify. logs.py/metrics.py
# should catch exactly this tuple, nothing broader.
KNOWN_AWS_EXCEPTIONS: tuple[type[Exception], ...] = (
    ClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
)


def classify_aws_exception(exc: Exception) -> ProviderFailureReason:
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError)):
        return ProviderFailureReason.TIMEOUT
    if isinstance(exc, EndpointConnectionError):
        return ProviderFailureReason.UNAVAILABLE
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return ProviderFailureReason.AUTH_ERROR
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _AUTH_ERROR_CODES:
            return ProviderFailureReason.AUTH_ERROR
        if code in _RATE_LIMITED_CODES:
            return ProviderFailureReason.RATE_LIMITED
        if code in _UNAVAILABLE_CODES:
            return ProviderFailureReason.UNAVAILABLE
        return ProviderFailureReason.UNKNOWN
    return ProviderFailureReason.UNKNOWN
