"""The backends of a repository path: the contract, and the one that sends the
requests through `pathlib_next.http`."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import ipaddress as _ipaddress
import urllib.parse as _urlparse
import warnings as _warnings

from pathlib_next import providers as _providers
from pathlib_next.http import (
    DEFAULT_TIMEOUT,
    HTTPClient,
    HTTPResponse,
    HTTPStatusError,
    get_transport,
    peer_text,
)

from ._version import __version__
from .exceptions import InsecureTransportWarning, RateLimitError

_UNSET = object()
"""The `timeout` of a call that did not give one: the backend's own."""


def _is_loopback(host: "str | None") -> bool:
    if not host:
        return False
    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _warn_if_plaintext(url: str, authorized: bool) -> None:
    if not url.lower().startswith("http://"):
        return
    if not authorized:
        return
    host = _urlparse.urlsplit(url).hostname
    if _is_loopback(host):
        return
    _warnings.warn(
        f"Sending an Authorization header over plain http to {host}; "
        "use an https API root",
        InsecureTransportWarning,
        stacklevel=3,
    )


def _rate_limit_error(response, path) -> "RateLimitError | None":
    """The `RateLimitError` for a rate-limited reply, else None. Both hosts
    signal a primary limit with 403 or 429 and a `(X-)RateLimit-Remaining: 0`
    header, and a secondary limit with 403 or 429 and a `Retry-After` header;
    a bare 429 is a limit too. Checked before the generic mapping, which would
    read a secondary-limit 403 as PermissionError and a 429 as EIO."""
    if response is None or response.status not in (403, 429):
        return None
    headers = response.headers
    remaining = headers.get("X-RateLimit-Remaining", headers.get("RateLimit-Remaining"))
    retry_after = headers.get("Retry-After")
    if response.status == 403 and remaining != "0" and retry_after is None:
        return None
    if retry_after is not None:
        when = f"retry after {peer_text(retry_after, 40)}s"
    else:
        reset = headers.get("X-RateLimit-Reset", headers.get("RateLimit-Reset", "?"))
        when = f"resets at {peer_text(reset, 40)}"
    return RateLimitError(
        _errno.EAGAIN,
        f"API rate limit exceeded (HTTP {response.status}) ({when})",
        str(path),
    )


@_contextlib.contextmanager
def translate_repo_errors(path):
    """Maps what a request raised to what `pathlib` raises for `path`: a
    rate-limited reply is `RateLimitError`, 404 `FileNotFoundError`, 401 and
    403 `PermissionError`, any other status `OSError(EIO)`; a timeout or a
    refused connection keeps its type and names `path`."""
    # `from None`: a chained cause is printed by every traceback. The status
    # code or exception type stays in the message.
    try:
        yield
    except HTTPStatusError as e:
        response = e.response
        status = response.status
        reason = peer_text(response.reason or "")
        limited = _rate_limit_error(response, path)
        if limited is not None:
            raise limited from None
        if status == 404:
            raise FileNotFoundError(_errno.ENOENT, "No such file", str(path)) from None
        elif status in (401, 403):
            raise PermissionError(
                _errno.EACCES, "Permission denied", str(path)
            ) from None
        else:
            message = f"HTTP Error {status} {reason}".rstrip()
            raise OSError(_errno.EIO, message, str(path)) from None
    except TimeoutError as e:
        raise TimeoutError(f"Timeout for {path} ({type(e).__name__})") from None
    except ConnectionError as e:
        raise ConnectionError(
            f"Connection error for {path} ({type(e).__name__})"
        ) from None


class BaseRepoBackend(object):
    """Protocol for issuing an authenticated HTTP request against the REST API
    of a hosting service. Subclass this to plug in custom auth or session
    handling; `RepoBackend` is the one that sends through `pathlib_next.http`.

    A backend may carry an `api_base` (the API root, replacing the one derived
    from the instance of a path) and a `cache` (a dict, to memoize the default
    branch of a repository); a path uses either only when the backend has
    one."""

    # Weakly referenceable so a path can record which backends it derived.
    __slots__ = ("__weakref__",)

    @_providers.notimplemented
    def request(self, method, url, headers=None, params=None, **kwargs):
        """Send one request to the API and return its `HTTPResponse`."""


class RepoBackend(HTTPClient, BaseRepoBackend):
    """The transport and the optional bearer `token` sent on every request.
    Unauthenticated works fine for public repositories, subject to the host's
    anonymous rate limit (60 requests an hour for github.com). A context
    manager: leaving the block closes a transport the backend built.

    `transport` is any object with a `send()` method as
    `pathlib_next.http.HTTPTransport.send()`; without one the built-in
    `HTTPTransport` is built on first use. `session=` (a `requests.Session`)
    and `requests_args` (`verify=`, `proxies=`, ...) select the `requests`
    transport of `pathlib-next-requests`; without it they raise `ImportError`.
    `headers` go with every request, under the request's own. `api_base`, when
    set, overrides the API root a path derives from its instance entirely
    (e.g. `https://api.github.com` or `https://{host}/api/v3`) -- the seam a
    test fake, or a reverse-proxied self-hosted setup, plugs into, since the
    derived root is always `https`; a request that carries an `Authorization`
    header to a plain `http://` URL on a host other than loopback warns
    (`InsecureTransportWarning`). Every request gets
    `timeout=DEFAULT_TIMEOUT` (`(10, 60)` seconds) unless `timeout` here or in
    the call says otherwise; `None` waits without limit. A `GET` follows
    redirects, dropping the token when the host changes."""

    __slots__ = ("token", "api_base", "cache")

    def __init__(
        self,
        token: str = None,
        session=None,
        api_base: str = None,
        *,
        transport=None,
        headers=None,
        timeout=DEFAULT_TIMEOUT,
        **requests_args,
    ):
        if session is not None or requests_args:
            if transport is not None:
                raise TypeError("give a transport, or a session and its arguments")
            transport = get_transport("requests")(session, **requests_args)
        super().__init__(
            transport,
            headers=headers,
            timeout=timeout,
            user_agent=f"repopath/{__version__}",
        )
        self.token = token or None
        self.api_base = api_base.rstrip("/") if api_base else None
        # Memoization shared by every path that shares this backend (the
        # resolved default branch of a repository, see `_hosted.py`).
        self.cache = {}

    def __repr__(self) -> str:
        # The token and the headers are left out: they are credentials.
        return f"{type(self).__name__}(api_base={self.api_base!r})"

    def request(
        self,
        method,
        url,
        headers=None,
        params=None,
        *,
        data=None,
        timeout=_UNSET,
        stream=False,
    ) -> HTTPResponse:
        sent = dict(headers or {})
        authorized = bool(self.token) or any(
            name.lower() == "authorization" for name in (*self.headers, *sent)
        )
        if params:
            query = _urlparse.urlencode(
                [(k, v) for k, v in dict(params).items() if v is not None], doseq=True
            )
            if query:
                url = url + ("&" if "?" in url else "?") + query
        _warn_if_plaintext(url, authorized)
        return super().request(
            method,
            url,
            data=data,
            headers=sent,
            stream=stream,
            timeout=self.timeout if timeout is _UNSET else timeout,
            auth=f"Bearer {self.token}" if self.token else None,
        )
