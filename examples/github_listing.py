"""List the files of a GitHub repository using `GitHubPath`.

Needs nothing beyond `repopath` itself: the standard library sends the
requests. It works unauthenticated for public repositories (GitHub's anonymous
rate limit is 60 requests an hour); set GITHUB_TOKEN for a private one.
Importing this module is always safe; run as a script it does nothing, and
exits 0, unless GITHUB_EXAMPLE_REPO is set.

    export GITHUB_EXAMPLE_REPO=psf/requests
    export GITHUB_EXAMPLE_PATH=requests   # optional, defaults to the repository root
    export GITHUB_EXAMPLE_REF=main        # optional, defaults to the default branch
    export GITHUB_TOKEN=...               # optional
    python examples/github_listing.py
"""

import os
import sys

from repopath import GitHubPath


def list_github(owner_repo: str, path: str, ref: str, token: str):
    owner, _, repo = owner_repo.partition("/")
    root = GitHubPath(
        path.strip("/"),
        owner=owner,
        repo=repo,
        ref=ref or None,
        token=token or None,
    )
    print(f"Listing GitHub location: {root}")
    for child in root.iterdir():
        kind = "dir " if child.is_dir() else "file"
        size = "" if child.is_dir() else f" ({child.stat().st_size} bytes)"
        print(f"  [{kind}] {child.name}{size}")


if __name__ == "__main__":
    owner_repo = os.environ.get("GITHUB_EXAMPLE_REPO")
    if not owner_repo or "/" not in owner_repo:
        print(
            "GITHUB_EXAMPLE_REPO (owner/repo) is not set -- skipping. See this "
            "file's module docstring for the required environment variables.",
            file=sys.stderr,
        )
        raise SystemExit(0)

    path = os.environ.get("GITHUB_EXAMPLE_PATH", "")
    ref = os.environ.get("GITHUB_EXAMPLE_REF", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    try:
        list_github(owner_repo, path, ref, token)
    except Exception as error:
        print(
            f"Could not list GitHub repo {owner_repo} ({error}); skipping.",
            file=sys.stderr,
        )
