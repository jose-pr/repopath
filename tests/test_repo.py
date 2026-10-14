"""What a `GitHubPath` and a `GitLabPath`, and the `github:` and `gitlab:` paths
that delegate to them, do beyond the shared `ReadPathContract` wiring in
test_contract.py: ref plumbing (through `iterdir()` too), error translation
(404/401/403/rate limit), the GitHub Enterprise API-base split, GitLab's
directory-versus-file stat, listings that are cut off or hold names that are not
one component, and the read-only modes.
"""

import errno
import http.server
import json

import pytest

pytestmark = pytest.mark.usefixtures("http_transport_kind")

from http_fakes import FakeTransport, reply
from pathlib_next.http import HTTPTransport
from repo_support import github, gitlab, make_path
from repopath import BaseRepoBackend, GitHubPath, GitLabPath, RepoBackend


def _github_at(flavor, api_base, name="", **keywords):
    return make_path(
        flavor, "github", name, backend=RepoBackend(api_base=api_base), **keywords
    )


def _gitlab_at(flavor, api_base, name="", **keywords):
    return make_path(
        flavor, "gitlab", name, backend=RepoBackend(api_base=api_base), **keywords
    )


# --- owner, repository, ref and the API root (no network) -----------------------


def test_owner_repo_repo_path():
    path = GitHubPath("src", "pkg", "mod.py", owner="acme", repo="widgets")
    assert path.owner == "acme"
    assert path.repo == "widgets"
    assert path.repo_path == "src/pkg/mod.py"
    assert path.instance == "github.com"


def test_repo_path_of_a_rooted_or_trailing_spelling_is_the_same():
    spelled = ["/src/mod.py", "src/mod.py", "src//mod.py/", "./src/mod.py"]
    assert {GitHubPath(text, owner="a", repo="w").repo_path for text in spelled} == {
        "src/mod.py"
    }
    assert GitHubPath(owner="a", repo="w").repo_path == ""
    assert GitHubPath("/", owner="a", repo="w").repo_path == ""


def test_ref():
    assert GitHubPath("a.txt", owner="a", repo="w", ref="v1.2.3").ref == "v1.2.3"


def test_ref_defaults_to_none():
    assert GitHubPath("a.txt", owner="a", repo="w").ref is None


@pytest.mark.parametrize(
    "path, expected",
    [
        (GitHubPath(owner="a", repo="w"), "https://api.github.com"),
        (
            GitHubPath(owner="a", repo="w", instance="GitHub.com"),
            "https://api.github.com",
        ),
        (
            GitHubPath(owner="a", repo="w", instance="www.github.com"),
            "https://api.github.com",
        ),
        (
            GitHubPath(owner="a", repo="w", instance="ghe.internal"),
            "https://ghe.internal/api/v3",
        ),
        (
            GitHubPath(owner="o", repo="r", instance="ghe.internal:8443"),
            "https://ghe.internal:8443/api/v3",
        ),
        (
            GitHubPath(owner="o", repo="r", instance="[fd00::5]"),
            "https://[fd00::5]/api/v3",
        ),
        (GitLabPath(owner="a", repo="w"), "https://gitlab.com/api/v4"),
        (
            GitLabPath(owner="a", repo="w", instance="gitlab.example.com"),
            "https://gitlab.example.com/api/v4",
        ),
        (
            GitLabPath(owner="o", repo="r", instance="gitlab.internal:8929"),
            "https://gitlab.internal:8929/api/v4",
        ),
        (
            GitLabPath(owner="o", repo="r", instance="[fd00::5]"),
            "https://[fd00::5]/api/v4",
        ),
        (
            GitLabPath(owner="o", repo="r", instance="[fd00::5]:8443"),
            "https://[fd00::5]:8443/api/v4",
        ),
    ],
)
def test_the_api_root_is_derived_from_the_instance(path, expected):
    assert path._api_base == expected


def test_a_backend_that_names_an_api_root_replaces_the_derived_one():
    path = GitHubPath(
        owner="a", repo="w", backend=RepoBackend(api_base="http://127.0.0.1:1/x/")
    )
    assert path._api_base == "http://127.0.0.1:1/x"


def test_token_builds_the_paths_own_backend():
    path = GitHubPath("a.txt", owner="a", repo="w", token="mytoken")
    assert path.backend.token == "mytoken"
    assert "mytoken" not in repr(path.backend)


def test_a_joined_path_keeps_the_backend_it_was_built_with():
    root = GitHubPath(owner="a", repo="w", token="mytoken")
    child = root / "sub" / "a.txt"
    assert child.backend is root.backend
    assert (child.owner, child.repo, child.repo_path) == ("a", "w", "sub/a.txt")


# --- ref plumbing end-to-end, including through iterdir() ------------------


def test_github_ref_selects_branch_content(flavor, github_api_server):
    main_content = github(flavor, github_api_server, "a.txt").read_bytes()
    other_content = github(
        flavor, github_api_server, "a.txt", ref="other-branch"
    ).read_bytes()
    assert main_content == b"a"
    assert other_content == b"a-on-other-branch"


def test_github_ref_survives_iterdir_child(flavor, github_api_server):
    root = github(flavor, github_api_server, ref="other-branch")
    child = next(p for p in root.iterdir() if p.name == "a.txt")
    assert child.ref == "other-branch"
    assert child.read_bytes() == b"a-on-other-branch"


def test_github_ref_survives_nested_iterdir(flavor, github_api_server):
    root = github(flavor, github_api_server, ref="other-branch")
    sub = next(p for p in root.iterdir() if p.name == "sub")
    nested = next(p for p in sub.iterdir() if p.name == "nested")
    assert nested.ref == "other-branch"


def test_a_keyword_moves_a_joined_path_to_another_ref():
    root = GitHubPath(owner="a", repo="w", ref="v1")
    assert GitHubPath(root, "x", ref="v2").ref == "v2"
    assert GitHubPath(root, "x").ref == "v1"
    assert GitHubPath(root, "x", ref="").ref is None


# --- GitHub-specific behavior ------------------------------------------


def test_github_scandir_on_file_raises_not_a_directory(flavor, github_api_server):
    p = github(flavor, github_api_server, "a.txt")
    with pytest.raises(NotADirectoryError):
        list(p._scandir())


def test_github_open_on_directory_raises_is_a_directory(flavor, github_api_server):
    p = github(flavor, github_api_server, "sub")
    with pytest.raises(IsADirectoryError):
        p.read_bytes()


def test_github_symlink_entry_treated_as_file(flavor, serve_http):
    """A `symlink`/`submodule` contents-API entry has no special handling
    -- it's surfaced as a plain file (documented divergence)."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                [{"name": "link", "path": "link", "type": "symlink", "size": 4}]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve_http(_Handler) as base_url:
        p = _github_at(flavor, base_url, owner="acme", repo="widgets")
        entries = dict(p._scandir())
        assert not entries["link"].is_dir()
        assert entries["link"].st_size == 4


# --- GitLab-specific behavior --------------------------------------------


def test_gitlab_stat_missing_path_raises_file_not_found(flavor, gitlab_api_server):
    p = gitlab(flavor, gitlab_api_server, "nope.txt")
    with pytest.raises(FileNotFoundError):
        p.stat()


def test_gitlab_scandir_blob_entries_are_type_only_hints(flavor, gitlab_api_server):
    p = gitlab(flavor, gitlab_api_server)
    entries = dict(p._scandir())
    assert entries["a.txt"].is_file() and not entries["a.txt"].size_known
    assert entries["sub"].is_dir()


def test_gitlab_nested_directory_stat(flavor, gitlab_api_server):
    p = gitlab(flavor, gitlab_api_server, "sub/nested")
    assert p.stat().is_dir()


# --- error translation ----------------------------------------------------


@pytest.fixture
def status_server(serve_http):
    """Serves canned status codes on demand -- `/<code>` returns that
    status, `/ratelimited` returns 403 with GitHub's rate-limit headers.
    """

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.lstrip("/").split("?", 1)[0]
            first_segment = path.split("/", 1)[0]
            if first_segment == "ratelimited":
                self.send_response(403)
                self.send_header("X-RateLimit-Remaining", "0")
                self.send_header("X-RateLimit-Reset", "1234567890")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                code = int(first_segment)
            except ValueError:
                code = 404
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with serve_http(_Handler) as base_url:
        yield base_url


def test_github_404_raises_file_not_found(flavor, status_server):
    p = _github_at(flavor, f"{status_server}/404")
    with pytest.raises(FileNotFoundError):
        p.stat()


def test_github_403_without_rate_limit_headers_raises_permission_error(
    flavor, status_server
):
    p = _github_at(flavor, f"{status_server}/403")
    with pytest.raises(PermissionError):
        p.stat()


def test_github_rate_limit_raises_clear_oserror(flavor, status_server):
    p = _github_at(flavor, f"{status_server}/ratelimited")
    with pytest.raises(OSError) as excinfo:
        p.stat()
    assert excinfo.value.errno == errno.EAGAIN
    assert "rate limit" in str(excinfo.value)


def test_repo_backend_headers_merge_with_request_headers():
    transport = FakeTransport(default=reply(200))
    backend = RepoBackend(token="T", transport=transport, headers={"X-A": "1"})
    backend.request("GET", "https://h/x", headers={"Accept": "raw"})
    sent = transport.sent[0].headers
    assert sent["X-A"] == "1"
    assert sent["Accept"] == "raw"
    assert sent["Authorization"] == "Bearer T"


# --- the tree endpoint is paginated ---


def test_gitlab_listing_over_one_page_is_complete(
    flavor, gitlab_api_server, fixture_tree
):
    many = fixture_tree / "many"
    many.mkdir()
    for i in range(130):
        (many / f"f{i:03}.txt").write_text("x")
    for i in range(150):
        (fixture_tree / "pkgs" / f"pkg{i:03}").mkdir(parents=True)
        (fixture_tree / "pkgs" / f"pkg{i:03}" / "m.py").write_text("m")

    assert len(list(gitlab(flavor, gitlab_api_server, "many").iterdir())) == 130
    assert len(list(gitlab(flavor, gitlab_api_server, "many").glob("*.txt"))) == 130
    names = {p.name for p in gitlab(flavor, gitlab_api_server, "pkgs").iterdir()}
    assert names == {f"pkg{i:03}" for i in range(150)}
    assert gitlab(flavor, gitlab_api_server, "pkgs/pkg149").is_dir()
    assert gitlab(flavor, gitlab_api_server, "pkgs/pkg100").stat().is_dir()


# --- stat answers from the server, not from the shape of the path ---


def test_gitlab_root_stat_asks_the_server(flavor, gitlab_api_server):
    assert gitlab(flavor, gitlab_api_server).is_dir()
    backend = RepoBackend(api_base=f"{gitlab_api_server[0]}/api/v4")
    for owner, repo, ref in (
        ("nobody", "ghost", None),
        ("acme", "widgets", "no-such-ref"),
    ):
        p = make_path(
            flavor, "gitlab", owner=owner, repo=repo, ref=ref, backend=backend
        )
        assert not p.exists(), (owner, repo, ref)
        assert not p.is_dir(), (owner, repo, ref)


def test_gitlab_trailing_slash_directory_stat(flavor, gitlab_api_server):
    assert gitlab(flavor, gitlab_api_server, "sub/").is_dir()
    assert gitlab(flavor, gitlab_api_server, "sub/nested/").stat().is_dir()
    with pytest.raises(FileNotFoundError):
        gitlab(flavor, gitlab_api_server, "missing/").stat()


# --- a project in a subgroup, and a directory called "-" ---


def test_gitlab_subgroup_project(flavor, gitlab_api_server):
    base_url = gitlab_api_server[0]
    backend = RepoBackend(api_base=f"{base_url}/api/v4")
    p = make_path(
        flavor,
        "gitlab",
        "sub/c.py",
        owner="acme/team",
        repo="widgets",
        backend=backend,
    )
    assert (p.owner, p.repo, p.repo_path) == ("acme/team", "widgets", "sub/c.py")
    assert p.read_bytes() == b"c"
    assert p.stat().st_size == 1

    root = make_path(
        flavor, "gitlab", owner="acme/team", repo="widgets", backend=backend
    )
    assert root.is_dir()
    sub = next(c for c in root.iterdir() if c.name == "sub")
    assert sorted(c.name for c in sub.iterdir()) == ["c.py", "nested"]


def test_gitlab_directory_named_dash_stays_addressable(
    flavor, gitlab_api_server, fixture_tree
):
    (fixture_tree / "-").mkdir()
    (fixture_tree / "-" / "inside.txt").write_text("dash")
    root = gitlab(flavor, gitlab_api_server)
    dash = next(c for c in root.iterdir() if c.name == "-")
    assert dash.repo_path == "-"
    assert dash.is_dir()
    assert [c.name for c in dash.iterdir()] == ["inside.txt"]
    assert next(dash.iterdir()).read_bytes() == b"dash"


# --- the contents API stops at 1,000 entries ---


def test_github_listing_past_contents_cap_is_complete(
    flavor, github_api_server, fixture_tree
):
    for i in range(1000):
        (fixture_tree / f"r{i:04}.txt").write_text("")
    wide = fixture_tree / "sub" / "nested" / "wide"
    wide.mkdir()
    for i in range(1001):
        (wide / f"w{i:04}.txt").write_text("w")

    # The root itself is capped too, so the `sub` tree SHA comes from the
    # Git Trees API listing of the root (default branch, then a named ref).
    root_names = {p.name for p in github(flavor, github_api_server).iterdir()}
    assert len(root_names) == 1005
    assert {"sub", "a.txt"} <= root_names
    other = {
        p.name for p in github(flavor, github_api_server, ref="other-branch").iterdir()
    }
    assert other == root_names

    listing = dict(github(flavor, github_api_server, "sub/nested/wide")._scandir())
    assert len(listing) == 1001
    assert listing["w1000.txt"].st_size == 1
    assert not listing["w1000.txt"].is_dir()
    nested = dict(github(flavor, github_api_server, "sub/nested")._scandir())
    assert nested["wide"].is_dir()


# --- a tree endpoint that answers an empty 200 for a file or a missing path ---


@pytest.fixture
def gitlab_empty_listing_server(serve_http):
    """A GitLab API whose tree endpoint answers an empty 200 for any path
    that is not a directory (a file or a missing path), as `stat()` allows
    for, instead of the fake's 404."""
    import urllib.parse

    project = "/api/v4/projects/acme%2Fwidgets"
    files = {"a.txt": b"A", "pkgs/m.py": b"M"}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            split = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(split.query)
            if split.path == project:
                return self._json(200, {"default_branch": "main"})
            if split.path == f"{project}/repository/tree":
                path = qs.get("path", [""])[0]
                prefix = f"{path}/" if path else ""
                names = sorted(
                    {
                        name[len(prefix) :].split("/", 1)[0]
                        for name in files
                        if name.startswith(prefix)
                    }
                )
                return self._json(
                    200,
                    [
                        {
                            "name": name,
                            "type": "blob" if f"{prefix}{name}" in files else "tree",
                        }
                        for name in names
                    ],
                )
            files_prefix = f"{project}/repository/files/"
            if split.path.startswith(files_prefix):
                rest = split.path[len(files_prefix) :]
                raw = rest.endswith("/raw")
                name = urllib.parse.unquote(rest[: -len("/raw")] if raw else rest)
                if name not in files:
                    return self._json(404, {"message": "404 File Not Found"})
                if raw:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(files[name])))
                    self.end_headers()
                    self.wfile.write(files[name])
                    return
                return self._json(200, {"size": len(files[name])})
            self._json(404, {})

        def _json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve_http(_Handler) as base_url:
        yield base_url, "acme", "widgets"


def test_gitlab_iterdir_on_a_file_with_empty_listing_raises(
    flavor, gitlab_empty_listing_server
):
    with pytest.raises(NotADirectoryError) as excinfo:
        list(gitlab(flavor, gitlab_empty_listing_server, "a.txt").iterdir())
    assert excinfo.value.errno == errno.ENOTDIR


def test_gitlab_iterdir_on_a_missing_path_with_empty_listing_raises(
    flavor, gitlab_empty_listing_server
):
    with pytest.raises(FileNotFoundError):
        list(gitlab(flavor, gitlab_empty_listing_server, "typo_dir").iterdir())


def test_gitlab_iterdir_of_a_directory_still_lists(flavor, gitlab_empty_listing_server):
    root = gitlab(flavor, gitlab_empty_listing_server)
    assert sorted(c.name for c in root.iterdir()) == ["a.txt", "pkgs"]
    pkgs = gitlab(flavor, gitlab_empty_listing_server, "pkgs")
    assert [c.name for c in pkgs.iterdir()] == ["m.py"]


def test_gitlab_open_on_a_directory_raises_is_a_directory(flavor, gitlab_api_server):
    with pytest.raises(IsADirectoryError):
        gitlab(flavor, gitlab_api_server, "sub").read_bytes()


# --- rate limits ------------------------------------------------------------


@pytest.fixture
def rate_limit_server(serve_http):
    """`/<status>/<header>=<value>/...` answers that status with those
    headers, for any API path below it."""
    import urllib.parse

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
            self.send_response(int(parts[0]))
            for part in parts[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with serve_http(_Handler) as base_url:
        yield base_url


@pytest.mark.parametrize(
    "api, detail",
    [
        ("429/Retry-After=30", "retry after 30s"),
        ("429/X-RateLimit-Remaining=0/X-RateLimit-Reset=99", "resets at 99"),
        ("403/Retry-After=60", "retry after 60s"),
        ("403/X-RateLimit-Remaining=0/X-RateLimit-Reset=7", "resets at 7"),
        ("429", "resets at ?"),
    ],
)
def test_github_primary_and_secondary_rate_limits_raise_eagain(
    flavor, rate_limit_server, api, detail
):
    p = _github_at(flavor, f"{rate_limit_server}/{api}", "a.txt")
    with pytest.raises(BlockingIOError) as excinfo:
        p.read_bytes()
    assert excinfo.value.errno == errno.EAGAIN
    assert "rate limit" in str(excinfo.value)
    assert detail in str(excinfo.value)


def test_gitlab_429_raises_eagain(flavor, rate_limit_server):
    p = _gitlab_at(
        flavor, f"{rate_limit_server}/429/RateLimit-Remaining=0/Retry-After=5"
    )
    with pytest.raises(BlockingIOError) as excinfo:
        p.stat()
    assert excinfo.value.errno == errno.EAGAIN
    assert "retry after 5s" in str(excinfo.value)


def test_plain_403_is_still_permission_error(flavor, rate_limit_server):
    p = _gitlab_at(flavor, f"{rate_limit_server}/403")
    with pytest.raises(PermissionError):
        p.stat()


# --- a backend that is only request() ---------------------------------------


def test_gitlab_custom_backend_without_cache_reads(
    flavor, gitlab_api_server, fixture_tree
):
    import urllib.parse

    base_url, owner, repo = gitlab_api_server

    class _Plain(BaseRepoBackend):
        # Only `request()`: no `cache`, no `api_base` slot of the base class.
        def __init__(self):
            self.api_base = f"{base_url}/api/v4"
            self.urls = []

        def request(self, method, url, headers=None, params=None, **kwargs):
            self.urls.append(url)
            if params:
                url += "?" + urllib.parse.urlencode(params)
            with HTTPTransport() as transport:
                return transport.send(
                    method, url, headers=headers or {}, timeout=10, **kwargs
                )

    backend = _Plain()
    p = gitlab(flavor, gitlab_api_server, "a.txt", backend=backend)
    assert p.read_bytes() == (fixture_tree / "a.txt").read_bytes()
    assert p.read_bytes() == (fixture_tree / "a.txt").read_bytes()
    # No memoization without a cache: the default branch is asked each time.
    assert sum(url.endswith(f"/projects/{owner}%2F{repo}") for url in backend.urls) == 2


# --- the ref reaches the API whole ---


def test_ref_with_an_ampersand_reaches_the_api_whole(flavor):
    class _Recording(BaseRepoBackend):
        def __init__(self):
            self.params = []

        def request(self, method, url, params=None, **kwargs):
            self.params.append(params)
            return reply(
                200, b"branch content", {"Content-Type": "application/octet-stream"}
            )

    backend = _Recording()
    p = make_path(
        flavor,
        "github",
        "a.txt",
        owner="acme",
        repo="widgets",
        ref="feat&x",
        backend=backend,
    )
    assert p.ref == "feat&x"
    assert p.read_bytes() == b"branch content"
    assert backend.params == [{"ref": "feat&x"}]
    assert p.as_uri() == "github://github.com/acme/widgets/a.txt?ref=feat%26x"


# --- the tree is read-only ---


@pytest.mark.parametrize("mode", ["r+b", "w", "ab", "x"])
def test_github_write_modes_raise_not_implemented(flavor, github_api_server, mode):
    with pytest.raises(NotImplementedError):
        github(flavor, github_api_server, "a.txt").open(mode)


@pytest.mark.parametrize("mode", ["r+b", "w", "ab", "x"])
def test_gitlab_write_modes_raise_not_implemented(flavor, gitlab_api_server, mode):
    with pytest.raises(NotImplementedError):
        gitlab(flavor, gitlab_api_server, "a.txt").open(mode)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_read_stream_is_read_only(
    flavor, github_api_server, gitlab_api_server, provider
):
    import io

    if provider == "github":
        p = github(flavor, github_api_server, "a.txt")
    else:
        p = gitlab(flavor, gitlab_api_server, "a.txt")
    with p.open("rb") as f:
        assert not f.writable()
        with pytest.raises(io.UnsupportedOperation):
            f.write(b"patch")


@pytest.mark.parametrize("cls", [GitHubPath, GitLabPath])
def test_a_repository_path_lists_through_scandir_alone(cls):
    assert "_listdir" not in vars(cls)


# --- a listed name is one component inside the directory that listed it ------

_NOT_ONE_COMPONENT = ["..", ".", "a/b", "/abs", "", "x\0y"]


class _CannedBackend(BaseRepoBackend):
    """Answers every request with one canned directory listing."""

    def __init__(self, payload):
        self._payload = payload

    def request(self, method, url, **kwargs):
        return reply(200, json.dumps(self._payload).encode())


def test_github_listing_skips_names_that_are_not_one_component(flavor):
    payload = [
        {"name": name, "type": "dir", "size": 0, "sha": "0"}
        for name in ["sub", *_NOT_ONE_COMPONENT]
    ] + [{"name": "ok.txt", "type": "file", "size": 3, "sha": "1"}]
    root = make_path(
        flavor, "github", "dir", owner="o", repo="r", backend=_CannedBackend(payload)
    )
    listing = dict(root._scandir())
    assert sorted(listing) == ["ok.txt", "sub"]
    assert listing["sub"].is_dir()
    assert sorted(child.name for child in root.iterdir()) == ["ok.txt", "sub"]


def test_gitlab_listing_skips_names_that_are_not_one_component(flavor):
    payload = [{"name": name, "type": "tree"} for name in ["sub", *_NOT_ONE_COMPONENT]]
    payload.append({"name": "ok.txt", "type": "blob"})
    root = make_path(
        flavor, "gitlab", "dir", owner="o", repo="r", backend=_CannedBackend(payload)
    )
    listing = dict(root._scandir())
    assert sorted(listing) == ["ok.txt", "sub"]
    assert listing["sub"].is_dir()
    assert sorted(child.name for child in root.iterdir()) == ["ok.txt", "sub"]


def test_gitlab_directory_holding_only_unsafe_names_is_still_a_directory(flavor):
    payload = [{"name": "..", "type": "tree"}]
    root = make_path(
        flavor, "gitlab", "dir", owner="o", repo="r", backend=_CannedBackend(payload)
    )
    assert list(root._scandir()) == []
