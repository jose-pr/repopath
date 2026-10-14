# `repopath` — public API header

Header-file-style reference for the `repopath` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is
self-contained. Development documentation lives with the source at
<https://github.com/jose-pr/repopath>.

Names are imported from `repopath` (the paths, the backends, the exceptions, the
warning and `__version__`), from `repopath.exceptions` (the exceptions and the
warning) and from `repopath.uri` (the `repo:`, `github:` and `gitlab:` schemes
of `uripath`). Every module whose name starts with `_` is private. `import
repopath` imports no third-party library and never `uripath`. The classes are
read-only clients of the REST APIs of GitHub and GitLab and speak no git
protocol: there is no clone, no `git:` URL and no commit.

Install with `pip install repopath`, which needs only `pathapi`; the
requests go through `pathapi.http`, so no HTTP library is installed. One
extra adds the URI layer; `repopath.uri` without it raises an `ImportError`
that names the extra:

| Extra | Adds | For |
| --- | --- | --- |
| `uri` | `uripath` | `repopath.uri`: the `repo:`, `github:` and `gitlab:` schemes |
| `all` | all of the above | |

`repopath.__version__` is the installed version, read from the distribution's
metadata.

## Paths (`repopath`)

```python
class RepoPath(Path):
    TYPE: str | None
    INSTANCES: tuple[str, ...]
    kind: str
    backend: BaseRepoBackend
    @staticmethod
    def kinds() -> tuple[str, ...]: ...
    def is_local(self) -> bool: ...

class GitHubPath(RepoPath):
    def __init__(
        self, *segments, owner=None, repo=None, ref=None, instance=None,
        token=None, backend=None,
    ): ...
    instance: str
    repo_owner: str
    repo: str
    ref: str | None
    repo_path: str
    def as_uri(self) -> str: ...

class GitLabPath(RepoPath):
    def __init__(
        self, *segments, owner=None, repo=None, ref=None, instance=None,
        token=None, backend=None,
    ): ...
    instance: str
    repo_owner: str
    repo: str
    ref: str | None
    repo_path: str
    def as_uri(self) -> str: ...
```

`RepoPath` is the family of a repository tree on a hosting service, and
`GitHubPath` and `GitLabPath` are its two kinds. Each is a
`pathapi.Path` (so `pathlib.Path`'s read methods, `copy()` out of the tree,
`walk()`, `glob()` and `PathSyncer` as a source work on it) and needs no URI.
`segments` are the path inside the repository, joined and normalized like a
POSIX path: `x` and `/x` name one file, empty and `.` segments go, `..` is kept
as a name.

- **Calling `RepoPath`.** `RepoPath(*segments, instance=..., type=None,
  **options)` returns a path of one kind, with `options` the keywords of that
  kind. `type` names the kind (`"github"`, `"gitlab"`); without it the kind is
  detected from `instance` (`github.com` and `gitlab.com`, with or without
  `www.`, in any case). An instance that no kind is detected for is a
  `ValueError` that asks for `type=` and lists the kinds; an unregistered `type`
  is a `ValueError` that lists the registered ones; neither is a `TypeError`;
  `type=` or `instance=` that is not a `str` is a `TypeError`. A segment that is
  already a repository path gives its kind. `type=` without `instance=` gets the
  default instance of the kind. Every kind is a subclass, so `isinstance(path,
  RepoPath)` holds. `RepoPath.kinds()` lists the registered kind names, sorted
  (`("github", "gitlab")`); `path.kind` is the name of the kind of a path.
- **Place.** `owner=` and `repo=` are required, unless a segment is a path of
  the same kind and supplies them (`path / "x"`, `path.parent` and
  `GitHubPath(path, "x")` keep the place); a keyword then moves the path. A
  value that is not a `str` is a `TypeError`; an empty one, a `.` or `..`, or an
  owner or repository holding a `/` is a `ValueError` (a `GitLabPath` owner is
  the one exception: a `/` separated group path, `"acme/team"`, names the
  project of a subgroup). `instance=` is the host of the service, with a port
  when it is not the default one: `github.com` (the default of `GitHubPath`),
  `gitlab.com` (the default of `GitLabPath`), `ghe.example:8443`,
  `[fd00::5]`. Another instance than the public one is GitHub Enterprise or a
  self-hosted GitLab, with the API at `https://<instance>/api/v3` or
  `https://<instance>/api/v4`; anything that is not a host and an optional port
  (a `/`, `@`, `?`, `#`, space, a bad port) is a `ValueError`. `ref=` is a
  branch, a tag or a commit; without one the default branch of the repository is
  read (`GitLabPath` looks it up once per backend); `ref=""` on a joined path
  goes back to the default branch.
- **Backend.** `token=` builds the path's own `RepoBackend` that sends it as a
  bearer token; every path derived from it keeps that backend. `backend=` shares
  one backend between paths built separately, or plugs in a `BaseRepoBackend` of
  your own; it must have a `request()` method (`TypeError`), and giving it with
  `token=` is a `TypeError`. Without either, the path builds an anonymous
  `RepoBackend`. Nothing connects when a path is built.
- **Identity.** Two paths are equal when they are of one class and name one
  instance (in any case), owner, repository and ref and the same names; the
  backend is not part of it. They are one tree (`_same_filesystem()`) when their
  API root, owner, repository and ref are the same, whatever token each uses.
  Paths of different kinds are never equal and cannot be joined
  (`NotImplementedError` from the constructor, `TypeError` from `/`).
- **Text.** `str(path)` and `path.as_uri()` are `github://instance/owner/repo/path`
  (`gitlab://...`), with `?ref=REF` when there is a ref; a GitLab project in a
  group, and a name `-`, get GitLab's `/-/` separator in front of the path
  (`gitlab://gitlab.com/acme/team/widgets/-/src`). `as_posix()` is the names
  only. `repr()` is `GitHubPath('src/x.py', owner='acme', repo='widgets',
  ref='v1')`, with `instance=` when it is not the default and `backend=` when it
  is not a plain `RepoBackend`. No display form and no error message shows a
  token.
- **Pickling and copying.** A path pickles its place and its names and never its
  token: the receiving process builds an anonymous backend. A backend whose class
  sets `picklable = True` travels with the path. `copy.copy()` and
  `copy.deepcopy()` keep the backend.
- **Reading.** `stat()`, `exists()`, `is_dir()`, `is_file()`, `iterdir()`,
  `open("r")`/`open("rb")`, `read_bytes()`, `read_text()`, `walk()`, `glob()`
  and `copy()` out of the tree work; `st_mtime` is `0`. Every write method
  (`mkdir`, `unlink`, `rmdir`, `rename`, `chmod`, `open("w")`, `open("r+")`, ...)
  raises `NotImplementedError`. A file is read whole into memory. A directory
  listed by `iterdir()` hands each child the stat the listing gave, once, so a
  `walk()` or `glob()` costs one request per directory.
- **Limits of git.** Git has no empty directories, so one exists only with a
  file below it. A symlink or a submodule is an ordinary file of the size the
  API reports. A name an API lists that is not one path component (`..`, `a/b`,
  an empty name, one with NUL) is left out of the listing.

`GitHubPath` reads the contents API (`GET /repos/{owner}/{repo}/contents/{path}`):

- API root `https://api.github.com` for `github.com`, else
  `https://<instance>/api/v3`. A directory is one `GET`; a listing that reaches
  the API's 1,000-entry cap is read again through the Git Trees API, which has no
  cap. File bodies are asked for with the raw media type.
- `stat()`, `exists()` and `is_file()` of a file are one `HEAD` of the contents
  URL asked for the raw media type and unencoded: the size is its
  `Content-Length`, and a `404` there is `FileNotFoundError`. A directory answers
  with a JSON type and is then listed with a `GET` (two requests; the root of the
  repository is listed directly). A `HEAD` reply without `X-GitHub-Media-Type`
  (a proxy's, a server that refuses `HEAD`), with an encoding, or without a
  plain `Content-Length` is not used: the `GET` of the contents object answers.
- A `200` whose `Content-Type` is `text/html` and that carries no
  `X-GitHub-Media-Type` header is a proxy's or a captive portal's page, not a
  file: `open()` of it is `BadReplyError`.

`GitLabPath` reads the REST API v4:

- API root `https://<instance>/api/v4`. Listings are paginated (100 per page) and
  read to the last page. A blob entry is a type-only hint (`size_known=False`),
  so `walk()` and `glob()` cost one request per directory and
  `is_dir()`/`is_file()` of a listed child ask nothing; `stat()` of a file is a
  `HEAD` of the files endpoint reading `X-Gitlab-Size` (a reply without it, or a
  refused `HEAD`, is followed by a `GET` of the file's metadata). The root of a
  project and an unknown ref are settled by the server, never by the shape of
  the path.
- The files endpoints need a ref, so the default branch is looked up
  (`GET /projects/:id`) and kept in `backend.cache` for every path that shares
  the backend.

### Failures

Every failure is the `OSError` `pathlib` raises for the same condition:

| Reply | Raised |
| --- | --- |
| 404 | `FileNotFoundError` |
| 401, 403 | `PermissionError` |
| 403 or 429 with `RateLimit-Remaining: 0` or `Retry-After`, any 429 | `RateLimitError`, a `BlockingIOError` with errno `EAGAIN`; the message says when to retry |
| another status | `OSError(EIO)` naming the status |
| a reply that is not the documented shape (a page of a proxy, `null`, an array where an object belongs, a truncated body, a size that is not a number) | `BadReplyError`, an `OSError(EIO)` naming the path; `exists()` is `False` |
| a JSON reply over `pathapi.http.MAX_REPLY_BYTES` | `OSError(EFBIG)` |
| a path that is a file where a directory is needed, or the reverse | `NotADirectoryError`, `IsADirectoryError` |
| no answer in time, a refused connection | `TimeoutError`, `ConnectionError` naming the path |

An exception of the transport is never raised across the API and is never
chained: the text of one can carry a URL or a header.

A `.` or `..` name, in the path or in the ref, cannot leave the API root of the
repository: the request is not made and the call raises `ValueError`
(`exists()` answers `False`). Owner, repository, path and ref are
percent-encoded into the API URL, and the ref travels as a query parameter.

## Backends (`repopath`)

```python
class BaseRepoBackend:
    def request(self, method, url, headers=None, params=None, **kwargs): ...

class RepoBackend(HTTPClient, BaseRepoBackend):
    token: str | None
    api_base: str | None
    cache: dict
    def __init__(
        self, token=None, session=None, api_base=None, *, transport=None,
        headers=None, timeout=(10, 60), **requests_args,
    ): ...
    def request(
        self, method, url, headers=None, params=None, *, data=None, timeout=...,
        stream=False,
    ): ...
    def close(self) -> None: ...
```

`BaseRepoBackend` is the contract: `request()` sends one request to the API and
returns a `pathapi.http.HTTPResponse`. A backend may carry `api_base` (the
API root, replacing the one a path derives from its instance) and `cache` (a
dict that memoizes the default branch); a path uses either only when the backend
has one. `RepoBackend` sends through `pathapi.http`, so everything that
module documents applies:

- `token` is sent as `Authorization: Bearer <token>`. It is never shown by
  `repr()` and not sent to another origin: a redirect to another scheme, host or
  port drops it. A request that carries `Authorization` (the token, or one in
  `headers=`) to a plain `http://` URL on a host other than loopback is still
  sent, with an `InsecureTransportWarning`.
- `transport` is any object with a `send()` method as
  `pathapi.http.HTTPTransport.send()`. Without one, the backend builds the
  transport that `PATHAPI_HTTP_TRANSPORT` names, `builtin` (standard
  library, certificates verified) by default. `session=` (a `requests.Session`)
  and `requests_args` (`verify=`, `proxies=`, ...) select the `requests`
  transport of the distribution `pathapi-requests`, and raise
  `ImportError` naming it without it; with `transport=` they are a `TypeError`.
- `headers` go with every request, under the request's own; the `User-Agent` is
  `repopath/<version>`. Every request gets `timeout=(10, 60)` seconds
  (`connect`, `read`) unless `timeout` here or in the call says otherwise;
  `None` waits without limit.
- `api_base` overrides the API root entirely (the seam a test fake, or a
  reverse-proxied self-hosted service, plugs into, since the derived root is
  always `https`).
- A `RepoBackend` is a context manager: leaving the block, or `close()`, closes
  a transport the backend built, never one it was given. It may be shared between
  threads.

## The `repo:`, `github:` and `gitlab:` schemes (`repopath.uri`)

```python
class RepoURIPath(DelegatingURIPath):
    kind: str
    instance: str
    repo_owner: str
    repo: str
    repo_path: str
    ref: str | None
    def is_local(self) -> bool: ...

class GitHubURIPath(RepoURIPath): ...

class GitLabURIPath(RepoURIPath): ...
```

Importing `repopath.uri` needs `uripath` (`pip install "repopath[uri]"`).
`uripath` loads it by itself, through its `uripath.schemes` entry points, the
first time a path of one of the schemes is used; `URIPath("github://...")`
returns a `GitHubURIPath`. Each class is a `uripath.schemes.DelegatingURIPath`
that hands every read to the `GitHubPath` or `GitLabPath` of its URI, built once
per instance on the backend of the URI path, so everything above holds for a URI
path too, and an error names the URI.

- `github://[TOKEN@]host/owner/repo/path?ref=REF` is a `GitHubURIPath`;
  `gitlab://[TOKEN@]host[:port]/owner/repo/path?ref=REF` a `GitLabURIPath`, with
  GitLab's own `/-/` ending the project path: `gitlab://host/group/subgroup/
  project/-/path`. Without the separator the first two segments are owner and
  repository; a `-` name needs it in front (`.../repo/-/-/x`), and the child
  `path / "-"` writes it. Any `host` is allowed: the kind is the scheme's.
- `repo://host/owner/repo/path?ref=REF` is a `RepoURIPath`, whose kind is
  detected from the host (`github.com`, `gitlab.com`, with or without `www.`,
  in any case). Any other host raises `URISyntaxError` (a `ValueError`) when the
  path is built, naming `github://host/...` and `gitlab://host/...`, which pin the
  kind on any host. `git:`, `git+github:` and `git+gitlab:` are not schemes of
  this package.
- `path.repo_owner`, `repo`, `repo_path`, `ref`, `instance` and `kind` are read from
  the URI; a URI that holds no owner and repository (`github://github.com/acme`)
  is a path that does not exist (`FileNotFoundError`, no request).
- The token is the user slot of the URI (`repo://TOKEN@host/...`) or its password
  slot (`github://x-access-token:TOKEN@host/...`, GitLab's `oauth2:TOKEN`), unless
  `backend=` is given. Because the user slot can be the secret, `str()`,
  `repr()`, `as_posix()` and `as_uri(sanitize=True)` of these paths, `str()` and
  `repr()` of their `Source`, and so every message of an exception, drop the
  whole userinfo; `as_uri()` still round-trips it, and so does a pickle.
  `as_posix()` is `host:path`.
- `?ref=` is carried to every child. Paths derived from one URI share the backend
  the first of them builds, so the default branch of a GitLab project is looked up
  once; `backend=` supplies one (a `RepoBackend` or a `BaseRepoBackend`).
- `is_local()` is `False`: the host is a hosting service and is never resolved.

## Exceptions (`repopath.exceptions`)

```python
class RepoPathError(PathError): ...
class RateLimitError(RepoPathError, BlockingIOError): ...
class BadReplyError(RepoPathError, OSError): ...
class InsecureTransportWarning(UserWarning): ...
```

`RepoPathError` (under `pathapi.PathError`) is the base of what this package
raises on its own at run time. `RateLimitError` and `BadReplyError` are the two
failures no builtin names, and keep the builtin a caller already catches. A
caller's bad argument is a plain `ValueError` or `TypeError`; an unsupported
operation is `NotImplementedError`. `InsecureTransportWarning` is a
`UserWarning`: a `RepoBackend` is about to send an `Authorization` header over
plain `http://` to a host that is not this machine.

## Gotchas

- Anonymous requests are rate limited by the host (60 an hour for `github.com`);
  a listing of a large tree or a recursive copy can use that up. Pass `token=`.
- A recursive `copy()` or `PathSyncer` run out of a tree makes one request per
  directory and one per file; a file is read whole into memory.
- A new kind of repository is a `RepoPath` subclass whose class body sets `TYPE`
  and `INSTANCES`; defining it registers the name and the instances, and a name or
  an instance another class holds is a `ValueError` unless the class statement
  passes `replace=True`. A kind supplies its constructor (calling
  `self._initialize(text, place, backend)`), `_keywords()` (the constructor
  keywords that name the place), `as_uri()` and the read hooks (`stat()`,
  `_scandir()`, `_open()`); `RepoPath(type="name", **keywords)` then returns it.
- `GitHubPath(...)` and `RepoPath(..., instance="github.com")` are the same
  class; the second spelling is for a name the caller reads from data.
- The settings of the transport (`PATHAPI_HTTP_TRANSPORT`, proxies, the
  limit `pathapi.http.MAX_REPLY_BYTES` on a JSON reply) belong to
  `pathapi.http`: assigning the limit changes it for every package that
  sends through it.
