"""Builds the same repository path as a `GitHubPath` or `GitLabPath` (the
`"path"` flavor) or as the URI path of its scheme (the `"uri"` flavor), so a
test of what a path does runs through both."""

from __future__ import annotations

import urllib.parse

from repopath import GiteaPath, GitHubPath, GitLabPath, RepoBackend

_KINDS = {"github": GitHubPath, "gitlab": GitLabPath, "gitea": GiteaPath}
_DEFAULT_INSTANCE = {
    "github": "github.com",
    "gitlab": "gitlab.com",
    "gitea": "codeberg.org",
}


def fake_backend(kind: str, server, **options) -> RepoBackend:
    """A `RepoBackend` that talks to the loopback fake `server` (the
    `(base_url, owner, repo)` a fixture yields) instead of the real API."""
    base_url = server[0] if isinstance(server, tuple) else server
    api = {"github": base_url, "gitlab": f"{base_url}/api/v4"}.get(
        kind, f"{base_url}/api/v1"
    )
    return RepoBackend(api_base=api, **options)


def make_path(
    flavor: str,
    kind: str,
    name: str = "",
    *,
    owner: str = "acme",
    repo: str = "widgets",
    ref: "str | None" = None,
    instance: "str | None" = None,
    backend=None,
):
    """The path `name` (the path inside the repository) of `owner/repo`."""
    if flavor == "path":
        names = [part for part in name.split("/") if part]
        return _KINDS[kind](
            *names, owner=owner, repo=repo, ref=ref, instance=instance, backend=backend
        )
    from repopath import uri

    cls = {
        "github": uri.GitHubURIPath,
        "gitlab": uri.GitLabURIPath,
        "gitea": uri.GiteaURIPath,
    }[kind]
    segments = owner.split("/") + [repo]
    if "/" in owner or "-" in name.split("/"):
        segments.append("-")
    text = f"{kind}://{instance or _DEFAULT_INSTANCE[kind]}/" + "/".join(segments)
    if name:
        text += "/" + name
    if ref:
        text += "?ref=" + urllib.parse.quote(ref, safe="")
    return cls(text, backend=backend)


def github(flavor, server, name="", *, backend=None, **keywords):
    """`name` in the repository the fake GitHub `server` serves."""
    owner, repo = server[1], server[2]
    backend = backend or fake_backend("github", server)
    return make_path(
        flavor, "github", name, owner=owner, repo=repo, backend=backend, **keywords
    )


def gitlab(flavor, server, name="", *, backend=None, **keywords):
    """`name` in the repository the fake GitLab `server` serves."""
    owner, repo = server[1], server[2]
    backend = backend or fake_backend("gitlab", server)
    return make_path(
        flavor, "gitlab", name, owner=owner, repo=repo, backend=backend, **keywords
    )


def gitea(flavor, server, name="", *, backend=None, **keywords):
    """`name` in the repository the fake Forgejo `server` serves."""
    owner, repo = server[1], server[2]
    backend = backend or fake_backend("gitea", server)
    return make_path(
        flavor, "gitea", name, owner=owner, repo=repo, backend=backend, **keywords
    )
