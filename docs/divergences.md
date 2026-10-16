# Divergences from `pathlib`

`repopath` targets `pathlib.Path` parity: same method names, signatures, semantics and
exception types wherever a `pathlib.Path` equivalent exists. Extensions (extra optional
kwargs, new methods like `rm()` and `copy()`) are allowed. Any *behavioral* divergence from
`pathlib` on a method that exists in both must be listed here -- no silent divergence.

The divergences below apply to `GitHubPath`, `GitLabPath`, `GiteaPath`, `SVNPath` and the URI paths that delegate
to them. The ones of the generic `Path` operations that every path inherits (`touch()` on a
non-local backend, `rm()` and its policies, `chown()`, `Path.replace()`, a `with` block that
raised while writing) are on the documentation site of `pathapi`,
<https://jose-pr.github.io/pathapi/divergences/>.

| Method | `pathlib` | `GitHubPath`, `GitLabPath`, `GiteaPath` | Why |
| --- | --- | --- | --- |
| Write methods (`mkdir`, `unlink`, `rmdir`, `rename`, `chmod`, `open("w")`, `open("r+")`) | pathlib supports all of these | All raise `NotImplementedError` -- the tree is read-only | Writing to a git repository goes through a commits API (create a commit, not a direct file write) with no filesystem-shaped equivalent: it must specify a commit message and author, and typically targets a new branch. |
| A relative path, and the empty path | relative to the current working directory | names are read from the root of the repository: `x` and `/x` are one file and the empty path is the root | A repository has no working directory, and two spellings of one name must be one file (`==`, `copy()` and `move()` ask). |
| `==` and `hash()` | equal when the path text is | equal when the class, the instance (in any case), the owner, the repository, the ref and the names are; the backend is not part of it | Two refs of one repository are two trees. |
| `str(path)` | the file path | the URI: `github://instance/owner/repo/path?ref=REF` (and `gitlab://`, `gitea://`), which names no token | The text has to say which repository the path is in. `as_posix()` is the names only. |
| `stat().st_mtime` | the file system's modification time | always `0` | Neither REST API returns a last-modified time from the call that gives the type and size; it takes a separate, expensive commit-history lookup per path. |
| `is_symlink()`, `readlink()` of a `symlink` or `submodule` entry | pathlib exposes links | surfaced as a plain file, no distinction | There is no portable meaning for a submodule (a pointer to another repository, not file content) or a symlink (git stores the link target as the blob content) without extra API calls. |
| An empty directory (`empty_dir/`) | pathlib directories can be empty | a directory exists only with a file below it | Git has no empty-directory concept: neither API can return a tree entry for a path with no file under it. This is the repository's property, not a limit of the library. |
| `stat().st_size` of a GitLab file in a listing | a stat is complete | a listed file is known to be a file and its size is not known (`size_known` is `False`): `is_dir()`/`is_file()` answer from the listing, and `stat()` asks the server for the size | GitLab's tree listing carries no size; guessing one could poison a caller that trusts it. |
| Names `..` and `.` | `PurePath` keeps `..` lexically | `.` segments are dropped and `..` is kept, but a `..` in the path or the ref raises `ValueError` before any request | A `..` in an API URL would be resolved by the client and leave the repository's API root. |
| A name the service lists that is not one path component (`..`, `a/b`, an empty name, one with NUL) | the file system never returns one | left out of `iterdir()` and `_scandir()` | A listing is another party's input: such a name must never become a child path. |
| A `Path` of another kind joined to a path | joining paths of one flavour is allowed | `NotImplementedError` from the constructor, `TypeError` from `/` | The owner and repository of a GitHub path mean nothing to a GitLab one. |
| The `repo:`, `github:`, `gitlab:` and `gitea:` paths: `str()`, `repr()`, `as_posix()`, `as_uri(sanitize=True)` | other schemes redact the password only | the **whole** userinfo is redacted; `as_posix()` is `host:path`; `as_uri()` (unsanitized) still returns it | The token is commonly the bare userinfo (`TOKEN@host`), the one part other schemes keep, so it reached logs and tracebacks. |
| `gitlab://host/group/sub/project/-/path` | N/A | a `-` segment after the owner and the repository ends the project path, so a project in a subgroup is addressable; a directory called `-` is written with the separator in front | GitLab's own URL convention; without it only `owner/repo` projects were addressable. |
| `GiteaPath`: a symlink, a submodule, a file in Git LFS | a link is a link; a file is its content | a symlink is a file that reads as its target text, with that length as its size (`is_symlink()` is `False`); a submodule is a directory with no entries that cannot be opened; an LFS file reads as the pointer Git holds and `stat()` states the pointer's size | The Git Trees API states a link as a blob and a submodule as a commit, and the `raw` route answers the stored bytes: the size and the bytes stay one thing. |
| `SVNPath`: `str(path)` | the file path | `svn+https://host/path/to/root/names?ref=REV`, which names no credential | The text has to say which repository and revision the path is in. |
| `SVNPath`: a path in the middle of a link | the operating system resolves every component | only the last name is followed (at most 40 links); a link in the middle of a path does not resolve, and a target that leaves the tree does not exist | Subversion stores a link as a file whose text is `link TARGET`; following one is a request, so the walk stays bounded and inside the tree. |
| `SVNPath`: `stat().st_mtime`, mode | the file system's | the time of the revision that last changed the entry; no permission bits (`svn:executable` is not reported) | It is what the server states with the stat in the one request. |
| `SVNPath`: `svn:externals` | N/A | not part of the tree: not listed, and their paths do not exist | An external names another location, possibly of another server, and nothing is followed to it. |
| `SourceForgePath`: the root of the tree | N/A | the URL SourceForge serves the tool at (the `trunk` of the project), not the root of the repository | It is the checkout URL the site names; `SVNPath` on the repository's URL reads the rest. |
