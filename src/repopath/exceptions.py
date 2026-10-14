"""The exceptions and the warning `repopath` defines.

A failure of a hosting service or of the connection to it is the `OSError` type
that `pathlib` raises for the same condition (`FileNotFoundError`,
`PermissionError`, `TimeoutError`, `ConnectionError`, ...); this module holds
the two that name a cause no builtin does. An exception of the HTTP transport is
never raised across the API. A bad argument is a plain `ValueError` or
`TypeError`.
"""

from __future__ import annotations

from pathlib_next import PathError

__all__ = [
    "BadReplyError",
    "InsecureTransportWarning",
    "RateLimitError",
    "RepoPathError",
]


class RepoPathError(PathError):
    """Base class of the exceptions `repopath` defines for a failure at run
    time that no builtin describes."""


class RateLimitError(RepoPathError, BlockingIOError):
    """The service refused a request because the caller used up its rate limit
    (errno `EAGAIN`): HTTP 403 or 429 with a `RateLimit-Remaining: 0` or a
    `Retry-After` header. The message says when to try again."""


class BadReplyError(RepoPathError, OSError):
    """The service answered with something that is not the shape its API
    documents (errno `EIO`): a page of a proxy or a captive portal, a gateway's
    JSON, `null`, a truncated body, a size that is not a number."""


class InsecureTransportWarning(UserWarning):
    """A `RepoBackend` is about to send an `Authorization` header (its token,
    or one given in `headers=`) over plain `http://` to a host that is not
    this machine, where anyone on the route can read it."""
