# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-14

The first release of `repopath`: the `github:`, `gitlab:` and `git:` schemes of
`pathlib-next` 0.9 (`pathlib_next.uri.schemes`) as a distribution of their own, and as
paths that need no URI. It needs Python 3.9 or newer and `pathapi>=0.1.0,<0.2`,
and nothing else; its one extra, `uri`, adds `pathapi-uri>=0.1.0,<0.2` and with it the
schemes. Under *Changed*, the migration from `pathlib-next` comes in this order: the
table of names, the table of URIs, then every change of behaviour with what to do about
it.

It is built on `pathapi`, which was published as `pathlib-next` up to 0.10.0 (import
name `pathlib_next`); the old names below are the ones before the move.

### Added

- `RepoPath`, `GitHubPath` and `GitLabPath`: read-only `pathapi.Path` classes for a
  repository tree on GitHub (and GitHub Enterprise) or GitLab (and self-hosted GitLab),
  over the services' REST APIs, with no URI and no library beyond `pathapi`.
  `GitHubPath("src/x.py", owner="acme", repo="widgets", ref="v1.2", instance="github.com",
  token=None, backend=None)`; `GitLabPath` takes the same keywords and an owner that may
  be a group path (`"acme/team"`).
- `repo_owner`, the property of a path and of a URI path that names the account or group
  that owns the repository. It is not called `owner`, the name `pathlib.Path.owner()` has for
  the user that owns a file.
- `RepoPath(*segments, instance=..., type=None, **options)` returns a `GitHubPath` or a
  `GitLabPath`: `type` names the kind (`"github"`, `"gitlab"`), and without it the kind
  is detected from `instance` (`github.com` and `gitlab.com`, in any case, with or
  without `www.`). Any other instance without `type` is a `ValueError` that lists the
  choices. `RepoPath.kinds()` lists the registered kinds, and a kind of your own is a
  subclass whose class body sets `TYPE` and `INSTANCES`.
- `repopath.uri`, for `uripath`: `RepoURIPath` (the `repo:` scheme, with the kind
  detected from the host), `GitHubURIPath` (`github:`) and `GitLabURIPath` (`gitlab:`),
  which delegate to the paths above. The entry points are declared whether or not
  `uripath` is installed, and `import repopath` never imports it. Importing
  `repopath.uri` without `uripath` raises an `ImportError` that names
  `pip install "repopath[uri]"`.
- `repopath.exceptions`: `RepoPathError`, the base of what this package raises on its
  own, with `RateLimitError` (also a `BlockingIOError`, errno `EAGAIN`) and
  `BadReplyError` (also an `OSError`, errno `EIO`); `InsecureTransportWarning` is there
  too.
- `RepoBackend` and `BaseRepoBackend` are in `repopath`. `RepoBackend` is a context
  manager and sends through `pathapi.http`.

### Changed

- The names. `pathlib_next.uri.schemes` named the classes of the three schemes with a
  `Path` suffix:

  | Was | Is now |
  | --- | --- |
  | `schemes.github.GitHubPath` (`github:`) | `repopath.uri.GitHubURIPath`; the path that needs no URI is `repopath.GitHubPath` |
  | `schemes.gitlab.GitLabPath` (`gitlab:`) | `repopath.uri.GitLabURIPath`; without a URI, `repopath.GitLabPath` |
  | `schemes.git.GitPath` (`git:`) | `repopath.uri.RepoURIPath` (`repo:`); without a URI, `repopath.RepoPath` |
  | `schemes.git.GitHubGitPath`, `GitLabGitPath` (`git+github:`, `git+gitlab:`) | `repopath.uri.GitHubURIPath`, `GitLabURIPath` (`github:`, `gitlab:`) |
  | `schemes.github.RepoBackend`; `schemes._gitrepo.BaseRepoBackend` | `repopath.RepoBackend`, `BaseRepoBackend` |
  | `schemes.github.InsecureTransportWarning` (also in `schemes.gitlab`) | `repopath.InsecureTransportWarning` |
  | `pathlib-next[uri]`, `pathlib-next[http]` | `repopath[uri]` |

- The URIs. `git:`, `git+github:` and `git+gitlab:` do not exist any more, and `uripath`
  treats a URI of one of them as a path of an unknown scheme. What to do: rewrite the
  URIs.

  | Was | Is now |
  | --- | --- |
  | `git://github.com/o/r/f`, `git://gitlab.com/o/r/f` | `repo://github.com/o/r/f`, `repo://gitlab.com/o/r/f` (the kind is detected from the host) |
  | `git+github://host/o/r/f` | `github://host/o/r/f` |
  | `git+gitlab://host/o/r/f` | `gitlab://host/o/r/f` |
  | `github://host/o/r/f`, `gitlab://host/o/r/f` | the same text |

  `repo://` with a host that is not `github.com` or `gitlab.com` raises `URISyntaxError`
  (a `ValueError`), as `git://` did, and names `github://host/...` and
  `gitlab://host/...`.
- A path that has an owner and a repository is built from them, not from a URI:
  `GitHubPath("src/x.py", owner="acme", repo="widgets")`. The URI classes keep the
  properties `repo_owner`, `repo`, `repo_path` and `ref`, and have `instance` and `kind`.
- Rate limits and replies of the wrong shape have classes of their own. Was:
  `OSError(EAGAIN)` and `OSError(EIO)`. Is: `RateLimitError` and `BadReplyError`, which
  are those `OSError` types and carry the path as `filename`. What to do: nothing;
  catch `RepoPathError` to catch both.
- An error raised through a URI path names the URI in its message, whatever scheme
  spelling built the path.
- A URI that holds no owner and no repository (`github://github.com/acme`) is a path that
  does not exist (`FileNotFoundError`, `exists()` is `False`) and no request is sent. Was:
  a request for a repository with an empty name.
- An owner or a repository that is empty, is a `.` or `..`, or (for `GitHubPath`) holds a
  `/` is refused by the constructor with `ValueError`. Was: refused when the API URL was
  built.
- `is_local()` of a URI path of these schemes is `False` without resolving the host.
  Was: the host name was looked up.
- Requests send `User-Agent: repopath/<version>` instead of `User-Agent: pathlib-next`;
  the version is `repopath.__version__`. A backend's `headers=` still replaces it.
- The `requests` HTTP transport is provided by `pathapi-requests`.
  `RepoBackend(session=...)` and `RepoBackend(**requests_args)` raise an `ImportError`
  naming that distribution without it. What to do: install `pathapi-requests`
  where one of them is used; with it installed they send through its transport and
  nothing else is configured.
- `uritools` is not a dependency of anything here, and `requests` is not installed with
  the package.

[Unreleased]: https://github.com/jose-pr/repopath/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jose-pr/repopath/releases/tag/v0.1.0
