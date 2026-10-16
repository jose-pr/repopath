"""`GiteaPath` against the replies recorded from a Forgejo (`tests/data/gitea`),
a served tree for what the recordings do not cover (pages, large directories),
and replies that are not the shape the API documents. Every request goes to a
loopback server."""

from __future__ import annotations

import http.server
import json
import urllib.parse

import pytest

import gitea_fakes
from repopath import BadReplyError, GiteaPath, RepoBackend, RepoPath

pytestmark = pytest.mark.usefixtures("http_transport_kind")


def recorded_path(served, *names, owner="lab", repo="pub", ref="main", **options):
    backend = options.pop("backend", None) or RepoBackend(
        api_base=f"{served.base_url}/api/v1"
    )
    return GiteaPath(
        *names, owner=owner, repo=repo, ref=ref, backend=backend, **options
    )


def api_path(server, *names, **options):
    base, owner, repo = server
    return GiteaPath(
        *names,
        owner=owner,
        repo=repo,
        backend=RepoBackend(api_base=f"{base}/api/v1"),
        **options,
    )


# --- the kind --------------------------------------------------------------------


def test_the_kind_is_registered_as_gitea_with_forgejo_as_another_name():
    assert "gitea" in RepoPath.kinds()
    assert "forgejo" not in RepoPath.kinds()
    assert type(RepoPath(owner="o", repo="r", type="gitea")) is GiteaPath
    assert type(RepoPath(owner="o", repo="r", type="forgejo")) is GiteaPath


def test_codeberg_is_detected_and_the_default_instance_is_codeberg():
    path = RepoPath("a", owner="o", repo="r", instance="codeberg.org")
    assert type(path) is GiteaPath
    assert GiteaPath(owner="o", repo="r").instance == "codeberg.org"
    assert path.kind == "gitea"


def test_another_instance_needs_a_type_and_has_its_api_under_api_v1():
    with pytest.raises(ValueError, match="gitea"):
        RepoPath("a", owner="o", repo="r", instance="git.example")
    path = RepoPath(
        "a", owner="o", repo="r", instance="git.example:3000", type="forgejo"
    )
    assert path._api_base == "https://git.example:3000/api/v1"
    assert path.as_uri() == "gitea://git.example:3000/o/r/a"


# --- what the recorded server says -------------------------------------------------


def test_stat_comes_from_the_tree_entries(gitea_recorded):
    sizes = {
        "README.md": 6,
        "empty-file": 0,
        "src/a.txt": 8,
        "src/link-to-a": 5,
        "data.lfs": 127,
    }
    for name, size in sizes.items():
        stat = recorded_path(gitea_recorded, *name.split("/")).stat()
        assert (stat.st_size, stat.is_dir()) == (size, False), name
    assert recorded_path(gitea_recorded, "src").is_dir()
    assert recorded_path(gitea_recorded).is_dir()
    routes = {
        path.partition("?")[0].rsplit("/", 2)[-2] for _, path in gitea_recorded.requests
    }
    assert routes <= {"trees"}, gitea_recorded.requests


def test_no_head_and_no_contents_request_is_made(gitea_recorded):
    path = recorded_path(gitea_recorded, "README.md")
    path.stat()
    path.read_bytes()
    assert {method for method, _ in gitea_recorded.requests} == {"GET"}
    assert not [p for _, p in gitea_recorded.requests if "/contents" in p]


def test_a_listing_names_the_entries_and_their_kinds(gitea_recorded):
    root = recorded_path(gitea_recorded)
    before = len(gitea_recorded.requests)
    stats = {child.name: child.stat() for child in root.iterdir()}
    # The listing carried the types and the sizes: it was the only request.
    assert len(gitea_recorded.requests) == before + 1
    for name in ("src", "docs", "sub", "ünï-çødé"):
        assert stats[name].is_dir(), name
    assert not stats["README.md"].is_dir()
    assert (stats["README.md"].st_size, stats["data.lfs"].st_size) == (6, 127)


def test_a_symbolic_link_is_a_file_that_reads_as_its_target(gitea_recorded):
    link = recorded_path(gitea_recorded, "src", "link-to-a")
    assert link.is_file()
    assert link.read_text() == "a.txt"
    assert link.stat().st_size == len("a.txt")


def test_a_submodule_is_an_empty_directory_that_cannot_be_opened(gitea_recorded):
    sub = recorded_path(gitea_recorded, "sub")
    assert sub.is_dir()
    assert list(sub.iterdir()) == []
    with pytest.raises(IsADirectoryError):
        sub.read_bytes()
    assert not recorded_path(gitea_recorded, "sub", "x").exists()


def test_a_file_in_lfs_reads_as_the_pointer_its_size_states(gitea_recorded):
    path = recorded_path(gitea_recorded, "data.lfs")
    data = path.read_bytes()
    assert data.startswith(b"version https://git-lfs.github.com/spec/v1\n")
    assert len(data) == path.stat().st_size == 127


def test_bytes_text_and_empty_files(gitea_recorded):
    assert recorded_path(gitea_recorded, "README.md").read_text() == "hello\n"
    assert recorded_path(gitea_recorded, "empty-file").read_bytes() == b""
    assert recorded_path(gitea_recorded, "blob.bin").read_bytes() == b"bin\x00\x01\x02"
    assert recorded_path(gitea_recorded, "run.sh").read_text().startswith("#!/bin/sh")


@pytest.mark.parametrize(
    "name, text",
    [
        ("100%.txt", "percent\n"),
        ("hash#.txt", "hash\n"),
        ("what?.txt", "question\n"),
        ("a+b.txt", "plus\n"),
        ("a&b.txt", "amp\n"),
        ('it\'s "q".txt', "quote\n"),
    ],
)
def test_names_that_need_escaping_reach_the_right_file(gitea_recorded, name, text):
    assert recorded_path(gitea_recorded, "my dir", name).read_text() == text
    names = {child.name for child in recorded_path(gitea_recorded, "my dir").iterdir()}
    assert name in names


def test_non_ascii_names_are_listed_and_read(gitea_recorded):
    directory = recorded_path(gitea_recorded, "ünï-çødé")
    assert [child.name for child in directory.iterdir()] == ["日本語.txt"]
    assert (directory / "日本語.txt").read_text() == "unicode\n"


def test_a_directory_is_not_a_file_and_a_file_is_not_a_directory(gitea_recorded):
    with pytest.raises(IsADirectoryError):
        recorded_path(gitea_recorded, "src").read_bytes()
    with pytest.raises(NotADirectoryError):
        list(recorded_path(gitea_recorded, "README.md").iterdir())
    with pytest.raises(IsADirectoryError):
        recorded_path(gitea_recorded).read_bytes()


def test_a_missing_path_is_file_not_found(gitea_recorded):
    for names in (("nope.txt",), ("src", "nope"), ("README.md", "below")):
        path = recorded_path(gitea_recorded, *names)
        assert not path.exists()
        with pytest.raises(FileNotFoundError):
            path.stat()
    with pytest.raises(FileNotFoundError):
        recorded_path(gitea_recorded, "nope.txt").read_bytes()


def test_a_ref_with_a_slash_is_encoded_in_the_tree_url_and_not_in_raw(gitea_recorded):
    path = recorded_path(gitea_recorded, "src", "a.txt", ref="feat/x")
    assert path.read_text() == "one\ntwo on other\n"
    names = {
        child.name for child in recorded_path(gitea_recorded, ref="feat/x").iterdir()
    }
    assert "src" in names
    assert any("/git/trees/feat%2Fx" in p for _, p in gitea_recorded.requests)


def test_a_tag_annotated_or_not_is_a_ref(gitea_recorded):
    assert (
        recorded_path(gitea_recorded, "src", "a.txt", ref="v2").read_text()
        == "one\ntwo\n"
    )
    assert recorded_path(gitea_recorded, "README.md", ref="v1").is_file()


def test_a_ref_that_is_not_there_is_not_found(gitea_recorded):
    path = recorded_path(gitea_recorded, "README.md", ref="nope")
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        path.stat()
    assert not recorded_path(gitea_recorded, ref="nope").exists()
    with pytest.raises(FileNotFoundError):
        list(recorded_path(gitea_recorded, ref="nope").iterdir())
    with pytest.raises(FileNotFoundError):
        recorded_path(gitea_recorded, "README.md", ref="nope").read_bytes()


def test_a_repository_without_a_commit_has_no_tree(gitea_recorded):
    root = recorded_path(gitea_recorded, repo="empty", ref=None)
    assert not root.exists()
    with pytest.raises(FileNotFoundError):
        list(root.iterdir())


def test_the_default_branch_is_asked_once_for_the_backend(gitea_recorded):
    backend = RepoBackend(api_base=f"{gitea_recorded.base_url}/api/v1")
    first = recorded_path(gitea_recorded, "README.md", ref=None, backend=backend)
    second = recorded_path(gitea_recorded, "docs", ref=None, backend=backend)
    assert first.is_file() and second.is_dir()
    repo = [p for _, p in gitea_recorded.requests if p == "/api/v1/repos/lab/pub"]
    assert len(repo) == 1


def test_a_private_repository_is_not_found_without_a_token(gitea_recorded):
    root = recorded_path(gitea_recorded, repo="priv")
    assert not root.exists()
    with pytest.raises(FileNotFoundError):
        recorded_path(gitea_recorded, "secret.txt", repo="priv").read_bytes()


def test_a_token_is_sent_as_a_bearer_and_reads_the_private_tree(gitea_recorded):
    backend = RepoBackend(token="a-token", api_base=f"{gitea_recorded.base_url}/api/v1")
    path = recorded_path(gitea_recorded, "secret.txt", repo="priv", backend=backend)
    assert path.read_text() == "secret\n"
    assert path.stat().st_size == 7
    assert set(gitea_recorded.authorizations) == {"Bearer a-token"}


def test_a_wrong_token_is_permission_denied(gitea_recorded):
    backend = RepoBackend(
        token=gitea_fakes.WRONG_TOKEN, api_base=f"{gitea_recorded.base_url}/api/v1"
    )
    with pytest.raises(PermissionError):
        recorded_path(gitea_recorded, "README.md", backend=backend).stat()


# --- a served tree: pages and large directories ---------------------------------


@pytest.fixture
def pages():
    return []


@pytest.fixture
def big_server(fixture_tree, pages):
    (fixture_tree / "big").mkdir()
    for index in range(2100):
        (fixture_tree / "big" / f"file-{index:04d}.txt").write_bytes(
            f"{index}\n".encode()
        )
    with gitea_fakes.gitea_api(fixture_tree, pages=pages) as served:
        yield served


def test_a_large_directory_is_listed_page_after_page(big_server, pages):
    names = [child.name for child in api_path(big_server, "big").iterdir()]
    assert names == [f"file-{index:04d}.txt" for index in range(2100)]
    # The tree is asked for by the name of the branch, then the directory by id.
    assert [(page, per_page) for _, page, per_page in pages[-3:]] == [
        (1, 1000),
        (2, 1000),
        (3, 1000),
    ]


def test_the_truncated_flag_of_a_last_page_does_not_ask_for_another(big_server, pages):
    list(api_path(big_server, "big").iterdir())
    last = pages[-1]
    assert last[1] == 3
    assert not any(page > 3 for _, page, _ in pages)


def test_a_file_in_a_large_directory_is_found_and_read(big_server):
    path = api_path(big_server, "big", "file-2050.txt")
    assert path.stat().st_size == len("2050\n")
    assert path.read_text() == "2050\n"
    assert not api_path(big_server, "big", "file-9999.txt").exists()


def test_the_listing_hints_make_a_stat_of_a_child_free(big_server, pages):
    children = list(api_path(big_server, "sub").iterdir())
    asked = len(pages)
    assert all(child.stat().st_size >= 0 for child in children)
    assert len(pages) == asked


def test_a_nested_stat_walks_the_trees_from_the_root(big_server, pages):
    api_path(big_server, "sub", "nested", "d.py").stat()
    # The root by branch name, `sub` by its id, `nested` by its id.
    assert len(pages) == 3
    assert pages[0][0] == "main"
    assert all(page == 1 for _, page, _ in pages)


def test_the_ref_names_the_root_tree(big_server, pages):
    path = api_path(big_server, "a.txt", ref="other-branch")
    assert path.stat().is_dir() is False
    assert path.read_bytes() == b"a-on-other-branch"
    assert pages[0][0] == "other-branch"


def test_a_path_with_a_dot_segment_is_refused_before_a_request(big_server, pages):
    with pytest.raises(ValueError, match="dot segment"):
        api_path(big_server, "sub", "..", "a.txt").read_bytes()
    assert not pages


# --- replies that are not the documented shape -----------------------------------


class _Api:
    def __init__(self):
        self.log = []
        self.route = lambda path: (404, {}, b"{}")


def _json(payload, status=200, headers=None):
    body = json.dumps(payload).encode()
    return status, {"Content-Type": "application/json", **(headers or {})}, body


@pytest.fixture
def api(serve_http):
    api = _Api()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            api.log.append(self.path)
            status, headers, body = api.route(self.path)
            self.send_response(status)
            for key, value in {"Content-Length": str(len(body)), **headers}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    with serve_http(Handler) as base:
        api.base = base
        yield api


def reply_path(api, *names):
    return GiteaPath(
        *names,
        owner="o",
        repo="r",
        ref="main",
        backend=RepoBackend(api_base=f"{api.base}/api/v1"),
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "tree",
        {"tree": None},
        {"tree": {}},
        {"tree": [None]},
        {"tree": [{"path": "a"}]},
        {"tree": [{"type": "blob"}]},
        {"tree": [{"path": 3, "type": "blob"}]},
        {"tree": [{"path": "a", "type": "blob", "size": -1}]},
        {"tree": [{"path": "a", "type": "blob", "size": "9"}]},
        {"tree": [{"path": "a", "type": "blob", "size": True}]},
    ],
)
def test_a_tree_that_is_not_a_tree_is_eio(api, payload):
    api.route = lambda path: _json(payload)
    with pytest.raises(BadReplyError) as caught:
        list(reply_path(api).iterdir())
    assert caught.value.errno == 5
    assert not reply_path(api, "a").exists()


def test_a_reply_that_is_not_json_is_eio(api):
    api.route = lambda path: (
        200,
        {"Content-Type": "text/html"},
        b"<html>portal</html>",
    )
    with pytest.raises(BadReplyError):
        reply_path(api).stat()
    assert not reply_path(api, "a").exists()


def test_a_repository_reply_without_a_default_branch_is_eio(api):
    api.route = lambda path: _json({"name": "r"})
    with pytest.raises(BadReplyError):
        GiteaPath(
            owner="o", repo="r", backend=RepoBackend(api_base=f"{api.base}/api/v1")
        ).stat()


def test_a_tree_entry_that_is_not_a_tree_to_walk_through_is_not_found(api):
    api.route = lambda path: _json(
        {"tree": [{"path": "d", "type": "tree", "size": 0}], "total_count": 1}
    )
    with pytest.raises(BadReplyError):
        reply_path(api, "d", "x").stat()


def test_names_that_are_not_one_component_are_left_out(api):
    entries = [
        {"path": name, "type": "blob", "size": 1, "sha": "0" * 40}
        for name in ("ok", "a/b", "..", ".", "", "nul\x00")
    ]
    api.route = lambda path: _json({"tree": entries, "total_count": len(entries)})
    assert [child.name for child in reply_path(api).iterdir()] == ["ok"]


def test_a_reply_without_a_total_count_ends_at_an_empty_page(api):
    def route(path):
        page = int(urllib.parse.parse_qs(path.partition("?")[2])["page"][0])
        entries = [{"path": f"f{page}", "type": "blob", "size": 1}] if page < 3 else []
        return _json({"tree": entries})

    api.route = route
    assert [child.name for child in reply_path(api).iterdir()] == ["f1", "f2"]
    assert len(api.log) == 3


def test_a_server_error_is_eio_and_a_limit_is_a_rate_limit_error(api):
    from repopath import RateLimitError

    api.route = lambda path: (500, {}, b"boom")
    with pytest.raises(OSError) as caught:
        reply_path(api).stat()
    assert caught.value.errno == 5
    api.route = lambda path: _json({}, 429, {"Retry-After": "7"})
    with pytest.raises(RateLimitError):
        reply_path(api, "a").stat()


def test_an_html_error_page_from_raw_does_not_hide_a_missing_file(api):
    def route(path):
        if "/raw/" in path:
            return 404, {"Content-Type": "text/html"}, b"<html>no</html>"
        return _json({"tree": [], "total_count": 0})

    api.route = route
    with pytest.raises(FileNotFoundError):
        reply_path(api, "a").read_bytes()


def test_writing_is_not_supported(gitea_recorded):
    path = recorded_path(gitea_recorded, "README.md")
    with pytest.raises(NotImplementedError):
        path.open("w")
    with pytest.raises(NotImplementedError):
        path.write_text("x")


# --- the gitea: scheme and repo:// on codeberg.org ----------------------------------


def uri_path(served, text, **options):
    pytest.importorskip("uripath")
    from uripath import URIPath

    backend = RepoBackend(api_base=f"{served.base_url}/api/v1", **options)
    return URIPath(text, backend=backend)


def test_a_gitea_uri_reads_the_tree_of_its_repository(gitea_recorded):
    from repopath import uri

    root = uri_path(gitea_recorded, "gitea://git.example:3000/lab/pub?ref=main")
    assert type(root) is uri.GiteaURIPath
    assert (root.kind, root.instance, root.repo_owner, root.repo) == (
        "gitea",
        "git.example:3000",
        "lab",
        "pub",
    )
    assert root.ref == "main"
    readme = root / "README.md"
    assert readme.repo_path == "README.md"
    assert readme.read_text() == "hello\n"
    assert readme.stat().st_size == 6
    assert {child.name for child in root.iterdir()} >= {"src", "docs", "sub"}
    assert (root / "sub").is_dir()


def test_a_ref_with_a_slash_travels_in_the_query(gitea_recorded):
    path = uri_path(gitea_recorded, "gitea://h/lab/pub/src/a.txt?ref=feat%2Fx")
    assert path.ref == "feat/x"
    assert path.read_text() == "one\ntwo on other\n"
    assert path.as_uri().endswith("/lab/pub/src/a.txt?ref=feat%2Fx")


def test_the_core_path_of_a_uri_is_a_gitea_path(gitea_recorded):
    path = uri_path(gitea_recorded, "gitea://h/lab/pub/README.md?ref=main")
    core = path._core_path
    assert type(core) is GiteaPath
    assert core.as_uri() == "gitea://h/lab/pub/README.md?ref=main"


def test_repo_on_codeberg_detects_the_kind(gitea_recorded):
    path = uri_path(gitea_recorded, "repo://codeberg.org/lab/pub/README.md?ref=main")
    assert path.kind == "gitea"
    assert path.read_text() == "hello\n"


def test_the_token_of_a_gitea_uri_is_sent_and_never_shown(gitea_recorded):
    from repopath import uri

    text = "gitea://tok_SECRET@h/lab/priv/secret.txt?ref=main"

    path = uri.GiteaURIPath(text)
    assert path.backend.token == "tok_SECRET"
    assert "tok_SECRET" not in repr(path) + str(path) + path.as_uri(sanitize=True)
    path = uri.GiteaURIPath(
        text,
        backend=RepoBackend(
            token="tok_SECRET", api_base=f"{gitea_recorded.base_url}/api/v1"
        ),
    )
    assert path.read_text() == "secret\n"
    assert set(gitea_recorded.authorizations) == {"Bearer tok_SECRET"}


def test_a_gitea_uri_without_a_repository_does_not_exist():
    pytest.importorskip("uripath")
    from uripath import URIPath

    for text in ("gitea://codeberg.org", "gitea://codeberg.org/lab"):
        path = URIPath(text)
        assert not path.exists()
