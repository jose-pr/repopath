"""`HostedRepoPath`, what `GitHubPath` and `GitLabPath` share: a repository
named by an owner and a name on an instance, at a ref, read through the REST
API of the service."""

from __future__ import annotations

import errno as _errno
import typing as _ty
import urllib.parse as _urlparse

from pathlib_next import providers as _providers
from pathlib_next.http import json_body, peer_text

from ._backend import BaseRepoBackend, RepoBackend
from ._kinds import RepoPath
from .exceptions import BadReplyError

if _ty.TYPE_CHECKING:
    from pathlib_next import FileStat

#: What a path segment keeps unescaped when the path is spelled as a URI.
_SAFE_SEGMENT = "!$&'()*+,;=:@"

_FORBIDDEN_IN_INSTANCE = set("/\\?#@ \t\r\n") | {chr(i) for i in range(32)}


def checked_instance(text: object) -> str:
    """`text` if it is a host name, an IP address (an IPv6 one in brackets) or
    either followed by `:port`; else `TypeError` for a non-string and
    `ValueError`. Nothing in it can reach another part of a URL."""
    if not isinstance(text, str):
        raise TypeError(f"instance= must be a str, not {type(text).__name__!r}")
    if not text or _FORBIDDEN_IN_INSTANCE & set(text):
        raise ValueError(f"instance= must be a host and an optional port, not {text!r}")
    try:
        parts = _urlparse.urlsplit("//" + text)
        host, _port = parts.hostname, parts.port
    except ValueError:
        host = None
    if not host:
        raise ValueError(f"instance= must be a host and an optional port, not {text!r}")
    return text


def _checked_name(path, what: str, value: object, inherited, nested: bool) -> str:
    if value is None:
        if inherited is None:
            raise TypeError(
                f"{type(path).__name__} needs {what}= (or a segment that is a "
                f"{type(path).__name__})"
            )
        return inherited
    if not isinstance(value, str):
        raise TypeError(f"{what}= must be a str, not {type(value).__name__!r}")
    parts = value.split("/") if nested else [value]
    if any(part in ("", ".", "..") for part in parts) or (not nested and "/" in value):
        raise ValueError(
            f"{what}= must be a name"
            + (" or a '/' separated group path" if nested else " with no '/'")
            + f", with no empty or dot segment, not {value!r}"
        )
    return value


class HostedRepoPath(RepoPath):
    """A repository on a hosting service that has an owner (a user, an
    organization or, on GitLab, a group path) and a name, at a ref.

    `Kind(*segments, owner=, repo=, ref=None, instance=None, token=None,
    backend=None)`: the segments are the path inside the repository.
    `instance` is the host of the service, with a port if it is not the
    default one (`github.com`, `ghe.example:8443`, `[fd00::5]`); a service
    other than the public one is GitHub Enterprise or a self-hosted GitLab,
    and its API root is derived from the instance. `ref` is a branch, a tag
    or a commit; without one the service's default branch is read.
    `token` builds the path's own `RepoBackend` that sends it as a bearer
    token; `backend=` shares one backend between paths built separately, or
    plugs in a `BaseRepoBackend` of your own, and giving it with `token=` is a
    `TypeError`. A path joined from another keeps all of these, and a keyword
    moves it (`ref=""` back to the default branch). Without a backend or a
    token, the path builds an anonymous `RepoBackend`.

    The tree is read-only: `open()` in a writing mode is `NotImplementedError`.
    A token is shown by no `str()`, `repr()` or error, and is not pickled."""

    __slots__ = ()

    #: The instance a path is on when none is given.
    DEFAULT_INSTANCE: str
    #: Whether the owner may be a `/` separated group path.
    OWNER_NESTS = False

    if _ty.TYPE_CHECKING:
        _backend: BaseRepoBackend
        _place: "tuple[str, str, str, str | None]"

    def __init__(
        self,
        *segments: str | RepoPath,
        owner: str | None = None,
        repo: str | None = None,
        ref: str | None = None,
        instance: str | None = None,
        token: str | None = None,
        backend: BaseRepoBackend | None = None,
    ):
        text, donor = self._join(segments)
        place = self._place_of(owner, repo, ref, instance, donor)
        if backend is not None and token is not None:
            raise TypeError("pass either backend= or token=, not both")
        if backend is None:
            if token is not None:
                backend = RepoBackend(token=token)
            elif donor is not None:
                backend = donor._backend
            else:
                backend = RepoBackend()
        elif not callable(getattr(backend, "request", None)):
            raise TypeError("a backend is an object with a request() method")
        self._initialize(text, place, backend)

    def _place_of(self, owner, repo, ref, instance, donor) -> tuple:
        """`(instance, owner, repo, ref)` from the keywords, else from the
        path this one is joined from, else the defaults."""
        inherited = donor._place if donor is not None else (None, None, None, None)
        if instance is None:
            instance = inherited[0] or self.DEFAULT_INSTANCE
        else:
            checked_instance(instance)
        owner = _checked_name(self, "owner", owner, inherited[1], self.OWNER_NESTS)
        repo = _checked_name(self, "repo", repo, inherited[2], False)
        if ref is None:
            ref = inherited[3]
        elif isinstance(ref, str):
            ref = ref or None
        else:
            raise TypeError(f"ref= must be a str, not {type(ref).__name__!r}")
        return instance, owner, repo, ref

    def _keywords(self) -> dict:
        instance, owner, repo, ref = self._place
        return {"owner": owner, "repo": repo, "ref": ref, "instance": instance}

    def _place_key(self) -> tuple:
        # A host name is case-insensitive (RFC 3986 3.2.2); the other parts are not.
        instance, owner, repo, ref = self._place
        return instance.lower(), owner, repo, ref or ""

    @property
    def instance(self) -> str:
        """The host (and port) of the service, as given."""
        return self._place[0]

    @property
    def repo_owner(self) -> str:
        """The user, organization or group path that owns the repository."""
        return self._place[1]

    @property
    def repo(self) -> str:
        """The name of the repository."""
        return self._place[2]

    @property
    def ref(self) -> "str | None":
        """The branch, tag or commit this path reads, or None for the default
        branch of the repository."""
        return self._place[3]

    @property
    def repo_path(self) -> str:
        """The path inside the repository, with no leading or trailing `/`."""
        return "/".join(name for name in self._segments if name)

    def __repr__(self) -> str:
        shown = [
            repr(self.as_posix()),
            f"owner={self.repo_owner!r}",
            f"repo={self.repo!r}",
        ]
        if self.ref:
            shown.append(f"ref={self.ref!r}")
        if self.instance.lower() != self.DEFAULT_INSTANCE:
            shown.append(f"instance={self.instance!r}")
        backend = self._backend
        if not (isinstance(backend, RepoBackend) and backend.api_base is None):
            # A backend shows its API root and never a credential.
            shown.append(f"backend={backend!r}")
        return f"{type(self).__name__}({', '.join(shown)})"

    def __str__(self) -> str:
        return self.as_uri()

    def _uri_segments(self, names: "_ty.Sequence[str]") -> "list[str]":
        """The path segments of `as_uri()`: the owner, the repository and the
        names, with whatever separator the service's URLs use."""
        return [self.repo_owner, self.repo, *names]

    @classmethod
    def _from_uri_segments(
        cls, parts: "_ty.Sequence[str]"
    ) -> "tuple[str, str, list[str]] | None":
        """`(owner, repo, names)` from the non-empty segments of the path of a
        URI of this kind, or None when they do not hold an owner and a
        repository. The inverse of `_uri_segments()`."""
        if len(parts) < 2:
            return None
        return parts[0], parts[1], list(parts[2:])

    def as_uri(self) -> str:
        """The `<kind>://instance/owner/repo/path` URI of this path, with
        `?ref=` when it has a ref. It names no token."""
        names = [name for name in self._segments if name]
        path = "/".join(
            _urlparse.quote(segment, safe=_SAFE_SEGMENT, errors="surrogateescape")
            for segment in self._uri_segments(names)
        )
        text = f"{self._family().TYPE}://{self.instance}/{path}"
        if self.ref:
            text += "?ref=" + _urlparse.quote(
                self.ref, safe="", errors="surrogateescape"
            )
        return text

    @property
    def _api_base(self) -> str:
        return getattr(self._backend, "api_base", None) or self._default_api_base()

    @_providers.notimplemented
    def _default_api_base(self) -> str:
        """The API root of this instance when the backend names none."""

    def _api_authority(self) -> str:
        """`host[:port]` of the instance in the case a URL spells it."""
        return self.instance.lower()

    def _same_filesystem(self, other: "HostedRepoPath") -> bool:
        # The tree is what the API root, the repository and the ref name; a
        # token only decides what may be read of it.
        return (self._api_base, *self._place[1:]) == (
            other._api_base,
            *other._place[1:],
        )

    def _bad_reply(self, what: str) -> BadReplyError:
        return BadReplyError(_errno.EIO, f"Unexpected API reply: {what}", str(self))

    def _decode(self, resp, *expected: type):
        """The JSON body of `resp`, which must be one of `expected` (`dict`,
        `list`): anything else -- a captive portal's page, a gateway's JSON,
        `null`, a truncated body -- is `BadReplyError` naming this path, so
        `exists()` answers `False` and `stat()` raises."""
        try:
            data = json_body(resp, self)
        except ValueError:
            data = None
        if not isinstance(data, expected):
            kinds = " or ".join({dict: "object", list: "array"}[k] for k in expected)
            raise self._bad_reply(f"not a JSON {kinds}")
        return data

    def _size_of(self, entry: dict) -> int:
        """The `size` of an API object: a non-negative integer, `0` when it
        has none."""
        size = entry.get("size")
        if size is None:
            return 0
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            return size
        raise self._bad_reply(f"size {peer_text(repr(size), 60)}")

    def _api_quote(self, text: str, safe: str = "") -> str:
        """`text` as part of an API URL path: percent-encoded (`safe` stays
        bare), and never a `.` or `..` segment, which the client would
        resolve out of the repository's API root."""
        if any(part in (".", "..") for part in text.split("/")):
            raise ValueError(f"A dot segment cannot be part of an API URL: {self}")
        return _urlparse.quote(text, safe=safe, errors="surrogateescape")

    def _backend_cache(self) -> "dict | None":
        """The backend's memoization dict, or None for a custom
        `BaseRepoBackend` that has none (memoization is then skipped)."""
        cache = getattr(self._backend, "cache", None)
        return cache if isinstance(cache, dict) else None
