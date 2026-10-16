# repopath

[![Version](https://img.shields.io/pypi/v/repopath.svg)](https://pypi.org/project/repopath/)
[![Python versions](https://img.shields.io/pypi/pyversions/repopath.svg)](https://pypi.org/project/repopath/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/repopath/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/repopath/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/repopath/test.yml)](https://github.com/jose-pr/repopath/actions/workflows/test.yml)

**`pathlib` for the repository trees of GitHub, GitLab, Gitea, Subversion and SourceForge, read-only.**
`GitHubPath("src/mod.py", owner="acme", repo="widgets", ref="v1.2")` has the method
names, signatures and exception types of `pathlib.Path`, and `GitLabPath` does the same
for GitLab; `RepoPath` is the class that returns either. None needs a URI, and all
build on [`pathapi`](https://github.com/jose-pr/pathapi) alone; with
[`uripath`](https://github.com/jose-pr/uripath) installed the same package also
provides the `repo:`, `github:`, `gitlab:`, `svn+https:`, `svn+http:` and `sourceforge:` schemes. It is documented at
<https://jose-pr.github.io/repopath/>.

The classes are clients of the hosting services' REST APIs. They speak no git
protocol: there is no clone, no commit and no `git:` URL, and the tree is read-only.

## Features

| Kind | Class | `type=` | Detected for | API |
| --- | --- | --- | --- | --- |
| GitHub and GitHub Enterprise | `GitHubPath` | `"github"` | `github.com` | contents API at `api.github.com`, or `https://<instance>/api/v3` |
| GitLab and self-hosted GitLab | `GitLabPath` | `"gitlab"` | `gitlab.com` | REST API v4 at `https://<instance>/api/v4` |
| Gitea, Forgejo and Codeberg | `GiteaPath` | `"gitea"` (alias `"forgejo"`) | `codeberg.org` | REST API at `https://<instance>/api/v1` |
| Subversion over HTTP | `SVNPath` | `"svn"` | (`type=` and the URL of the repository) | WebDAV of `mod_dav_svn` at that URL |
| SourceForge (Subversion tools) | `SourceForgePath` | `"sourceforge"` | `sourceforge.net` | REST API of the site, then the tool's Subversion URL |

- **A `pathlib` path in a repository**: `open`, `read_text`, `iterdir`, `glob`, `stat`
  and the rest, plus `walk`, `copy` out of the tree and `PathSyncer` as a source, from
  `pathapi`. Nothing connects until the first request.
- **One class for the kind**: `RepoPath("f", owner="acme", repo="widgets",
  instance="github.com")` is a `GitHubPath`; another instance needs `type=`, and
  `RepoPath.kinds()` lists the kinds. A kind is a subclass that registers itself.
- **Cheap listings**: a directory is one request and each child carries the size and
  type the listing gave, so `walk()` and `glob()` cost one request per directory;
  `stat()` of a file is a `HEAD`. Listings past GitHub's 1,000-entry cap and GitLab's
  pages are read to the end.
- **Authentication that stays out of sight**: `token=` is sent as a bearer token to the
  API's own origin only, and shown by no `str()`, `repr()`, pickle or error message. A
  token over plain `http://` to another host warns.
- **No library to install**: requests go through `pathapi.http`, on the standard
  library, with a replaceable transport. Errors are the `OSError` types `pathlib` raises,
  `RateLimitError` (a `BlockingIOError`) for a rate limit and `BadReplyError` for a reply
  that is not the shape the API documents.
- **Subversion and SourceForge**: `SVNPath("trunk/README", instance="https://svn.example.org/repos/project")`
  reads a Subversion repository served over HTTP with a request per `stat()` and per
  listing, a revision with `ref=`, links and the md5 and sha1 digests the server states;
  `SourceForgePath("README", project="docutils")` finds the Subversion tool of a
  SourceForge project and reads it the same way.
- **The `repo:`, `github:`, `gitlab:`, `svn+https:`, `svn+http:` and `sourceforge:`
  schemes** for `uripath`:
  `URIPath("github://github.com/acme/widgets/src/mod.py?ref=v1.2")` is a
  `GitHubURIPath` that delegates to a `GitHubPath`.

## Installation

```bash
pip install repopath
```

Requires `pathapi`, which pip installs with it, and Python 3.9 or newer.

| Install | Adds |
| --- | --- |
| `pip install repopath` | the paths and the backends; no other library |
| `pip install "repopath[uri]"` | `uripath` and with it the `repo:`, `github:`, `gitlab:`, `svn+https:`, `svn+http:` and `sourceforge:` schemes |
| `pip install "repopath[all]"` | all of the above |

## Quick start

**A path in a repository**: the owner and repository are keywords, the segments are the
path inside it, and `ref` is a branch, a tag or a commit:

<!-- example: skip: reads from GitHub -->
```python
from repopath import GitHubPath

readme = GitHubPath("README.md", owner="psf", repo="requests")
print(readme.read_text()[:200])
for child in readme.parent.iterdir():
    print(child.name, child.is_dir())
```

**Another kind, or another instance**: `instance` names the server, `type` the kind when
the instance does not tell, and `token` is the credential:

<!-- example: skip: reads from GitLab -->
```python
from repopath import GitLabPath, RepoPath

doc = RepoPath("doc", owner="gitlab-org", repo="gitlab", instance="gitlab.com")
private = RepoPath(
    "src", owner="team/tools", repo="app", instance="git.example.org",
    type="gitlab", token="glpat-...",
)
assert isinstance(doc, GitLabPath)
```

**Copy a tree** out of a repository with `pathapi`:

<!-- example: skip: reads from GitHub -->
```python
from pathapi import LocalPath
from repopath import GitHubPath

GitHubPath("docs", owner="psf", repo="requests", ref="main").copy(
    LocalPath("docs-copy"), recursive=True
)
```

**The schemes**, with `uripath` installed:

<!-- example: skip: reads from GitHub -->
```python
from uripath import URIPath

print(URIPath("repo://github.com/psf/requests/README.md?ref=main").read_text()[:200])
```

## API overview

| Module | Purpose |
| --- | --- |
| `repopath` | `RepoPath`, `GitHubPath`, `GitLabPath`, `GiteaPath`, `SVNPath`, `SourceForgePath`, the backends `RepoBackend` and `BaseRepoBackend`, the exceptions `RepoPathError`, `RateLimitError` and `BadReplyError`, the warning `InsecureTransportWarning` and `__version__` |
| `repopath.exceptions` | The exceptions and the warning |
| `repopath.uri` | `RepoURIPath`, `GitHubURIPath`, `GitLabURIPath`, `SVNURIPath` and `SourceForgeURIPath`, the schemes of `uripath` (needs the `uri` extra) |

The full reference is on the [documentation site](https://jose-pr.github.io/repopath/);
an agent-oriented API summary ships inside the package as `repopath/AGENTS.md`.

## Development

```bash
pip install -e ".[dev,all]"
python -m pytest -q -rs
```

The tests send every request to a fake of the API on the loopback interface; none
reaches another host. A test that needs `uripath` is skipped without it, so check the
skip list (`-rs`). Contributor notes (virtual environments, formatting, CI, releases) are
in the repository's [`AGENTS.md`](https://github.com/jose-pr/repopath/blob/main/AGENTS.md).
To preview the documentation: `pip install -e ".[all,docs]"` then `mkdocs serve`.

Changes are recorded in the
[changelog](https://github.com/jose-pr/repopath/blob/main/CHANGELOG.md).

### Releasing

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](https://github.com/jose-pr/repopath/blob/main/CHANGELOG.md). Pushing a tag matching `v*` triggers the release
workflow: test gate → build (checking the tag names the version built) → a strict
docs build as a gate → GitHub release → publish. The release workflow never deploys
the docs site itself: for a final release its last job dispatches the docs workflow
at the tag, which owns every Pages deploy.

## License

MIT — see [LICENSE](https://github.com/jose-pr/repopath/blob/main/LICENSE).
