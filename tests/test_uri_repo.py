"""The `repo:`, `github:` and `gitlab:` schemes: which class a URI gets, how its
parts become an owner, a repository, a path and a ref, where the token comes
from and what never shows it, and what the URI paths leave to the path they
delegate to (see test_repo.py for what that path does)."""

from __future__ import annotations

import errno
import pickle
import urllib.parse

import pytest

uripath = pytest.importorskip("uripath")

from child import run
from http_fakes import reply
from repo_support import fake_backend, gitlab
from repopath import BaseRepoBackend, GitHubPath, GitLabPath, RepoBackend
from repopath import uri
from uripath import Source, URIPath
from uripath.exceptions import URISyntaxError

API = "https://api.example.test/base"


class _Recording(BaseRepoBackend):
    """Records each request and answers an empty listing."""

    __slots__ = ("api_base", "cache", "requests")

    def __init__(self, api_base=API):
        self.api_base = api_base
        self.cache = {}
        self.requests = []

    def request(self, method, url, headers=None, params=None, **kwargs):
        self.requests.append((method, url, params))
        return reply(200, b"[]", {"Content-Type": "application/json"})


# --- which class a URI gets ----------------------------------------------------------


@pytest.mark.parametrize(
    "text, cls",
    [
        ("github://127.0.0.1/o/r/f", uri.GitHubURIPath),
        ("github://ghe.internal/o/r/f", uri.GitHubURIPath),
        ("gitlab://git.example/o/r/f", uri.GitLabURIPath),
        ("repo://github.com/o/r/f", uri.RepoURIPath),
        ("repo://gitlab.com/o/r/f", uri.RepoURIPath),
    ],
)
def test_a_uri_gets_the_class_of_its_scheme(text, cls):
    path = URIPath(text)
    assert type(path) is cls
    assert isinstance(path, uri.RepoURIPath)


@pytest.mark.parametrize(
    "host, kind",
    [
        ("github.com", "github"),
        ("GitHub.COM", "github"),
        ("www.github.com", "github"),
        ("WWW.GitLab.com", "gitlab"),
        ("gitlab.com", "gitlab"),
    ],
)
def test_repo_detects_the_kind_from_the_host_in_any_case(host, kind):
    path = URIPath(f"repo://{host}/o/r/x")
    assert path.kind == kind
    assert type(path._core_path) is {"github": GitHubPath, "gitlab": GitLabPath}[kind]


def test_github_and_gitlab_pin_the_kind_whatever_the_host():
    assert URIPath("github://gitlab.com/o/r").kind == "github"
    assert URIPath("gitlab://github.com/o/r").kind == "gitlab"
    assert URIPath("github://ghe.example:8443/o/r").kind == "github"


@pytest.mark.parametrize(
    "text",
    [
        "repo://git.example/o/r",
        "repo://127.0.0.1/o/r",
        "repo://[::1]/o/r",
        "repo:///o/r",
        "repo:",
    ],
)
def test_repo_refuses_a_host_it_cannot_detect_and_names_the_pinned_schemes(text):
    with pytest.raises(ValueError) as excinfo:
        URIPath(text)
    assert isinstance(excinfo.value, URISyntaxError)
    message = str(excinfo.value)
    assert "github:" in message and "gitlab:" in message


def test_repo_refuses_such_a_host_when_the_class_is_called_directly():
    with pytest.raises(URISyntaxError):
        uri.RepoURIPath("repo://git.example/o/r")
    with pytest.raises(URISyntaxError):
        uri.RepoURIPath("repo://git.example/o/r", backend=_Recording())


def test_the_refusal_of_a_host_shows_no_token():
    # Built outside the raising line: a traceback prints that source line.
    text = "repo://" + "ghp_SECRET123" + "@ghe.internal/acme/widgets/README.md"
    with pytest.raises(ValueError) as excinfo:
        URIPath(text)
    assert "ghp_SECRET123" not in str(excinfo.value)
    assert "ghe.internal/acme/widgets/README.md" in str(excinfo.value)


def test_moving_a_repo_path_to_a_host_that_has_no_kind_is_refused():
    path = URIPath("repo://github.com/o/r/f")
    moved = path.with_source(Source("repo", None, "gitlab.com", None))
    assert moved.kind == "gitlab"
    with pytest.raises(URISyntaxError):
        path.with_source(Source("repo", None, "git.example", None))


@pytest.mark.parametrize(
    "text", ["git://github.com/o/r/f", "git+github://h/o/r/f", "git+gitlab://h/o/r/f"]
)
def test_the_git_schemes_are_not_dispatched_to_this_distribution(text):
    path = URIPath(text)
    assert type(path) is URIPath
    with pytest.raises(NotImplementedError):
        path.stat()


def test_the_entry_points_name_the_six_schemes_and_nothing_else():
    run_result = run("""
        import importlib.metadata as metadata

        entry_points = metadata.entry_points()
        group = (
            entry_points.select(group="uripath.schemes")
            if hasattr(entry_points, "select")
            else entry_points.get("uripath.schemes", [])
        )
        found = {
            entry.name: entry.value
            for entry in group
            if entry.value.startswith("repopath")
        }
        print(sorted(found.items()))
        """)
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.strip() == str(
        [
            ("github", "repopath.uri:GitHubURIPath"),
            ("gitlab", "repopath.uri:GitLabURIPath"),
            ("repo", "repopath.uri:RepoURIPath"),
            ("sourceforge", "repopath.uri:SourceForgeURIPath"),
            ("svn+http", "repopath.uri:SVNURIPath"),
            ("svn+https", "repopath.uri:SVNURIPath"),
        ]
    )


def test_a_scheme_is_found_through_its_entry_point_without_importing_the_module():
    result = run("""
        import sys
        from uripath import URIPath

        assert "repopath.uri" not in sys.modules
        schemes = URIPath.schemes()
        assert {"repo", "github", "gitlab"} <= set(schemes)
        assert "repopath.uri" not in sys.modules
        path = URIPath("repo://github.com/o/r/f")
        print(type(path).__module__, type(path).__name__, path.kind)
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "repopath.uri RepoURIPath github"


# --- the parts of a URI ------------------------------------------------------------------


def test_a_uri_path_does_not_call_the_repository_owner_owner():
    path = URIPath("github://github.com/acme/widgets/src")
    assert not isinstance(getattr(path, "owner", None), str)


def test_owner_repo_repo_path_and_ref_of_a_uri():
    path = URIPath("github://github.com/acme/widgets/src/pkg/mod.py?ref=v1.2.3")
    assert (path.repo_owner, path.repo, path.repo_path, path.ref) == (
        "acme",
        "widgets",
        "src/pkg/mod.py",
        "v1.2.3",
    )
    assert path.instance == "github.com"


def test_the_ref_is_read_from_the_query_and_defaults_to_none():
    assert URIPath("github://github.com/a/w/f").ref is None
    assert URIPath("github://github.com/a/w/f?ref=").ref is None
    assert URIPath("github://github.com/a/w/f?ref=feat%26x").ref == "feat&x"


@pytest.mark.parametrize(
    "text, owner, repo, repo_path",
    [
        (
            "gitlab://gitlab.com/acme/team/widgets/-/sub/c.py",
            "acme/team",
            "widgets",
            "sub/c.py",
        ),
        ("gitlab://gitlab.com/acme/team/widgets/-", "acme/team", "widgets", ""),
        ("gitlab://gitlab.com/acme/widgets/sub/c.py", "acme", "widgets", "sub/c.py"),
        ("gitlab://gitlab.com/acme/widgets/-/sub/c.py", "acme", "widgets", "sub/c.py"),
        ("gitlab://gitlab.com/acme/widgets/-/-/f", "acme", "widgets", "-/f"),
        ("gitlab://gitlab.com/acme/-/f", "acme", "-", "f"),
        ("gitlab://gitlab.com/acme", "acme", "", ""),
        ("gitlab://gitlab.com/", "", "", ""),
    ],
)
def test_gitlab_reads_the_separator_that_ends_the_project(text, owner, repo, repo_path):
    path = URIPath(text)
    assert (path.repo_owner, path.repo, path.repo_path) == (owner, repo, repo_path)


def test_a_gitlab_subgroup_project_reads_through_the_separator(gitlab_api_server):
    backend = fake_backend("gitlab", gitlab_api_server)
    root = uri.GitLabURIPath("gitlab://gitlab.com/acme/team/widgets/-", backend=backend)
    assert root.is_dir()
    sub = next(c for c in root.iterdir() if c.name == "sub")
    assert sub.as_uri() == "gitlab://gitlab.com/acme/team/widgets/-/sub"
    assert sorted(c.name for c in sub.iterdir()) == ["c.py", "nested"]
    assert root.parent.name == "widgets" and sub.parent == root


def test_a_directory_named_dash_gets_the_separator_in_front_of_it(
    gitlab_api_server, fixture_tree
):
    (fixture_tree / "-").mkdir()
    (fixture_tree / "-" / "inside.txt").write_text("dash")
    root = gitlab("uri", gitlab_api_server)
    by_hand = root / "-"
    listed = next(c for c in root.iterdir() if c.name == "-")
    assert by_hand.path == listed.path
    assert listed.as_uri().endswith("/acme/widgets/-/-")
    assert next(listed.iterdir()).read_bytes() == b"dash"


def test_a_uri_without_an_owner_and_a_repository_asks_nothing_and_is_not_there():
    for text in ("github://github.com", "github://github.com/acme", "gitlab://h/acme"):
        path = uri.GitHubURIPath(text, backend=_Recording())
        with pytest.raises(FileNotFoundError):
            path.stat()
        assert path.exists() is False and path.is_dir() is False
        assert path.backend.requests == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("gitlab://gitlab.internal:8929/o/r", "https://gitlab.internal:8929/api/v4"),
        ("gitlab://[fd00::5]/o/r", "https://[fd00::5]/api/v4"),
        ("gitlab://[fd00::5]:8443/o/r", "https://[fd00::5]:8443/api/v4"),
        ("gitlab://GitLab.Example:9/o/r", "https://gitlab.example:9/api/v4"),
        ("github://ghe.internal:8443/o/r", "https://ghe.internal:8443/api/v3"),
        ("github://[fd00::5]/o/r", "https://[fd00::5]/api/v3"),
        ("github://github.com/o/r", "https://api.github.com"),
        ("github://GitHub.com/o/r", "https://api.github.com"),
        ("repo://WWW.GITHUB.COM/o/r", "https://api.github.com"),
        ("github:///o/r", "https://api.github.com"),
    ],
)
def test_the_api_root_comes_from_the_host_and_port_of_the_uri(text, expected):
    # A backend of ours that names no API root: the path derives it.
    class Plain(BaseRepoBackend):
        def request(self, *args, **kwargs):
            raise AssertionError("nothing is requested")

    path = URIPath(text, backend=Plain())
    assert path._core_path._api_base == expected


# --- the token ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "userinfo, token",
    [
        ("ghp_BARE123", "ghp_BARE123"),
        ("x-access-token:ghp_REAL456", "ghp_REAL456"),
        ("oauth2:glpat-REAL789", "glpat-REAL789"),
    ],
)
@pytest.mark.parametrize("scheme", ["github", "gitlab", "repo"])
def test_the_token_is_the_password_or_else_the_bare_user_and_is_never_shown(
    scheme, userinfo, token
):
    host = "gitlab.com" if scheme == "gitlab" else "github.com"
    p = URIPath(f"{scheme}://{userinfo}@{host}/acme/widgets/a.txt")
    assert p.backend.token == token
    user = userinfo.partition(":")[0]

    child = p.parent / "b.txt"
    shown = [
        str(p),
        repr(p),
        format(p),
        p.as_posix(),
        p.parent.as_posix(),
        p.as_uri(sanitize=True),
        str(child),
        repr(child),
        repr(p.source),
        str(p.source),
        repr(p.backend),
    ]
    for text in shown:
        assert token not in text and user not in text
        assert "@" not in text
    assert p.as_posix() == f"{host}:/acme/widgets/a.txt"
    # the full round trip is still available explicitly
    assert p.as_uri() == f"{scheme}://{userinfo}@{host}/acme/widgets/a.txt"


def test_the_backend_keyword_wins_over_the_userinfo():
    backend = RepoBackend(token="explicit")
    p = uri.GitHubURIPath("github://ignored@github.com/acme/widgets", backend=backend)
    assert p.backend is backend and p.backend.token == "explicit"


def test_a_uri_without_userinfo_has_no_token():
    assert URIPath("github://github.com/acme/widgets").backend.token is None


def test_the_token_reaches_the_api_as_a_bearer_and_is_in_no_message(serve_http):
    import http.server

    seen = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    token = "ghp_REAL456"
    with serve_http(_Handler) as base:
        p = URIPath(f"github://x-access-token:{token}@github.com/acme/widgets/a.txt")
        p.backend.api_base = base
        with pytest.raises(FileNotFoundError) as excinfo:
            p.read_bytes()
    assert seen[-1]["Authorization"] == f"Bearer {token}"
    assert token not in str(excinfo.value) and "x-access-token" not in str(
        excinfo.value
    )
    assert str(p) in str(excinfo.value)


def test_a_token_backend_does_not_follow_a_join_to_another_host():
    root = URIPath("github://ghp_SECRET@github.com/acme/widgets")

    # A str join stays on the original host, so the token goes nowhere new.
    literal = root / "github://evil.invalid/x/y"
    assert literal.source.host == "github.com"
    assert literal.backend.token == "ghp_SECRET"
    # A join must not build a backend (it is a pure-path operation), so a child
    # of a connectionless parent makes its own on first use.
    assert root._backend is None
    root.backend  # after this use it exists, and the next child shares it
    assert (root / "docs").backend is root.backend

    # Crossing hosts needs a URI argument, and that drops the token.
    evil = root / URIPath("github://evil.invalid/x/y")
    assert evil.source.host == "evil.invalid"
    assert evil.backend is not root.backend
    assert evil.backend.token != "ghp_SECRET"
    assert (root / "README.md").backend is root.backend


# --- the URL of the request ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "github://github.com/o/r/a/%2E%2E/%2E%2E/%2E%2E/%2E%2E/%2E%2E/user",
        "github://github.com/o/..%2F..%2Fuser%2Femails%23/x",
        "github://github.com/o%2F..%2F..%2Fnotifications%3Fall=true%23/r/a",
        "github://github.com/o/r/a%2F..%2F..%2F..%2Fuser",
        "gitlab://gitlab.com/o/r/a%2F..%2F..%2Fuser",
    ],
)
def test_a_crafted_uri_cannot_send_a_request_outside_the_repository(text):
    backend = _Recording()
    path = URIPath(text, backend=backend)
    try:
        path.stat()
    except (ValueError, OSError):
        pass
    for _method, url, _params in backend.requests:
        parts = urllib.parse.urlsplit(url)
        assert parts.query == "" and parts.fragment == ""
        assert url.startswith(f"{API}/repos/") or "/projects/" in url
        assert "." not in parts.path.split("/") and ".." not in parts.path.split("/")


def test_a_ref_is_sent_as_a_parameter_whatever_it_holds():
    backend = _Recording()
    path = URIPath("github://github.com/o/r/dir?ref=a%2Fb%3Fc%3Dd%23e", backend=backend)
    list(path.iterdir())
    method, url, params = backend.requests[0]
    assert urllib.parse.urlsplit(url).query == ""
    assert params == {"ref": "a/b?c=d#e"}


# --- what is left to the path that does the work -------------------------------------------


def test_an_error_names_the_uri_and_not_the_path_it_was_delegated_to():
    path = URIPath("repo://github.com/o/r/missing.txt", backend=_Recording())
    assert str(path._core_path).startswith("github://")
    for error in (
        FileNotFoundError(errno.ENOENT, "No such file", str(path._core_path)),
        OSError(errno.EIO, f"Unexpected API reply: {path._core_path}!"),
    ):
        with pytest.raises(OSError) as excinfo:
            with path._delegated():
                raise error
        text = str(excinfo.value)
        assert str(path) in text and "github://" not in text.replace(str(path), "")


def test_children_keep_the_kind_the_ref_and_the_backend(github_api_server):
    backend = fake_backend("github", github_api_server)
    root = uri.RepoURIPath(
        f"repo://github.com/{github_api_server[1]}/{github_api_server[2]}?ref=other-branch",
        backend=backend,
    )
    child = next(p for p in root.iterdir() if p.name == "a.txt")
    assert type(child) is uri.RepoURIPath
    assert (child.kind, child.ref, child.backend) == ("github", "other-branch", backend)
    assert child.read_bytes() == b"a-on-other-branch"


def test_a_uri_path_is_never_this_machine():
    assert URIPath("github://127.0.0.1/o/r").is_local() is False
    assert URIPath("repo://github.com/o/r").is_local() is False


@pytest.mark.parametrize(
    "text",
    [
        "repo://github.com/o/r/dir/f.txt?ref=main",
        "github://ghe.example:8443/o/r/f",
        "gitlab://gitlab.com/g/s/p/-/f",
    ],
)
def test_a_path_pickles_as_the_same_value_and_keeps_its_token(text):
    path = URIPath(text)
    again = pickle.loads(pickle.dumps(path))
    assert type(again) is type(path) and again == path
    token = URIPath(text.replace("://", "://TOKEN@", 1))
    assert pickle.loads(pickle.dumps(token)).backend.token == "TOKEN"


def test_a_tree_is_copied_to_a_local_directory(github_api_server, tmp_path):
    root = uri.GitHubURIPath(
        f"github://github.com/{github_api_server[1]}/{github_api_server[2]}",
        backend=fake_backend("github", github_api_server),
    )
    root.copy(tmp_path / "out", recursive=True)
    assert (tmp_path / "out" / "a.txt").read_text() == "a"
    assert (tmp_path / "out" / "sub" / "nested" / "d.py").read_text() == "d"


# --- the core path and its URI agree -----------------------------------------------------


@pytest.mark.parametrize(
    "core",
    [
        GitHubPath("src", "a b.py", owner="o", repo="r"),
        GitHubPath("src", owner="o", repo="r", ref="feat/x&y"),
        GitHubPath(owner="o", repo="r", instance="ghe.example:8443"),
        GitHubPath("-", "f", owner="o", repo="r"),
        GitLabPath("f", owner="g/s", repo="r"),
        GitLabPath("-", "f", owner="o", repo="r", ref="main"),
        GitLabPath("d", "-", owner="o", repo="r", instance="[fd00::5]"),
    ],
)
def test_the_uri_of_a_path_names_the_path_it_was_made_from(core):
    path = URIPath(core.as_uri())
    twin = path._core_path
    assert type(twin) is type(core)
    assert (twin.repo_owner, twin.repo, twin.ref, twin.instance) == (
        core.repo_owner,
        core.repo,
        core.ref,
        core.instance,
    )
    assert twin.repo_path == core.repo_path
    assert twin == core
    assert str(path) == core.as_uri()


def test_a_uri_path_can_be_made_from_a_path_that_needs_no_uri():
    core = GitHubPath("src", owner="o", repo="r", ref="v1", token="T")
    path = uri.GitHubURIPath(core)
    assert str(path) == core.as_uri() and path.ref == "v1"
    assert path.backend.token is None
