"""pathlib-style, read-only paths for the repository trees of GitHub, GitLab and Gitea.

`RepoPath` is a path in the repository of a hosting service and `GitHubPath`,
`GitLabPath` and `GiteaPath` are three of its kinds; they read the REST API of
the service and speak no git protocol. They need no URI and no third-party library, so
`import repopath` needs no extra and never imports `uripath`. The `repo:`,
`github:`, `gitlab:` and `gitea:` schemes of `uripath` are `repopath.uri`.
"""

from __future__ import annotations

from ._backend import BaseRepoBackend, RepoBackend
from ._gitea import GiteaPath
from ._github import GitHubPath
from ._gitlab import GitLabPath
from ._kinds import RepoPath
from ._sourceforge import SourceForgePath
from ._svn import SVNPath
from ._version import __version__
from .exceptions import (
    BadReplyError,
    InsecureTransportWarning,
    RateLimitError,
    RepoPathError,
)

__all__ = [
    "BadReplyError",
    "BaseRepoBackend",
    "GiteaPath",
    "GitHubPath",
    "GitLabPath",
    "InsecureTransportWarning",
    "RateLimitError",
    "RepoBackend",
    "RepoPath",
    "RepoPathError",
    "SVNPath",
    "SourceForgePath",
    "__version__",
]
