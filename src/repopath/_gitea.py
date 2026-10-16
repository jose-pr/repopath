from __future__ import annotations

import errno as _errno

from pathapi import FileStat
from pathapi import providers as _providers
from pathapi.http import drop_body, raise_for_status

from ._backend import translate_repo_errors
from ._hosted import HostedRepoPath

#: The most entries one page of the Git Trees API holds.
_PAGE = 1000


class GiteaPath(HostedRepoPath):
    """A path in a repository on Gitea or Forgejo (Codeberg runs Forgejo),
    read-only, through the REST API under `/api/v1`.

    `GiteaPath("src/mod.py", owner="acme", repo="widgets", ref="v1.2",
    instance="git.example")`; see `HostedRepoPath` for the keywords. The
    default instance is `codeberg.org`; any other is a self-hosted one, always
    at `https://{instance}/api/v1`. Directories are read through the Git Trees
    API in pages of 1,000 (the contents API answers a large directory whole and
    slowly), file bodies through the `raw` route. A symbolic link is a file
    whose bytes are its target; a submodule is an empty directory; a file kept
    in Git LFS reads as the pointer Git holds, which is the size `stat()`
    states. No mtime. A private repository and a missing one both answer 404.
    Needs no third-party library."""

    TYPE = "gitea"
    INSTANCES = ("codeberg.org",)
    ALIASES = ("forgejo",)
    DEFAULT_INSTANCE = "codeberg.org"
    __slots__ = ()

    def _default_api_base(self) -> str:
        return f"https://{self._api_authority()}/api/v1"

    @property
    def _repo_url(self) -> str:
        owner, repo = self._api_quote(self.repo_owner), self._api_quote(self.repo)
        return f"{self._api_base}/repos/{owner}/{repo}"

    def _request(self, url: str, params=None):
        """A streamed `GET` that succeeded: the caller decodes or reads the
        body, which closes the response."""
        with translate_repo_errors(self):
            resp = self._backend.request("GET", url, params=params, stream=True)
            try:
                raise_for_status(resp)
            except BaseException:
                drop_body(resp)
                raise
        return resp

    def _params(self) -> dict:
        ref = self.ref
        return {"ref": ref} if ref else {}

    def _default_branch(self) -> str:
        cache = self._backend_cache()
        key = ("gitea_default_branch", self._repo_url)
        if cache is not None and key in cache:
            return cache[key]
        data = self._decode(self._request(self._repo_url), dict)
        branch = data.get("default_branch")
        if not (isinstance(branch, str) and branch):
            raise self._bad_reply("a repository without a default_branch")
        if cache is not None:
            cache[key] = branch
        return branch

    def _tree_page(
        self, treeish: str, page: int, per_page: int = _PAGE
    ) -> "tuple[list, int | None]":
        """One page of the tree `treeish` (a tree id, or a ref for the root)
        and the total number of entries, or None when the reply gives none."""
        url = f"{self._repo_url}/git/trees/{self._api_quote(treeish)}"
        # A ref that is not there, and an empty repository, are 400 here.
        with translate_repo_errors(self):
            resp = self._backend.request(
                "GET",
                url,
                params={"per_page": per_page, "page": page},
                stream=True,
            )
            try:
                if resp.status == 400:
                    raise FileNotFoundError(_errno.ENOENT, "No such file", str(self))
                raise_for_status(resp)
            except BaseException:
                drop_body(resp)
                raise
        data = self._decode(resp, dict)
        tree = data.get("tree")
        if not isinstance(tree, list):
            raise self._bad_reply("a tree without a tree list")
        total = data.get("total_count")
        if not (isinstance(total, int) and not isinstance(total, bool) and total >= 0):
            total = None
        return tree, total

    def _tree_entries(self, treeish: str):
        """The entries of a tree, page after page as the caller iterates."""
        seen = 0
        page = 1
        while True:
            tree, total = self._tree_page(treeish, page)
            for item in tree:
                if not (
                    isinstance(item, dict)
                    and isinstance(item.get("path"), str)
                    and isinstance(item.get("type"), str)
                ):
                    raise self._bad_reply("a tree entry without a path and a type")
                self._size_of(item)
                yield item
            seen += len(tree)
            if not tree or (total is not None and seen >= total):
                return
            page += 1

    def _root(self) -> str:
        return self.ref or self._default_branch()

    def _locate(self, names: "list[str]") -> dict:
        """The tree entry for the path of `names` (not empty): the tree of
        each name but the last is found by walking from the root."""
        treeish = self._root()
        for index, name in enumerate(names):
            for entry in self._tree_entries(treeish):
                if entry["path"] == name:
                    break
            else:
                raise FileNotFoundError(_errno.ENOENT, "No such file", str(self))
            if index == len(names) - 1:
                return entry
            if entry["type"] != "tree":
                # Below a file, a link or a submodule there is nothing.
                raise FileNotFoundError(_errno.ENOENT, "No such file", str(self))
            sha = entry.get("sha")
            if not isinstance(sha, str):
                raise self._bad_reply("a tree entry without a sha")
            treeish = sha
        raise AssertionError("no names")  # pragma: no cover

    @staticmethod
    def _stat_of(entry: dict, size: int) -> FileStat:
        # A tree and a submodule (`commit`) are directories.
        if entry["type"] == "blob":
            return FileStat(st_size=size, is_dir=False)
        return FileStat(st_size=0, is_dir=True)

    def stat(self, *, follow_symlinks=True):
        hint = self._pop_stat_hint()
        if hint is not None:
            return hint
        names = [name for name in self._segments if name]
        if not names:
            # Ask the server: a missing repository or ref must not read as an
            # existing directory.
            self._tree_page(self._root(), 1, 1)
            return FileStat(is_dir=True)
        entry = self._locate(names)
        return self._stat_of(entry, self._size_of(entry))

    def _scandir(self):
        names = [name for name in self._segments if name]
        if not names:
            treeish = self._root()
        else:
            entry = self._locate(names)
            if entry["type"] == "blob":
                raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
            if entry["type"] != "tree":
                # A submodule: the repository it names is not here.
                return
            treeish = entry.get("sha")
            if not isinstance(treeish, str):
                raise self._bad_reply("a tree entry without a sha")
        for entry in self._tree_entries(treeish):
            # The API's names are not trusted to be one path component.
            if not _providers.is_safe_child_name(entry["path"]):
                continue
            yield entry["path"], self._stat_of(entry, self._size_of(entry))

    def _open(self, mode="r", buffering=-1):
        self._check_read_mode(mode)
        path = self.repo_path
        if not path:
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        try:
            resp = self._request(
                f"{self._repo_url}/raw/{self._api_quote(path, '/')}", self._params()
            )
        except FileNotFoundError:
            # The raw route 404s for a directory and a submodule as for a
            # missing path.
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
