"""`SourceForgePath` against loopback fakes of the REST API of SourceForge and of
the Subversion server its tools are served by. Nothing reaches a real host."""

import json
import pickle

import pytest

import svn_fakes
from http_fakes import FakeTransport, reply
from pathapi.testing import ReadPathContract
from repopath import (
    BadReplyError,
    RepoBackend,
    RepoPath,
    SourceForgePath,
    SVNPath,
)
from svn_fakes import SourceForged


class _Site:
    """The REST fake of a site and the Subversion fake its tool is served by."""

    def __init__(self, api, svn, project="proj", mount="code", base="/svn/open/trunk"):
        self.api, self.svn = api, svn
        self.project, self.mount = project, mount
        self.backend = RepoBackend(api_base=api.base_url)
        self.clone = svn.base_url + base

    def path(self, *names, **options):
        options.setdefault("project", self.project)
        options.setdefault("backend", self.backend)
        return SourceForgePath(*names, **options)


@pytest.fixture
def site(svn_recorded):
    """A project `proj` whose `code` tool is the Subversion `trunk` of the
    recordings."""
    tools = {
        ("proj", "code"): SourceForged("svn", svn_recorded.base_url + "/svn/open/trunk")
    }
    with svn_fakes.sourceforge_api(tools) as api:
        yield _Site(api, svn_recorded)


def _tools(**tools):
    return {("proj", mount): SourceForged(*value) for mount, value in tools.items()}


# -- the contract -----------------------------------------------------------


class TestSourceForgePathContract(ReadPathContract):
    @pytest.fixture
    def root(self, svn_fixture_server, http_transport_kind):
        clone = svn_fixture_server.base_url + "/svn/fixture"
        with svn_fakes.sourceforge_api(_tools(code=("svn", clone))) as api:
            yield SourceForgePath(
                project="proj", backend=RepoBackend(api_base=api.base_url)
            )


# -- reading ----------------------------------------------------------------


def test_a_project_is_listed_and_read_as_the_subversion_tree_of_its_tool(site):
    root = site.path()

    names = sorted(c.name for c in root.iterdir())

    assert names[:3] == ["README", "big", "blob.bin"]
    assert (root / "README").read_text() == "hello\n"
    assert (root / "src" / "a.txt").stat().st_size == 8
    assert (root / "README").checksum() == "b1946ac92492d2347c6235b4d2611184"
    assert isinstance(root, SVNPath) and root.kind == "sourceforge"
    assert site.svn.count("PROPFIND", "/svn/open/trunk/") == 4


def test_the_tool_is_asked_once_per_backend(site):
    root = site.path()

    root.is_dir()
    list(root.iterdir())
    (root / "README").read_bytes()
    site.path("README").stat()

    assert site.api.requests == [("GET", "/rest/p/proj/code/", None, None)]


def test_a_revision_reads_through_the_stub_of_the_repository(site):
    old = site.path("src", "a.txt", ref=2)

    assert old.read_text() == "one\ntwo\n"
    assert not site.path("src", "b.txt", ref=3).exists()
    assert site.path("src", "a.txt", ref=1).read_text() == "one\n"
    assert [r[1] for r in site.svn.requests if r[0] == "OPTIONS"] == [
        "/svn/open/trunk/"
    ]


def test_the_rest_address_is_the_site_and_the_names_of_the_project_and_tool():
    clone = "https://svn.code.sf.net/p/docutils/code/trunk"
    document = svn_fakes.records("public")
    tool = next(r for r in document.values() if r.label.startswith("16_"))
    listing = next(r for r in document.values() if r.label.startswith("03_sfsvn"))
    transport = FakeTransport(
        {
            ("GET", "https://sourceforge.net/rest/p/docutils/code/"): reply(
                200, tool.content, {"Content-Type": "application/json"}
            ),
            ("PROPFIND", clone + "/docutils", "0"): reply(207, listing.content),
        }
    )
    root = SourceForgePath(
        "docutils", project="docutils", backend=RepoBackend(transport=transport)
    )

    assert root.is_dir()
    assert [(s.method, s.url) for s in transport.sent] == [
        ("GET", "https://sourceforge.net/rest/p/docutils/code/"),
        ("PROPFIND", clone + "/docutils"),
    ]


def test_another_site_and_another_mount_are_in_the_rest_address(site):
    other = SourceForgePath(
        project="p-1", mount="svn.2", instance="forge.example:8443",
        backend=RepoBackend(transport=FakeTransport()),
    )  # fmt: skip

    assert other._rest_url() == "https://forge.example:8443/rest/p/p-1/svn.2/"


# -- what the tool is -------------------------------------------------------


def test_a_tool_that_is_not_subversion_is_not_implemented():
    tools = _tools(git=("git", "https://git.example/p/proj/git"), hg=("hg", None))
    with svn_fakes.sourceforge_api(tools) as api:
        backend = RepoBackend(api_base=api.base_url)
        for mount, kind in (("git", "git"), ("hg", "hg")):
            path = SourceForgePath("x", project="proj", mount=mount, backend=backend)
            with pytest.raises(NotImplementedError, match=kind):
                path.stat()
            with pytest.raises(NotImplementedError, match=kind):
                list(path.iterdir())


def test_a_project_or_tool_that_does_not_exist_is_missing():
    with svn_fakes.sourceforge_api(_tools(code=("svn", "http://127.0.0.1:1/s"))) as api:
        backend = RepoBackend(api_base=api.base_url)
        for options in (
            {"project": "no-such-project"},
            {"project": "proj", "mount": "no-such-tool"},
        ):
            path = SourceForgePath("x", backend=backend, **options)
            assert not path.exists()
            with pytest.raises(FileNotFoundError):
                path.stat()
            with pytest.raises(FileNotFoundError):
                path.read_bytes()


@pytest.mark.parametrize(
    "tool",
    [
        {"name": "svn"},
        {"name": "svn", "clone_url_https_anon": 3},
        {"name": "svn", "clone_url_https_anon": "svn://svn.example/p/proj/code"},
        {"name": "svn", "clone_url_https_anon": "https://user:pw@svn.example/p/proj"},
        {"name": "svn", "clone_url_https_anon": "https://svn.example/p?x=1"},
        {"name": 3},
        {},
        [],
        "svn",
    ],
)
def test_a_reply_that_is_not_a_subversion_tool_is_a_bad_reply(tool):
    from api_fakes import serve
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps(tool).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with serve(Handler) as base:
        path = SourceForgePath("x", project="proj", backend=RepoBackend(api_base=base))
        with pytest.raises(BadReplyError):
            path.stat()
        assert not path.exists()


def test_a_page_that_is_not_json_is_a_bad_reply():
    from api_fakes import serve
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "13")
            self.end_headers()
            self.wfile.write(b"<html></html>")

    with serve(Handler) as base:
        path = SourceForgePath("x", project="proj", backend=RepoBackend(api_base=base))
        with pytest.raises(BadReplyError):
            path.stat()


def test_a_redirect_of_the_rest_address_is_not_followed():
    from api_fakes import serve
    import http.server

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append(self.path)
            self.send_response(301)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    with serve(Handler) as base:
        path = SourceForgePath("x", project="proj", backend=RepoBackend(api_base=base))
        with pytest.raises(BadReplyError):
            path.stat()
    assert seen == ["/rest/p/proj/code/"]


# -- the place --------------------------------------------------------------


def test_the_place_is_checked():
    with pytest.raises(TypeError):
        SourceForgePath("x")
    with pytest.raises(TypeError):
        SourceForgePath(project=3)
    for bad in ("", ".", "..", "a/b", "a b", "-a", "a?b", "a#b", "ü"):
        with pytest.raises(ValueError):
            SourceForgePath(project=bad)
        with pytest.raises(ValueError):
            SourceForgePath(project="p", mount=bad)
    with pytest.raises(ValueError):
        SourceForgePath(project="p", instance="a/b")
    with pytest.raises(TypeError):
        SourceForgePath(project="p", username="u", password="p")


def test_a_call_of_repopath_makes_a_sourceforge_path():
    path = RepoPath("trunk", type="sourceforge", project="docutils")
    by_instance = RepoPath("trunk", instance="www.SourceForge.net", project="docutils")

    assert type(path) is SourceForgePath and path.kind == "sourceforge"
    assert (path.project, path.mount, path.instance) == (
        "docutils",
        "code",
        "sourceforge.net",
    )
    assert type(by_instance) is SourceForgePath
    assert "sourceforge" in RepoPath.kinds()


def test_equality_text_and_pickle():
    a = SourceForgePath("x", project="p", instance="SourceForge.net")
    b = SourceForgePath("/x", project="p", backend=RepoBackend())

    assert a == b and hash(a) == hash(b)
    assert a != SourceForgePath("x", project="q")
    assert a != SourceForgePath("x", project="p", mount="svn")
    assert a != SourceForgePath("x", project="p", ref=2)
    assert str(a) == a.as_uri() == "sourceforge://p/x"
    assert SourceForgePath("a b", project="p", mount="svn", ref=7).as_uri() == (
        "sourceforge://p/a%20b?mount=svn&ref=7"
    )
    assert SourceForgePath(project="p", instance="forge.example").as_uri() == (
        "sourceforge://p?instance=forge.example"
    )
    assert repr(a) == "SourceForgePath('x', project='p')"
    assert repr(
        SourceForgePath(project="p", mount="svn", ref=2, instance="f.example")
    ) == (
        "SourceForgePath('', project='p', mount='svn', ref='2', instance='f.example')"
    )
    assert pickle.loads(pickle.dumps(a)) == a
    assert a != SVNPath("x", instance="https://sourceforge.net/p")


def test_a_joined_path_keeps_the_place_and_the_backend():
    backend = RepoBackend()
    root = SourceForgePath(project="p", mount="svn", ref=4, backend=backend)

    child = root / "trunk" / "x.txt"

    assert child.backend is backend and child.ref == "4"
    assert (child.project, child.mount) == ("p", "svn")
    assert SourceForgePath(child, "y").mount == "svn"
    assert SourceForgePath(child, ref="").ref is None
    with pytest.raises(NotImplementedError):
        SourceForgePath(SVNPath(instance="https://h/s"), project="p")


def test_nothing_is_written(site):
    path = site.path("README")

    with pytest.raises(NotImplementedError):
        path.write_text("x")
    with pytest.raises(NotImplementedError):
        path.unlink()
    assert site.api.requests == []
