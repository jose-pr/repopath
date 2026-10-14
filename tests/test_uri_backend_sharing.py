"""The paths of one repository look the default branch up once: the children of
a root share the backend, and so its cache, that the first of them builds -- a
URI path derives one for its family, a path built without a backend does the
same for the paths joined from it."""

import pytest

pytestmark = pytest.mark.usefixtures("http_transport_kind")

from repopath import GitLabPath, RepoBackend


class _CountingRepoBackend(RepoBackend):
    __slots__ = ("calls",)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return super().request(method, url, **kwargs)


def _lookups(calls, owner, repo):
    return [
        url for _method, url in calls if url.endswith(f"/projects/{owner}%2F{repo}")
    ]


def test_the_children_of_a_gitlab_root_look_the_default_branch_up_once(
    gitlab_api_server,
):
    base, owner, repo = gitlab_api_server
    backend = _CountingRepoBackend(api_base=base + "/api/v4")
    backend.calls = calls = []
    root = GitLabPath(owner=owner, repo=repo, backend=backend)
    assert (root / "a.txt").read_bytes() == b"a"
    assert (root / "b.py").read_bytes() == b"b"
    assert (root / "sub" / "c.py").read_bytes() == b"c"
    assert len(_lookups(calls, owner, repo)) == 1


def test_the_children_of_a_uri_root_share_the_backend_the_first_of_them_builds(
    gitlab_api_server,
):
    pytest.importorskip("uripath")
    from uripath import URIPath
    from repopath import uri

    base, owner, repo = gitlab_api_server
    calls = []

    class GitLabLoopbackURIPath(uri.GitLabURIPath):
        """`gitlab:` whose derived backend talks to `base` instead."""

        __SCHEMES = ("gitlab-loopback",)
        __slots__ = ()

        def _initbackend(self):
            backend = _CountingRepoBackend(api_base=base + "/api/v4")
            backend.calls = calls
            return backend

    root = URIPath(f"gitlab-loopback://x/{owner}/{repo}")
    assert (root / "a.txt").read_bytes() == b"a"
    assert (root / "b.py").read_bytes() == b"b"
    assert (root / "sub" / "c.py").read_bytes() == b"c"
    assert len(_lookups(calls, owner, repo)) == 1
