"""The `svn+https:`, `svn+http:` and `sourceforge:` schemes: which class a URI
gets, how its parts become the place of the path it delegates to, where the
credentials come from and what never shows them, and what the URI paths leave
to that path (see test_svn.py and test_sourceforge.py for what it does)."""

from __future__ import annotations

import pickle

import pytest

uripath = pytest.importorskip("uripath")

import svn_fakes
from pathapi.protocols import NativeChecksum, Readlink, supports
from pathapi.testing import ReadPathContract
from repopath import RepoBackend, SourceForgePath, SVNPath, uri
from svn_fakes import SourceForged
from uripath import URIPath
from uripath.exceptions import URISyntaxError


def at(served, text="", base="/svn/open", scheme="svn+http", **options):
    host = served.base_url.partition("//")[2]
    return URIPath(f"{scheme}://{host}{base}{text}", **options)


def asked_get(served):
    return [r for r in served.requests if r[0] == "GET"]


# -- the contract -----------------------------------------------------------


class TestSVNURIPathContract(ReadPathContract):
    @pytest.fixture
    def root(self, svn_fixture_server, http_transport_kind):
        return at(svn_fixture_server, base="/svn/fixture")


class TestSourceForgeURIPathContract(ReadPathContract):
    @pytest.fixture
    def root(self, svn_fixture_server, http_transport_kind):
        clone = svn_fixture_server.base_url + "/svn/fixture"
        tools = {("proj", "code"): SourceForged("svn", clone)}
        with svn_fakes.sourceforge_api(tools) as api:
            yield URIPath(
                "sourceforge://proj", backend=RepoBackend(api_base=api.base_url)
            )


# -- which class a URI gets -------------------------------------------------


@pytest.mark.parametrize(
    "text, cls",
    [
        ("svn+https://svn.example.org/repos/p/trunk", uri.SVNURIPath),
        ("svn+http://127.0.0.1:8080/svn/p", uri.SVNURIPath),
        ("SVN+HTTPS://svn.example.org/repos/p", uri.SVNURIPath),
        ("sourceforge://docutils/trunk", uri.SourceForgeURIPath),
        ("sourceforge://docutils", uri.SourceForgeURIPath),
    ],
)
def test_a_uri_gets_the_class_of_its_scheme(text, cls):
    path = URIPath(text)

    assert type(path) is cls
    assert path.is_local() is False


def test_the_schemes_are_registered_by_the_entry_points():
    schemes = URIPath.schemes()

    assert {"svn+https", "svn+http", "sourceforge"} <= set(schemes)
    assert "svn" not in schemes
    assert URIPath.scheme_class("svn+https") is uri.SVNURIPath
    assert URIPath.scheme_class("sourceforge") is uri.SourceForgeURIPath


def test_the_parts_become_the_place_of_the_core_path():
    path = URIPath("svn+https://svn.example.org:8443/repos/p/tr%20unk/a%20b?ref=3")
    core = path._core_path

    assert isinstance(core, SVNPath)
    assert core.instance == "https://svn.example.org:8443"
    assert core.repo_path == "repos/p/tr unk/a b"
    assert core.ref == "3" and path.ref == "3" and path.kind == "svn"


@pytest.mark.parametrize(
    "text",
    [
        "svn+https:///repos/p",
        "sourceforge:///docutils",
        "sourceforge://u@docutils/x",
        "sourceforge://u:p@docutils/x",
        "sourceforge://docutils:8080/x",
    ],
)
def test_a_uri_that_names_no_usable_place_is_refused_when_it_is_built(text):
    with pytest.raises(URISyntaxError):
        URIPath(text)


@pytest.mark.parametrize(
    "text",
    [
        "svn+http://h/a?ref=x",
        "sourceforge://docutils/x?mount=a/b",
        "sourceforge://docutils/x?ref=-1",
    ],
)
def test_a_uri_whose_place_is_wrong_is_refused_when_it_is_first_used(text):
    path = URIPath(text)

    with pytest.raises(URISyntaxError):
        path._core_path


def test_the_place_of_a_sourceforge_uri():
    path = URIPath(
        "sourceforge://DocUtils/trunk/README?mount=svn&ref=4&instance=forge.example"
    )
    core = path._core_path

    assert isinstance(core, SourceForgePath)
    assert (core.project, core.mount, core.ref, core.instance) == (
        "docutils",
        "svn",
        "4",
        "forge.example",
    )
    assert core.repo_path == "trunk/README"
    assert (path.project, path.mount, path.kind) == ("docutils", "svn", "sourceforge")
    plain = URIPath("sourceforge://docutils")._core_path
    assert (plain.mount, plain.instance, plain.ref) == ("code", "sourceforge.net", None)


# -- reading ----------------------------------------------------------------


def test_a_svn_uri_lists_reads_and_stats(svn_recorded):
    root = at(svn_recorded, "/trunk")

    assert root.is_dir()
    assert sorted(c.name for c in root.iterdir())[:3] == ["README", "big", "blob.bin"]
    assert (root / "README").read_text() == "hello\n"
    assert (root / "README").stat().st_size == 6
    assert not (root / "no-such").exists()
    assert (root / "my dir" / "a+b.txt").read_text() == "plus\n"
    assert type(root / "README") is uri.SVNURIPath


def test_a_revision_is_carried_to_every_child(svn_recorded):
    old = at(svn_recorded, "/trunk/src?ref=2")

    assert sorted(c.name for c in old.iterdir()) == ["a.txt", "b.txt"]
    assert (old / "a.txt").read_text() == "one\ntwo\n"
    assert (old / "a.txt").ref == "2"
    # The repository root, found once, serves every path below it that shares
    # the backend.
    assert len([r for r in svn_recorded.requests if r[0] == "OPTIONS"]) == 1
    assert at(svn_recorded, "/trunk/src/a.txt?ref=1").read_text() == "one\n"
    assert not at(svn_recorded, "/trunk/src/b.txt?ref=3").exists()


def test_an_error_names_the_uri_and_not_the_path_it_was_delegated_to(svn_recorded):
    missing = at(svn_recorded, "/trunk/no-such")

    with pytest.raises(FileNotFoundError) as error:
        missing.read_bytes()
    assert error.value.filename == str(missing)
    assert "svn+http://" in str(error.value)
    with pytest.raises(NotADirectoryError) as error:
        list(at(svn_recorded, "/trunk/README").iterdir())
    assert error.value.filename == str(at(svn_recorded, "/trunk/README"))


def test_links_and_digests_are_the_core_paths(svn_recorded):
    link = at(svn_recorded, "/trunk/src/link-to-a")

    assert link.is_symlink() and link.read_text() == "one\ntwo\n"
    assert link.readlink().path == "a.txt"
    assert at(svn_recorded, "/trunk/src/link-absolute").readlink().path == "/etc/passwd"
    readme = at(svn_recorded, "/trunk/README")
    reads = len(asked_get(svn_recorded))
    assert readme.checksum() == "b1946ac92492d2347c6235b4d2611184"
    assert readme.supported_checksums() == {"md5", "sha1"}
    assert supports(readme, NativeChecksum) and supports(readme, Readlink)
    assert len(asked_get(svn_recorded)) == reads
    with pytest.raises(NotImplementedError):
        link.checksum()


def test_a_listing_hands_each_child_its_stat(svn_recorded):
    children = {c.name: c for c in at(svn_recorded, "/trunk").iterdir()}
    before = len(svn_recorded.requests)

    assert children["README"].is_file()
    assert children["src"].is_dir()

    assert len(svn_recorded.requests) == before


# -- credentials ------------------------------------------------------------

_PASSWORD = svn_fakes.USERS["alice"]


def test_a_user_and_password_in_the_uri_are_sent_as_basic(svn_recorded):
    host = svn_recorded.base_url.partition("//")[2]
    path = URIPath(f"svn+http://alice:{_PASSWORD}@{host}/svn/locked/trunk/README")

    assert path.read_text() == "hello\n"
    assert {r[3] for r in svn_recorded.requests} == {"alice"}
    assert URIPath(f"svn+http://alice:wrong@{host}/svn/locked/trunk").exists() is False
    assert URIPath(f"svn+http://{host}/svn/locked/trunk").exists() is False


def test_no_display_form_and_no_error_shows_the_password(svn_recorded):
    host = svn_recorded.base_url.partition("//")[2]
    path = URIPath(f"svn+http://alice:{_PASSWORD}@{host}/svn/locked/trunk/no-such")

    shown = [str(path), repr(path), path.as_posix(), path.as_uri(sanitize=True)]
    shown += [str(path.source), repr(path.source)]
    with pytest.raises(FileNotFoundError) as error:
        path.read_bytes()
    shown += [str(error.value), repr(error.value), *map(str, error.value.args)]
    assert all(_PASSWORD not in text for text in shown), shown
    assert _PASSWORD in path.as_uri()


def test_a_backend_wins_over_the_uri(svn_recorded):
    backend = RepoBackend(auth=("alice", _PASSWORD))
    path = at(svn_recorded, "/trunk/README", base="/svn/locked", backend=backend)

    assert path.read_text() == "hello\n"
    assert path.backend is backend


def test_a_sourceforge_uri_sends_no_credential(svn_recorded):
    tools = {
        ("proj", "code"): SourceForged("svn", svn_recorded.base_url + "/svn/open/trunk")
    }
    with svn_fakes.sourceforge_api(tools) as api:
        path = URIPath(
            "sourceforge://proj/README", backend=RepoBackend(api_base=api.base_url)
        )
        assert path.read_text() == "hello\n"
    assert {r[3] for r in svn_recorded.requests} == {None}


# -- sourceforge ------------------------------------------------------------


def test_a_sourceforge_uri_reads_the_tool_of_the_project(svn_recorded):
    tools = {
        ("proj", "code"): SourceForged(
            "svn", svn_recorded.base_url + "/svn/open/trunk"
        ),
        ("proj", "git"): SourceForged("git", "https://git.example/p/proj/git"),
    }
    with svn_fakes.sourceforge_api(tools) as api:
        backend = RepoBackend(api_base=api.base_url)
        root = URIPath("sourceforge://proj", backend=backend)

        assert sorted(c.name for c in root.iterdir())[:2] == ["README", "big"]
        assert (root / "src" / "a.txt").read_text() == "one\ntwo\n"
        old = URIPath("sourceforge://proj/src/a.txt?ref=1", backend=backend)
        assert old.read_text() == "one\n"
        assert not URIPath("sourceforge://nobody/x", backend=backend).exists()
        with pytest.raises(NotImplementedError, match="git"):
            URIPath("sourceforge://proj/x?mount=git", backend=backend).stat()
        assert len(api.requests) == 3


def test_an_error_of_a_sourceforge_uri_names_the_uri():
    with svn_fakes.sourceforge_api({}) as api:
        path = URIPath(
            "sourceforge://nobody/x", backend=RepoBackend(api_base=api.base_url)
        )

        with pytest.raises(FileNotFoundError) as error:
            path.read_bytes()
    assert error.value.filename == str(path) == "sourceforge://nobody/x"


# -- copies -----------------------------------------------------------------


def test_a_pickle_and_a_copy_keep_the_uri_and_the_revision():
    path = URIPath("svn+https://svn.example.org/repos/p/trunk?ref=3")

    assert pickle.loads(pickle.dumps(path)) == path
    assert pickle.loads(pickle.dumps(path)).ref == "3"
    forge = URIPath("sourceforge://docutils/a b?mount=svn")
    assert pickle.loads(pickle.dumps(forge)) == forge
    assert str(forge) == "sourceforge://docutils/a%20b?mount=svn"


def test_nothing_is_written(svn_recorded):
    path = at(svn_recorded, "/trunk/README")

    with pytest.raises(NotImplementedError):
        path.write_text("x")
    with pytest.raises(NotImplementedError):
        path.unlink()
    assert svn_recorded.requests == []
