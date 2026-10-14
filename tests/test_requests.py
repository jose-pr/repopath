"""What a repository path shows of a token, how it composes the API URL a
request would use, and when its backend warns. No test here reaches a server:
the backends are stubs that record the call."""

import urllib.parse
import warnings

import pytest

from http_fakes import FakeTransport, reply
from repopath import (
    BaseRepoBackend,
    GitHubPath,
    GitLabPath,
    InsecureTransportWarning,
    RepoBackend,
)

API = "https://api.example.test/base"


class _NoRequests(BaseRepoBackend):
    """A backend whose API root is `API` and which fails the test on use."""

    __slots__ = ("api_base", "cache")

    def __init__(self, api_base=API):
        self.api_base = api_base
        self.cache = {}

    def request(self, method, url, **kwargs):
        pytest.fail(f"a request was made: {method} {url}")


def _github(*names, owner="o", repo="r", ref=None):
    return GitHubPath(*names, owner=owner, repo=repo, ref=ref, backend=_NoRequests())


# --- a token is not shown ---


@pytest.mark.parametrize("cls", [GitHubPath, GitLabPath])
def test_no_display_form_of_a_repository_path_shows_the_token(cls):
    path = cls(
        "dir",
        "f.txt",
        owner="o",
        repo="r",
        ref="main",
        instance="ghe.example",
        token="SECRETTOKEN",
    )
    for shown in (
        str(path),
        repr(path),
        format(path),
        path.as_posix(),
        path.parent.as_posix(),
        path.as_uri(),
        str(path / "g"),
        repr(path / "g"),
        repr(path.backend),
    ):
        assert "SECRETTOKEN" not in shown
    assert path.backend.token == "SECRETTOKEN"


# --- owner, repository and path are one URL component each ---


@pytest.mark.parametrize(
    "owner, repo, names, expected",
    [
        ("o", "r?x=1", ("a.txt",), f"{API}/repos/o/r%3Fx%3D1/contents/a.txt"),
        (
            "o",
            "r#frag",
            ("dir/name", "a?b.txt"),
            f"{API}/repos/o/r%23frag/contents/dir/name/a%3Fb.txt",
        ),
        ("o", "r", ("a b", "c d.txt"), f"{API}/repos/o/r/contents/a%20b/c%20d.txt"),
        ("o", "r", ("a", ".", "b"), f"{API}/repos/o/r/contents/a/b"),
        ("o", "r", (), f"{API}/repos/o/r/contents"),
    ],
)
def test_github_api_url_encodes_owner_repo_and_path(owner, repo, names, expected):
    assert _github(*names, owner=owner, repo=repo)._contents_url() == expected


@pytest.mark.parametrize(
    "build",
    [
        lambda p: p.with_name(".."),
        lambda p: p / "..",
        lambda p: p.with_segments("a", "..", "x"),
        lambda p: p._make_child_relpath(".."),
        lambda p: p / "a/../..",
    ],
    ids=["with_name", "join", "with_segments", "child", "joined-text"],
)
def test_github_dot_segment_cannot_reach_the_api_url(build):
    path = build(_github("a"))
    with pytest.raises(ValueError):
        path._contents_url()
    # `exists()` answers a bool and sends nothing (the backend would fail).
    assert path.exists() is False


@pytest.mark.parametrize(
    "owner, repo",
    [
        ("..", "r"),
        (".", "r"),
        ("o", ".."),
        ("o/..", "r"),
        ("o", "r/.."),
        ("o/r", "x"),
        ("o", "a/b"),
        ("", "r"),
        ("o", ""),
    ],
)
def test_github_owner_or_repo_that_is_not_one_name_is_refused(owner, repo):
    with pytest.raises(ValueError):
        GitHubPath("a", owner=owner, repo=repo, backend=_NoRequests())


@pytest.mark.parametrize("owner", ["..", "a/../b", "a//b", "/a", "a/"])
def test_gitlab_owner_that_is_not_a_group_path_is_refused(owner):
    with pytest.raises(ValueError):
        GitLabPath("a", owner=owner, repo="r", backend=_NoRequests())


def test_github_tree_url_refuses_a_ref_that_is_a_dot_segment():
    path = _github("dir", ref="../../x")
    with pytest.raises(ValueError):
        path._tree_entries(path.ref)


@pytest.mark.parametrize("path_text", ["..", "a/..", "./x"])
def test_gitlab_dot_segment_cannot_reach_the_file_url(path_text):
    path = GitLabPath(owner="o", repo="r", backend=_NoRequests(f"{API}/api/v4"))
    with pytest.raises(ValueError):
        path._file_url(path_text)


def test_gitlab_file_url_is_one_encoded_segment():
    path = GitLabPath(owner="o", repo="r", backend=_NoRequests(f"{API}/api/v4"))
    assert path._file_url("a b/c#d.txt", "/raw") == (
        f"{API}/api/v4/projects/o%2Fr/repository/files/a%20b%2Fc%23d.txt/raw"
    )


def test_gitlab_project_of_a_group_is_one_encoded_segment():
    path = GitLabPath(owner="a/b", repo="r", backend=_NoRequests(f"{API}/api/v4"))
    assert path._tree_url() == f"{API}/api/v4/projects/a%2Fb%2Fr/repository/tree"


# --- a token over plain http warns, except to this machine ---


def _send(api_base, **backend_args):
    transport = FakeTransport(default=reply(200))
    backend = RepoBackend(transport=transport, api_base=api_base, **backend_args)
    backend.request("GET", f"{backend.api_base}/repos/o/r")
    return [(s.method, s.url, s.headers) for s in transport.sent]


def test_a_repository_api_request_names_repopath_and_its_version_as_its_user_agent():
    import repopath

    calls = _send("https://api.example.test")
    assert calls[0][2]["User-Agent"] == f"repopath/{repopath.__version__}"


def test_token_over_plain_http_to_another_host_warns_and_is_still_sent():
    with pytest.warns(InsecureTransportWarning, match="api.example.test"):
        calls = _send("http://api.example.test/api", token="T")
    assert calls[0][2]["Authorization"] == "Bearer T"


def test_authorization_header_over_plain_http_warns():
    with pytest.warns(InsecureTransportWarning):
        _send("http://api.example.test", headers={"authorization": "Token T"})


@pytest.mark.parametrize(
    "api_base",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8080/api",
        "http://[::1]:8080",
        "http://api.localhost",
        "https://api.example.test",
    ],
)
def test_token_to_loopback_or_https_does_not_warn(api_base):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _send(api_base, token="T")


def test_plain_http_without_a_credential_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _send("http://api.example.test")


def test_the_warning_is_a_userwarning_the_header_names():
    import pathlib

    import repopath

    assert issubclass(InsecureTransportWarning, UserWarning)
    header = (pathlib.Path(repopath.__file__).parent / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "InsecureTransportWarning" in header


def test_the_default_timeout_applies_unless_one_is_given():
    transport = FakeTransport(default=reply(200))
    RepoBackend(transport=transport).request("GET", "http://h/x")
    assert transport.sent[-1].timeout == (10, 60)
    RepoBackend(transport=transport, timeout=None).request("GET", "http://h/x")
    assert transport.sent[-1].timeout is None
    RepoBackend(transport=transport, timeout=3).request(
        "GET", "http://h/x", timeout=(1, 2)
    )
    assert transport.sent[-1].timeout == (1, 2)


def test_a_ref_is_a_query_parameter_and_not_part_of_the_path():
    # The ref is sent as a query parameter of the request, not spliced into the
    # path, so a ref holding `/` or `?` cannot change which endpoint is asked.
    sent = []

    class _Recording(_NoRequests):
        def request(self, method, url, **kwargs):
            sent.append((url, kwargs.get("params")))
            return reply(200, b"[]", {"Content-Type": "application/json"})

    path = GitHubPath("dir", owner="o", repo="r", ref="a/b?c=d#e", backend=_Recording())
    list(path.iterdir())
    url, params = sent[0]
    assert urllib.parse.urlsplit(url).query == ""
    assert params == {"ref": "a/b?c=d#e"}
