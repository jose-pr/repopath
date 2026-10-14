from __future__ import annotations

import errno as _errno
import itertools as _itertools
import urllib.parse as _urlparse

from pathapi import FileStat
from pathapi import providers as _providers
from pathapi.http import drop_body, raise_for_status, server_size

from ._backend import translate_repo_errors
from ._hosted import HostedRepoPath


class GitLabPath(HostedRepoPath):
    """A path in a GitLab project's repository, read-only, through the REST
    API v4 (the project is the URL-encoded `owner/repo`).

    `GitLabPath("src/mod.py", owner="acme", repo="widgets", ref="v1.2")`; see
    `HostedRepoPath` for the keywords. A project in a subgroup has the group
    path as its owner: `owner="acme/team", repo="widgets"`. The default
    instance is `gitlab.com`; a self-hosted one is just another instance (and
    port), always at `https://{instance}/api/v4`. The tree-listing endpoint
    doesn't carry file size, so `_scandir()` only pre-seeds a stat hint for
    `tree` (directory) entries (`size=0` is truthful for a directory, not a
    placeholder) -- `blob` (file) entries get a real `stat()` lazily instead of
    guessing a size that could poison a caller trusting the hint. No mtime.
    Needs no third-party library."""

    TYPE = "gitlab"
    INSTANCES = ("gitlab.com", "www.gitlab.com")
    DEFAULT_INSTANCE = "gitlab.com"
    OWNER_NESTS = True
    __slots__ = ()

    def _default_api_base(self) -> str:
        return f"https://{self._api_authority()}/api/v4"

    def _uri_segments(self, names):
        # GitLab's own `/-/` ends the project path in its web URLs. It is
        # needed when the project is in a group, and when a name is `-`, which
        # would otherwise read as the separator.
        owner = self.repo_owner.split("/")
        if len(owner) > 1 or "-" in names:
            return [*owner, self.repo, "-", *names]
        return [*owner, self.repo, *names]

    @classmethod
    def _from_uri_segments(cls, parts):
        # The first `-` after an owner and a repository ends the project path;
        # without one the first two segments are the owner and the repository.
        try:
            index = list(parts).index("-", 2)
        except ValueError:
            return super()._from_uri_segments(parts)
        return "/".join(parts[: index - 1]), parts[index - 1], list(parts[index + 1 :])

    @property
    def _project_id(self) -> str:
        return _urlparse.quote(f"{self.repo_owner}/{self.repo}", safe="")

    def _params(self, **extra) -> dict:
        ref = self.ref
        if ref:
            extra["ref"] = ref
        return extra

    def _file_url(self, path: str, suffix: str = "") -> str:
        encoded = self._api_quote(path)
        return f"{self._api_base}/projects/{self._project_id}/repository/files/{encoded}{suffix}"

    def _tree_url(self) -> str:
        return f"{self._api_base}/projects/{self._project_id}/repository/tree"

    def _request(self, method, url, **kwargs):
        """A streamed request that succeeded: the caller decodes or reads the
        body, which closes the response."""
        with translate_repo_errors(self):
            resp = self._backend.request(method, url, stream=True, **kwargs)
            try:
                raise_for_status(resp)
            except BaseException:
                drop_body(resp)
                raise
        return resp

    def _resolved_ref(self) -> str:
        # Unlike the tree endpoint (ref optional), GitLab's repository/files endpoints
        # (metadata AND raw) answer 400 "ref is missing" without a `ref`: resolve and
        # cache the project's default branch once per backend.
        ref = self.ref
        if ref:
            return ref
        # A custom `BaseRepoBackend` may have no cache: no memoization then.
        cache = self._backend_cache()
        key = ("gitlab_default_branch", self._api_base, self._project_id)
        if cache is not None and key in cache:
            return cache[key]
        resp = self._request("GET", f"{self._api_base}/projects/{self._project_id}")
        branch = self._decode(resp, dict).get("default_branch")
        if not (isinstance(branch, str) and branch):
            raise self._bad_reply("a project without a default_branch")
        if cache is not None:
            cache[key] = branch
        return branch

    def _file_size(self, path: str) -> "int | None":
        """The size of the file at `path`, or `None` when there is no such
        file. `HEAD` on the files endpoint answers with the size in
        `X-Gitlab-Size` and no body; a reply without it (or a server that
        refuses `HEAD`) is followed by a `GET` of the file's metadata."""
        url = self._file_url(path)
        params = {"ref": self._resolved_ref()}
        try:
            with translate_repo_errors(self):
                resp = self._backend.request("HEAD", url, params=params)
                if resp.status not in (405, 501):
                    raise_for_status(resp)
            size = server_size(resp.headers.get("X-Gitlab-Size"))
            if size is not None and resp.status < 300:
                return size
            meta = self._decode(self._request("GET", url, params=params), dict)
        except FileNotFoundError:
            return None
        if "size" not in meta:
            raise self._bad_reply("file metadata without a size")
        return self._size_of(meta)

    def _tree_entries(self, path: str):
        # The tree endpoint is paginated (at most 100 per page): follow
        # `X-Next-Page` until the last page.
        page = 1
        while True:
            resp = self._request(
                "GET",
                self._tree_url(),
                params=self._params(path=path, per_page=100, page=page),
            )
            for item in self._decode(resp, list):
                if not (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("type"), str)
                ):
                    raise self._bad_reply("a tree entry without a name and a type")
                yield item
            next_page = resp.headers.get("X-Next-Page", "")
            if not next_page.isdigit() or int(next_page) <= page:
                return
            page = int(next_page)

    def stat(self, *, follow_symlinks=True):
        hint = self._pop_stat_hint()
        if hint is not None:
            return hint
        path = self.repo_path
        if not path:
            # Ask the server: a missing project, owner-only URI or unknown
            # ref must not read as an existing directory.
            self._decode(
                self._request("GET", self._tree_url(), params=self._params(per_page=1)),
                list,
            )
            return FileStat(is_dir=True)
        size = self._file_size(path)
        if size is not None:
            return FileStat(st_size=size, is_dir=False)
        # Not a file at this exact path. Git has no empty directories, so a
        # path whose tree listing has any entry is a directory; an empty
        # listing (or a 404) means it does not exist.
        try:
            resp = self._request(
                "GET", self._tree_url(), params=self._params(path=path, per_page=1)
            )
        except FileNotFoundError:
            raise FileNotFoundError(_errno.ENOENT, "No such file", str(self)) from None
        if self._decode(resp, list):
            return FileStat(is_dir=True)
        raise FileNotFoundError(_errno.ENOENT, "No such file", str(self))

    def _scandir(self):
        entries = self._tree_entries(self.repo_path)
        try:
            first = next(entries, None)
        except FileNotFoundError:
            # The tree endpoint 404s for a blob path as for a missing one.
            if self.repo_path and self._file_size(self.repo_path) is not None:
                raise NotADirectoryError(
                    _errno.ENOTDIR, "Not a directory", str(self)
                ) from None
            raise
        if first is None:
            # Git has no empty directories, so an empty listing of a
            # non-root path is a file or a missing path (a server may answer
            # either with an empty 200): pathlib's iterdir() raises for both.
            if self.repo_path:
                self._hint = None
                if not self.stat().is_dir():
                    raise NotADirectoryError(
                        _errno.ENOTDIR, "Not a directory", str(self)
                    )
            return
        for entry in _itertools.chain((first,), entries):
            # The API's names are not trusted to be one path component.
            if not _providers.is_safe_child_name(entry["name"]):
                continue
            # The tree listing carries no size: a blob is known to be a file
            # (and a tree a directory), which is all `walk()` and `glob()` ask.
            # Any other type (a submodule) is left to a `stat()`.
            if entry["type"] == "tree":
                hint = FileStat(is_dir=True)
            elif entry["type"] == "blob":
                hint = FileStat(is_dir=False, size_known=False)
            else:
                hint = None
            yield entry["name"], hint

    def _open(self, mode="r", buffering=-1):
        self._check_read_mode(mode)
        path = self.repo_path
        if not path:
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        try:
            resp = self._request(
                "GET",
                self._file_url(path, "/raw"),
                params={"ref": self._resolved_ref()},
            )
        except FileNotFoundError:
            # The files endpoint 404s for a tree path as for a missing one.
            try:
                is_dir = self.stat().is_dir()
            except FileNotFoundError:
                is_dir = False
            if is_dir:
                raise IsADirectoryError(
                    _errno.EISDIR, "Is a directory", str(self)
                ) from None
            raise
        try:
            return self._reader(resp.read())
        finally:
            resp.close()
