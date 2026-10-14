"""pathlib-style, read-only paths for the repository trees of GitHub and GitLab.

`RepoPath` is a path in the repository of a hosting service and `GitHubPath`
and `GitLabPath` are its two kinds; they read the REST API of the service and
speak no git protocol. They need no URI and no third-party library, so
`import repopath` needs no extra and never imports `uripath`. The `repo:`,
`github:` and `gitlab:` schemes of `uripath` are `repopath.uri`.
"""

from __future__ import annotations

from ._backend import BaseRepoBackend, RepoBackend
from ._github import GitHubPath
from ._gitlab import GitLabPath
from ._kinds import RepoPath
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
    "GitHubPath",
    "GitLabPath",
    "InsecureTransportWarning",
    "RateLimitError",
    "RepoBackend",
    "RepoPath",
    "RepoPathError",
    "__version__",
]
