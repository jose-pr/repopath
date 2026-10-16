# repopath

**`pathlib` for the repository trees of GitHub and GitLab, read-only.**
`GitHubPath("src/mod.py", owner="acme", repo="widgets", ref="v1.2")` has the method names,
signatures and exception types of `pathlib.Path`; `GitLabPath` does the same for GitLab,
and `RepoPath` is the class that returns either. None needs a URI, and all build on
[`pathapi`](https://jose-pr.github.io/pathapi/) alone, which holds the `Path`
base class, `glob`, `walk` and `PathSyncer`. With
[`uripath`](https://jose-pr.github.io/uripath/) installed the same package also provides
the `repo:`, `github:` and `gitlab:` schemes:
`URIPath("repo://github.com/acme/widgets/src/mod.py")`.

The classes read the REST APIs of the services and speak no git protocol: there is no
clone and no commit, and the tree is read-only.

Deliberate behavioral differences from `pathlib` are listed, with their reasons, in
[Divergences from pathlib](divergences.md).

## Installation

```bash
pip install repopath
pip install "repopath[uri]"           # adds uripath and with it the schemes
```

No other library is required: requests go through `pathapi.http`, on the standard
library.

| Install | Adds |
| --- | --- |
| `pip install repopath` | the paths and the backends |
| `pip install "repopath[uri]"` | `uripath` and the schemes |
| `pip install "repopath[all]"` | all of the above |

## Tour

**A path in a repository**: the owner and the repository are keywords and the segments
are the path inside it; nothing connects until the first request:

<!-- example: skip: reads from GitHub -->
```python
from repopath import GitHubPath

readme = GitHubPath("README.md", owner="psf", repo="requests")
print(readme.read_text()[:200])
for child in readme.parent.iterdir():
    print(child.name, child.is_dir())
```

**Which kind**: `RepoPath` returns a `GitHubPath` or a `GitLabPath`, by `type=` or by the
instance it is given:

<!-- example: skip: reads from GitLab -->
```python
from repopath import RepoPath

doc = RepoPath("doc", owner="gitlab-org", repo="gitlab", instance="gitlab.com")
tools = RepoPath(
    "src", owner="team/tools", repo="app", instance="git.example.org",
    type="gitlab", token="glpat-...",
)
print(RepoPath.kinds())
```

**The schemes**: with `uripath` installed, the URL of a file is a path, and the same
classes do the work:

<!-- example: skip: reads from GitHub -->
```python
from uripath import URIPath

print(URIPath("github://github.com/psf/requests/README.md?ref=main").read_text()[:200])
print(URIPath("repo://gitlab.com/gitlab-org/gitlab/README.md").stat().st_size)
```

**Subversion and SourceForge**: `SVNPath` reads a Subversion repository served over HTTP and
`SourceForgePath` the Subversion tool of a SourceForge project:

<!-- example: skip: reads from a Subversion server -->
```python
from repopath import SourceForgePath, SVNPath

print(SVNPath("trunk/README", instance="https://svn.example.org/repos/p", ref=1234).read_text())
print(SourceForgePath("README.rst", project="docutils").checksum())
```

See [Repository paths](guides/paths.md) and [The schemes](guides/uri.md).
