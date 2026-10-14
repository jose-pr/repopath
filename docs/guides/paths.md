# Repository paths

`GitHubPath` and `GitLabPath` are `pathapi.Path` classes over the REST API of a
hosting service. `RepoPath` is the family they belong to: calling it returns one of them.
Everything `pathlib` does to read a tree works; every write raises `NotImplementedError`.

## The place

```python
from repopath import GitHubPath, GitLabPath

GitHubPath("src/mod.py", owner="acme", repo="widgets")
GitHubPath("src/mod.py", owner="acme", repo="widgets", ref="v1.2")        # a tag
GitHubPath("src/mod.py", owner="acme", repo="widgets", instance="ghe.example:8443")
GitLabPath("src/mod.py", owner="acme/team", repo="widgets")               # a subgroup
```

- The segments are the path inside the repository, joined like a POSIX path; `x` and
  `/x` are one file. The owner and the repository are keywords and are required, unless a
  path they are joined from supplies them: `path / "x"`, `path.parent` and
  `GitHubPath(path, "x")` keep the place, and a keyword moves it.
- `instance` is the host of the service, with a port when it is not the default.
  `github.com` and `gitlab.com` are the defaults of the two classes; any other instance
  is GitHub Enterprise (API at `https://<instance>/api/v3`) or a self-hosted GitLab
  (`https://<instance>/api/v4`).
- `ref` is a branch, a tag or a commit. Without one the default branch is read; on
  GitLab it is looked up once per backend. `ref=""` on a joined path goes back to it.
- A GitLab owner may be a group path (`"acme/team"`), which is how a project in a
  subgroup is named.

## Which class

`RepoPath(*segments, instance=..., type=None, **options)` returns a path of one kind.
`type` names the kind; without it the kind is detected from the instance, which works for
`github.com` and `gitlab.com` only. Any other instance is a `ValueError` that asks for
`type=`:

```python
from repopath import RepoPath

RepoPath("f", owner="o", repo="r", instance="github.com")                    # GitHubPath
RepoPath("f", owner="o", repo="r", instance="git.example", type="gitlab")    # GitLabPath
RepoPath.kinds()                                                             # ("github", "gitlab")
```

A kind is a subclass of `RepoPath` whose class body sets `TYPE` (its name) and
`INSTANCES` (the instances it is detected for); defining it registers it, so
`RepoPath(type="name", **its_keywords)` returns it. A name or an instance another class
holds is a `ValueError` unless the class statement passes `replace=True`. The keywords
are the kind's own: `owner` and `repo` belong to the two kinds that have an owner and a
repository, not to `RepoPath`.

## The backend and the token

A backend sends the requests. `token=` builds the path's own `RepoBackend` with that
bearer token and every path joined from it shares it; `backend=` shares one between paths
built separately, or plugs in a `BaseRepoBackend` of your own. Giving both is a
`TypeError`. Without either, the path builds an anonymous backend.

<!-- example: skip: reads from GitHub -->
```python
from repopath import GitHubPath, RepoBackend

with RepoBackend(token="ghp_...", timeout=(5, 30)) as backend:
    root = GitHubPath(owner="acme", repo="widgets", backend=backend)
    for child in root.iterdir():
        print(child)
```

The backend sends through `pathapi.http`: the standard-library transport by default
(`PATHAPI_HTTP_TRANSPORT` chooses another), `timeout=(10, 60)` seconds unless given,
and the token only to the origin of the API. `api_base=` replaces the API root a path
derives from its instance. A token over plain `http://` to a host other than this machine
warns (`InsecureTransportWarning`).

## What it costs

- A directory is one request, and every child carries the stat the listing gave, so
  `walk()` and `glob()` cost one request per directory. On GitHub the listing has
  type and size; on GitLab it has the type only, so `is_dir()` and `is_file()` of a
  listed child are free and `stat()` of a file is one `HEAD`.
- `stat()` of a GitHub file is a `HEAD` of the contents URL asked for the raw media type;
  a directory is then listed. A directory of more than 1,000 entries is read through the
  Git Trees API, and GitLab's pages are read to the last one.
- A file is read whole into memory. A JSON reply over `pathapi.http.MAX_REPLY_BYTES`
  (8 MiB) is `OSError(EFBIG)`.
- Anonymous requests are rate limited by the service; the limit is `RateLimitError`, a
  `BlockingIOError` whose message says when to retry.

## Failures

Every failure is the `OSError` `pathlib` raises for the same condition. A reply that is
not the shape the API documents (a proxy's page, `null`, a truncated body) is
`BadReplyError`, an `OSError(EIO)` naming the path, and `exists()` is then `False`. No
message names a token, and no exception of the transport crosses the API.
