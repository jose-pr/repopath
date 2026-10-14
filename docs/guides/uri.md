# The `repo:`, `github:` and `gitlab:` schemes

With [`uripath`](https://jose-pr.github.io/uripath/) installed (`pip install
"repopath[uri]"`), the URL of a file in a repository is a path:

<!-- example: skip: reads from the hosting services -->
```python
from uripath import URIPath

URIPath("repo://github.com/acme/widgets/src/mod.py?ref=v1.2")      # a RepoURIPath
URIPath("github://ghe.example:8443/acme/widgets/src/mod.py")       # a GitHubURIPath
URIPath("gitlab://git.example/acme/team/widgets/-/src/mod.py")     # a GitLabURIPath
```

`uripath` finds the classes through the entry points of the group `uripath.schemes` that
this distribution declares; nothing else is needed, and `import repopath` never imports
`uripath`. Each class is a URI path (see the `uripath` documentation for everything about
URIs) whose requests are done by the matching path of this package, so the two behave
alike, and an error names the URI.

| Scheme | Class | Kind |
| --- | --- | --- |
| `repo:` | `RepoURIPath` | detected from the host: `github.com` or `gitlab.com`, with or without `www.` |
| `github:` | `GitHubURIPath` | GitHub, on any host |
| `gitlab:` | `GitLabURIPath` | GitLab, on any host |

`repo://host/...` with a host that is neither is a `URISyntaxError` (a `ValueError`) that
names `github://host/...` and `gitlab://host/...`, which pin the kind.

## The parts of the URI

`<scheme>://[TOKEN@]host[:port]/owner/repo/path/in/repo?ref=REF`:

- The host and port are the instance; without a host the default instance of the kind
  is used.
- A GitLab project in a subgroup ends its path with GitLab's own `/-/`:
  `gitlab://host/group/subgroup/project/-/path`. Without the separator the first two
  segments are the owner and the repository. A directory called `-` needs it in front
  (`.../project/-/-/x`), and `path / "-"` writes it.
- `ref` is the query parameter `?ref=`, carried to every child. Without it the default
  branch is read.
- `repo_owner`, `repo`, `repo_path`, `ref`, `instance` and `kind` are properties of the URI
  path. A URI with no owner or repository is a path that does not exist.

## The token

The token is the user slot of the URI (`repo://TOKEN@github.com/...`) or its password
slot (`github://x-access-token:TOKEN@github.com/...`, or GitLab's `oauth2:TOKEN`), unless
`backend=` is given. Because the user slot can be the secret, `str()`, `repr()`,
`as_posix()` and `as_uri(sanitize=True)` of these paths, the `Source` of one, and so every
error message, show none of the userinfo; `as_uri()` still gives it back. `backend=` is a
`RepoBackend` or a `BaseRepoBackend` used as it is; without one, the paths derived from
one URI share the backend the first of them builds.
