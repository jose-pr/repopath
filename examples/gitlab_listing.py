"""List the files of a GitLab project using `GitLabPath`.

Needs nothing beyond `repopath` itself: the standard library sends the
requests. It works unauthenticated for public projects; set GITLAB_TOKEN for a
private one. Importing this module is always safe; run as a script it does
nothing, and exits 0, unless GITLAB_EXAMPLE_PROJECT is set.

    export GITLAB_EXAMPLE_PROJECT=gitlab-org/gitlab
    export GITLAB_EXAMPLE_PATH=doc         # optional, defaults to the project root
    export GITLAB_EXAMPLE_REF=master       # optional, defaults to the default branch
    export GITLAB_EXAMPLE_HOST=gitlab.com  # optional, for a self-hosted instance
    export GITLAB_TOKEN=...                # optional
    python examples/gitlab_listing.py
"""

import os
import sys

from repopath import GitLabPath


def list_gitlab(host: str, owner_repo: str, path: str, ref: str, token: str):
    owner, _, repo = owner_repo.rpartition("/")
    root = GitLabPath(
        path.strip("/"),
        owner=owner,
        repo=repo,
        ref=ref or None,
        instance=host,
        token=token or None,
    )
    print(f"Listing GitLab location: {root}")
    for child in root.iterdir():
        kind = "dir " if child.is_dir() else "file"
        print(f"  [{kind}] {child.name}")


if __name__ == "__main__":
    owner_repo = os.environ.get("GITLAB_EXAMPLE_PROJECT")
    if not owner_repo or "/" not in owner_repo:
        print(
            "GITLAB_EXAMPLE_PROJECT (group/project) is not set -- skipping. See "
            "this file's module docstring for the required environment variables.",
            file=sys.stderr,
        )
        raise SystemExit(0)

    host = os.environ.get("GITLAB_EXAMPLE_HOST", "gitlab.com")
    path = os.environ.get("GITLAB_EXAMPLE_PATH", "")
    ref = os.environ.get("GITLAB_EXAMPLE_REF", "")
    token = os.environ.get("GITLAB_TOKEN", "")

    try:
        list_gitlab(host, owner_repo, path, ref, token)
    except Exception as error:
        print(
            f"Could not list GitLab project {owner_repo} ({error}); skipping.",
            file=sys.stderr,
        )
