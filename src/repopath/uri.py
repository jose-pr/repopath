"""The `repo:`, `github:`, `gitlab:`, `gitea:`, `svn+https:`, `svn+http:` and
`sourceforge:` schemes of `uripath`: `RepoURIPath`, `GitHubURIPath`,
`GitLabURIPath` and `GiteaURIPath`, URI paths that delegate to a `GitHubPath`,
a `GitLabPath` or a `GiteaPath`;
`SVNURIPath` and `SourceForgeURIPath`, which delegate to an `SVNPath` and a
`SourceForgePath`.

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
import urllib.parse as _urlparse

from uripath import Source
from uripath.exceptions import URISyntaxError
from uripath.schemes import DelegatingURIPath
from uripath.source import compose_host, fold_host

from ._backend import RepoBackend
from ._gitea import GiteaPath
from ._github import GitHubPath
from ._gitlab import GitLabPath
from ._hosted import HostedRepoPath
from ._kinds import RepoPath
from ._sourceforge import SourceForgePath
from ._svn import SVNPath, _AnywherePath

__all__ = [
    "GiteaURIPath",
    "GitHubURIPath",
    "GitLabURIPath",
    "RepoURIPath",
    "SVNURIPath",
    "SourceForgeURIPath",
]


def _list_gitea_as_token_scheme() -> None:
    # uripath 0.1 keeps the schemes whose user name is a token in one table,
    # and lists only the three it names; `gitea:` joins it as it is loaded.
    try:
        from uripath.schemes import _elsewhere
    except ImportError:  # pragma: no cover
        return
    table = getattr(_elsewhere, "TOKEN_SCHEMES", None)
    if isinstance(table, frozenset) and "gitea" not in table:
        _elsewhere.TOKEN_SCHEMES = table | {"gitea"}


_list_gitea_as_token_scheme()


class RepoURIPath(DelegatingURIPath):
    """`repo:` scheme (`repo://instance/owner/repo/path?ref=REF`): read-only
    access to a repository on a hosting service, done by the path of its kind
    (a `GitHubPath`, a `GitLabPath` or a `GiteaPath`) on the backend of this
    path. The kind is detected from the host: `github.com`, `gitlab.com` (and
    `www.`) and `codeberg.org`; any other host is a `ValueError` that names
    `github://host/...`, `gitlab://host/...` and `gitea://host/...`, which pin
    the kind on any host. `ref` (branch, tag or
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
    #: The user name of these schemes is an access token: no display form shows it.
    _SECRET_USERINFO = True

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
                f"repo: can only auto-detect github.com, gitlab.com and "
                f"codeberg.org; use github:, gitlab: or gitea: for {shown!r}"
            )
        return kind

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
        `"gitlab"`, `"gitea"`)."""
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
    def repo_owner(self) -> str:
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


class GiteaURIPath(RepoURIPath):
    """`gitea:` scheme (`gitea://[TOKEN@]host[:port]/owner/repo/path?ref=REF`):
    read-only access to a repository on Gitea or Forgejo (Codeberg runs
    Forgejo) via the REST API under `/api/v1`, done by a `GiteaPath`. `host`
    defaults to `codeberg.org`; a self-hosted server is just a different host
    (and port), always at `https://{host}/api/v1`. The kind is fixed whatever
    the host. See `RepoURIPath` for the token and the ref."""

    __SCHEMES = ("gitea",)
    __slots__ = ()
    _KIND = GiteaPath


_NO_SOURCE = Source(None, None, None, None)


class _SubversionURIPath(DelegatingURIPath):
    """What `svn+https:` and `sourceforge:` share: a read-only URI path whose
    I/O is done by an `SVNPath` (or its SourceForge kind) on the backend of the
    URI path, and whose links and digests are the core path's."""

    __slots__ = ()
    _DELEGATED = ("_scandir", "_listdir", "_open")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Settles the parts, so a URI that names no usable place is refused
        # when the path is built and not at its first request.
        self.source

    def is_local(self) -> bool:
        """Whether the tree is on this machine: never. The host of the URI is
        a server and is not resolved."""
        return False

    @property
    def ref(self) -> "str | None":
        """The revision of the `?ref=` query, as text, or None for the head."""
        return self.query.to_dict(single=True).get("ref") or None

    def readlink(self):
        # The text of the link, unresolved: a result, not a destination.
        with self._delegated():
            target = self._core_path._link_text()
        source = self.source if target.startswith("/") else _NO_SOURCE
        return self._from_parsed_parts(source, target, None, None)

    def supported_checksums(self) -> "_ty.FrozenSet[str]":
        with self._delegated():
            return self._core_path.supported_checksums()

    def checksum(self, algorithm: str = "md5") -> str:
        with self._delegated():
            return self._core_path.checksum(algorithm)


class SVNURIPath(_SubversionURIPath):
    """`svn+https:` and `svn+http:` schemes
    (`svn+https://[USER:PASSWORD@]host[:port]/path/to/root/names?ref=REV`):
    read-only access to a Subversion repository served over HTTP by
    `mod_dav_svn`, done by an `SVNPath`. The URL after `svn+` is the URL of
    the path, anywhere in the repository: with `?ref=` the repository root is
    found from the server's `OPTIONS` reply. A user and password in the URI are
    sent as `Authorization: Basic`, unless `backend=` is given. The tree is
    read-only. (`svn:` is the scheme of `svnserve` and is not this one.)"""

    __SCHEMES = ("svn+https", "svn+http")
    __slots__ = ()

    def _init(self, source: Source, path, query, fragment, /, **kwargs):
        scheme = str(source.scheme or "").lower()
        # A path with no source is the text of a link, a result and not a place.
        if source.scheme and (
            scheme not in ("svn+https", "svn+http") or not source.host
        ):
            raise URISyntaxError(
                f"a {scheme}: URI names the host of the server: "
                "svn+https://host/path/to/repository"
            )
        super()._init(source, path, query, fragment, **kwargs)

    @property
    def kind(self) -> str:
        """The name of the kind of repository this path reads: `"svn"`."""
        return SVNPath.TYPE

    def _initbackend(self):
        user, password = self.source.parsed_userinfo()
        if user in (None, ""):
            return RepoBackend()
        try:
            pair = tuple(
                part.decode("utf-8") if isinstance(part, bytes) else part
                for part in (user, password or "")
            )
        except UnicodeDecodeError:
            raise URISyntaxError(
                "the user and password of the URI are not UTF-8"
            ) from None
        return RepoBackend(auth=pair)

    def _origin(self) -> str:
        source = self.source
        scheme = str(source.scheme).lower().partition("+")[2]
        host = compose_host(fold_host(source.host))
        return f"{scheme}://{host}" + (f":{source.port}" if source.port else "")

    def _make_core(self) -> SVNPath:
        names = [name for name in self.segments if name]
        if any("/" in name for name in names):
            raise URISyntaxError("a name of a svn+https: URI holds a '/'")
        try:
            return _AnywherePath(
                *names, instance=self._origin(), ref=self.ref, backend=self.backend
            )
        except ValueError as error:
            raise URISyntaxError(str(error)) from None


class SourceForgeURIPath(_SubversionURIPath):
    """`sourceforge:` scheme (`sourceforge://PROJECT/names?mount=code&ref=REV`):
    read-only access to the Subversion tool of a SourceForge project, done by a
    `SourceForgePath`. The host slot is the project; `?mount=` is the tool's
    mount point (`code` by default), `?ref=` a revision and `?instance=` the
    host of another site. The URI holds no user, password or port: the reads
    are anonymous."""

    __SCHEMES = ("sourceforge",)
    __slots__ = ()

    def _init(self, source: Source, path, query, fragment, /, **kwargs):
        if source.scheme and (not source.host or source.userinfo or source.port):
            raise URISyntaxError(
                "a sourceforge: URI is sourceforge://PROJECT/names, with no "
                "user, password or port"
            )
        super()._init(source, path, query, fragment, **kwargs)

    @property
    def kind(self) -> str:
        """The name of the kind of repository this path reads: `"sourceforge"`."""
        return SourceForgePath.TYPE

    def _initbackend(self):
        return RepoBackend()

    @property
    def project(self) -> str:
        """The project, the host slot of the URI."""
        return str(self.source.host).lower()

    @property
    def mount(self) -> str:
        """The mount point of the tool (`code` unless `?mount=` says another)."""
        return self.query.to_dict(single=True).get("mount") or "code"

    def _make_core(self) -> SourceForgePath:
        names = [name for name in self.segments if name]
        options = self.query.to_dict(single=True)
        try:
            return SourceForgePath(
                *names,
                project=self.project,
                mount=self.mount,
                ref=self.ref,
                instance=options.get("instance") or None,
                backend=self.backend,
            )
        except ValueError as error:
            raise URISyntaxError(str(error)) from None
