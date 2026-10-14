"""The `repo:`, `github:` and `gitlab:` schemes of `uripath`: `RepoURIPath`,
`GitHubURIPath` and `GitLabURIPath`, URI paths that delegate to a `GitHubPath`
or a `GitLabPath`.

Importing this module needs `uripath`: `pip install "repopath[uri]"`. `uripath`
loads it by itself, through the `uripath.schemes` entry points, the first time
a path of one of the schemes is used.
"""

from __future__ import annotations

try:
    import uripath as _uripath  # noqa: F401
except ImportError as _error:
    raise ImportError(
        'repopath.uri needs the uripath distribution: pip install "repopath[uri]"',
        name="uripath",
    ) from _error

import contextlib as _contextlib
import errno as _errno
import typing as _ty

from uripath import Source
from uripath.exceptions import URISyntaxError
from uripath.schemes import DelegatingURIPath
from uripath.source import compose_host, fold_host

from ._backend import RepoBackend
from ._github import GitHubPath
from ._gitlab import GitLabPath
from ._hosted import HostedRepoPath
from ._kinds import RepoPath

__all__ = ["GitHubURIPath", "GitLabURIPath", "RepoURIPath"]


class RepoURIPath(DelegatingURIPath):
    """`repo:` scheme (`repo://instance/owner/repo/path?ref=REF`): read-only
    access to a repository on a hosting service, done by the path of its kind
    (a `GitHubPath` or a `GitLabPath`) on the backend of this path. The kind is
    detected from the host: `github.com` and `gitlab.com` (and `www.`); any
    other host is a `ValueError` that names `github://host/...` and
    `gitlab://host/...`, which pin the kind on any host. `ref` (branch, tag or
    commit) is optional and carried in the `?ref=` query; without one the
    service's default branch is read.

    Auth: a bearer token given as the user slot of the URI,
    `repo://TOKEN@host/...`, or as its password slot,
    `repo://x-access-token:TOKEN@host/...` (the `oauth2:TOKEN` of GitLab
    too); the `backend` keyword wins if both are given. Because the user slot
    can be the secret, `str()`, `repr()`, `as_posix()` and `as_uri(sanitize=True)`,
    and so every message of an exception, drop the WHOLE userinfo for these
    schemes; `as_uri()` still round-trips it. The tree is read-only."""

    __SCHEMES = ("repo",)
    __slots__ = ()
    _DELEGATED = ("_scandir", "_listdir", "_open")

    #: The kind this class pins, or None to detect it from the host.
    _KIND: "type[HostedRepoPath] | None" = None

    if _ty.TYPE_CHECKING:
        backend: RepoBackend

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Settles the parts, so a host that no kind is detected for is refused
        # when the path is built and not at its first request.
        self.source

    def _init(self, source: Source, path, query, fragment, /, **kwargs):
        self._kind_of(source, path, query, fragment)
        super()._init(source, path, query, fragment, **kwargs)

    @classmethod
    def _kind_of(
        cls, source: Source, path: str = "", query="", fragment=""
    ) -> "type[HostedRepoPath]":
        """The kind of path that serves a URI at `source`: the one this class
        pins, else the one its host is detected for."""
        if cls._KIND is not None:
            return cls._KIND
        host = str(source.host or "").lower()
        try:
            kind = RepoPath._kind_for((), None, host or None)
        except (TypeError, ValueError):
            kind = None
        if kind is None or not issubclass(kind, HostedRepoPath):
            # Redacted like the paths: the userinfo can be a bare token.
            shown = cls._format_parsed_parts(
                source, path, query, fragment, sanitize=True
            )
            raise URISyntaxError(
                f"repo: can only auto-detect github.com and gitlab.com; "
                f"use github: or gitlab: for {shown!r}"
            )
        return kind

    @classmethod
    def _format_parsed_parts(
        cls, source: Source, path: str, query: str, fragment: str, /, sanitize=True
    ) -> str:
        # A bare `TOKEN@host` puts the secret in the user slot, which the
        # base sanitizing keeps -- drop the whole userinfo instead.
        if sanitize and source.userinfo:
            source = source._replace(userinfo=None)
        return super()._format_parsed_parts(
            source, path, query, fragment, sanitize=sanitize
        )

    def as_posix(self) -> str:
        # `user@host:path` would show the token when it is the user slot.
        host = self.source.host
        return f"{host}:{self.path}" if host else self.path

    def _initbackend(self):
        user, password = self.source.parsed_userinfo()
        return RepoBackend(token=password or user or None)

    def is_local(self) -> bool:
        """Whether the tree is on this machine: never. The host of the URI is
        a hosting service and is not resolved."""
        return False

    @property
    def kind(self) -> str:
        """The name of the kind of repository this path reads (`"github"`,
        `"gitlab"`)."""
        return self._kind_of(self.source).TYPE

    @property
    def instance(self) -> str:
        """The host (and port) of the service, in the case a URL spells it."""
        text = self._instance()
        return text or self._kind_of(self.source).DEFAULT_INSTANCE

    def _instance(self) -> "str | None":
        """`host[:port]` of the URI with an IPv6 literal bracketed, or None
        when the URI names no host (the kind's default instance then)."""
        source = self.source
        if not source.host:
            return None
        text = compose_host(fold_host(source.host))
        return f"{text}:{source.port}" if source.port else text

    def _split(self) -> "tuple[str, str, list[str]]":
        """`(owner, repo, names)` of the path of this URI, the kind's way:
        `("", "", [])` and the like when it has fewer parts than a
        repository."""
        parts = [part for part in self.segments if part]
        split = self._kind_of(self.source)._from_uri_segments(parts)
        if split is not None:
            return split
        return (parts[0] if parts else ""), "", []

    @property
    def owner(self) -> str:
        """The owner of the repository: the first segment (the group path of a
        GitLab project in a subgroup, ended by `/-/`)."""
        return self._split()[0]

    @property
    def repo(self) -> str:
        """The name of the repository."""
        return self._split()[1]

    @property
    def repo_path(self) -> str:
        """The path inside the repository, with no leading or trailing `/`."""
        return "/".join(self._split()[2])

    @property
    def ref(self) -> "str | None":
        """The branch, tag or commit of the `?ref=` query, or None."""
        return self.query.to_dict(single=True).get("ref") or None

    def _make_core(self) -> HostedRepoPath:
        kind = self._kind_of(self.source)
        owner, repo, names = self._split()
        if not (owner and repo):
            raise FileNotFoundError(
                _errno.ENOENT,
                f"a {self.source.scheme}: path needs an owner and a repository",
                str(self),
            )
        return kind(
            *names,
            owner=owner,
            repo=repo,
            ref=self.ref,
            instance=self._instance(),
            backend=self.backend,
        )

    @_contextlib.contextmanager
    def _delegated(self, *extra: "tuple[str, str]"):
        # The core path names itself by its own scheme (`github:` for a path
        # of `repo:`), and in the middle of a message as well as alone: the
        # name of this path replaces it wherever it is.
        try:
            with super()._delegated(*extra):
                yield
        except OSError as error:
            core_path = self._core
            if core_path is not None and str(core_path) != str(self):
                core, mine = str(core_path), str(self)
                error.args = tuple(
                    arg.replace(core, mine) if isinstance(arg, str) else arg
                    for arg in error.args
                )
                # `str(OSError)` reads these, not `args`.
                for attribute in ("strerror", "filename", "filename2"):
                    value = getattr(error, attribute)
                    if isinstance(value, str) and core in value:
                        setattr(error, attribute, value.replace(core, mine))
            raise


class GitHubURIPath(RepoURIPath):
    """`github:` scheme (`github://[TOKEN@]host/owner/repo/path?ref=REF`):
    read-only access to a GitHub repository tree via the REST contents API,
    done by a `GitHubPath`. `host` defaults to `github.com` (API at
    `api.github.com`); any other host is GitHub Enterprise (API at
    `https://{host}/api/v3`). The kind is fixed whatever the host. See
    `RepoURIPath` for the token and the ref."""

    __SCHEMES = ("github",)
    __slots__ = ()
    _KIND = GitHubPath


class GitLabURIPath(RepoURIPath):
    """`gitlab:` scheme (`gitlab://[TOKEN@]host[:port]/owner/repo/path?ref=REF`):
    read-only access to a GitLab project's repository tree via the REST API
    v4, done by a `GitLabPath`. A project in a subgroup is addressed with
    GitLab's own `/-/` separator, as in its web URLs:
    `gitlab://host/group/subgroup/project/-/path/in/repo`; without the
    separator the first two segments are owner and repository. `host` defaults
    to `gitlab.com`; a self-hosted instance is just a different host (and
    port), always at `https://{host}/api/v4`. See `RepoURIPath` for the token
    and the ref."""

    __SCHEMES = ("gitlab",)
    __slots__ = ()
    _KIND = GitLabPath

    def _separator_index(self) -> "int | None":
        # GitLab's `/-/` ends the project path; it needs at least
        # `owner/repo` in front of it.
        try:
            return self.segments.index("-", 3)
        except ValueError:
            return None

    def _make_child_relpath(self, name: str, **kwargs):
        parent = self
        if name == "-" and self._separator_index() is None and len(self.segments) > 2:
            # A directory literally named "-" would read as the separator:
            # spell the parent with `/-/` so the child stays that directory.
            segments = self.segments[:3] + ("-",) + self.segments[3:]
            parent = self._from_parsed_parts(
                self.source, "/".join(segments), self.query, self.fragment
            )
        return super(GitLabURIPath, parent)._make_child_relpath(name, **kwargs)
