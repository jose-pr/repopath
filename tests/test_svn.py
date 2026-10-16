"""`SVNPath` against loopback fakes of `mod_dav_svn`: the replies recorded from
real servers (`tests/data/svn`) replayed byte for byte, and a tree served in
the same shapes. Nothing reaches a real host."""

import errno
import hashlib
import http.server
import pickle
import re
import stat
import urllib.parse

import pytest

import svn_fakes
from api_fakes import serve
from pathapi.protocols import (
    BinaryOpen,
    Listing,
    NativeChecksum,
    Readlink,
    Stat,
    supports,
)
from pathapi.testing import ReadPathContract
from repopath import BadReplyError, RepoBackend, RepoPath, SVNPath


def svn(served, name="", *, base="/svn/open", **options):
    """`name` in the tree the recordings call `base`."""
    names = [part for part in name.split("/") if part]
    return SVNPath(*names, instance=served.base_url + base, **options)


def asked(served, method=None):
    return [r for r in served.requests if method is None or r[0] == method]


# -- the contract -----------------------------------------------------------


class TestSVNPathContract(ReadPathContract):
    @pytest.fixture
    def root(self, svn_fixture_server, http_transport_kind):
        return SVNPath(instance=svn_fixture_server.base_url + "/svn/fixture")


# -- stat and listing -------------------------------------------------------


def test_a_file_and_a_directory_are_told_apart_by_one_propfind(svn_recorded):
    readme = svn(svn_recorded, "trunk/README")

    status = readme.stat()

    assert (status.st_size, stat.S_ISREG(status.st_mode)) == (6, True)
    assert status.st_mtime > 0
    assert asked(svn_recorded) == [("PROPFIND", "/svn/open/trunk/README", "0", None)]
    assert svn(svn_recorded, "trunk").is_dir()
    assert not svn(svn_recorded, "trunk").is_file()


def test_a_listing_is_one_propfind_of_depth_one(svn_recorded):
    names = sorted(child.name for child in svn(svn_recorded, "trunk").iterdir())

    assert names == [
        "README", "big", "blob.bin", "docs", "empty-dir", "empty-file",
        "my dir", "secret", "src", "ünï-çødé",
    ]  # fmt: skip
    assert asked(svn_recorded) == [("PROPFIND", "/svn/open/trunk/", "1", None)]


def test_a_listing_hands_each_child_its_stat(svn_recorded):
    children = {c.name: c for c in svn(svn_recorded, "trunk").iterdir()}
    before = len(svn_recorded.requests)

    # A hint answers one question, and the next one asks the server.
    assert children["README"].is_file()
    assert children["src"].is_dir()
    assert children["blob.bin"].stat().st_size == 6

    assert len(svn_recorded.requests) == before


def test_a_walk_costs_one_request_per_directory(svn_recorded):
    top = svn(svn_recorded, "trunk/src")

    walked = [sorted(files) for _top, _dirs, files in top.walk()]

    assert walked == [["a.txt", "link-absolute", "link-to-a", "link-to-docs"]]
    assert len(svn_recorded.requests) == 1


def test_a_missing_path_and_what_is_below_a_file_do_not_exist(svn_recorded):
    assert not svn(svn_recorded, "trunk/no-such").exists()
    assert not svn(svn_recorded, "trunk/README/x").exists()
    with pytest.raises(FileNotFoundError):
        svn(svn_recorded, "trunk/no-such").stat()
    with pytest.raises(FileNotFoundError):
        list(svn(svn_recorded, "trunk/no-such").iterdir())


def test_an_external_is_not_part_of_the_tree(svn_recorded):
    names = {child.name for child in svn(svn_recorded, "trunk").iterdir()}

    assert "ext-docs" not in names
    assert not svn(svn_recorded, "trunk/ext-docs").exists()


def test_empty_files_and_directories(svn_recorded):
    assert svn(svn_recorded, "trunk/empty-file").read_bytes() == b""
    assert svn(svn_recorded, "trunk/empty-file").stat().st_size == 0
    assert svn(svn_recorded, "trunk/empty-dir").is_dir()
    assert list(svn(svn_recorded, "trunk/empty-dir").iterdir()) == []


def test_listing_a_file_and_reading_a_directory_say_what_they_are(svn_recorded):
    with pytest.raises(NotADirectoryError):
        list(svn(svn_recorded, "trunk/README").iterdir())
    with pytest.raises(IsADirectoryError):
        svn(svn_recorded, "trunk/src").read_bytes()


# -- names ------------------------------------------------------------------

_NAMES = {
    "my dir/file with space.txt": "space",
    "my dir/100%.txt": "percent",
    "my dir/hash#.txt": "hash",
    "my dir/what?.txt": "question",
    "my dir/a+b.txt": "plus",
    "my dir/a&b.txt": "amp",
    "ünï-çødé/日本語.txt": "unicode",
}


@pytest.mark.parametrize("name", sorted(_NAMES))
def test_a_name_that_needs_escaping_is_read_and_stat(svn_recorded, name):
    path = svn(svn_recorded, "trunk/" + name)

    assert path.read_text().strip() == _NAMES[name]
    assert path.stat().st_size == len(_NAMES[name]) + 1


def test_listing_decodes_the_names_of_the_server(svn_recorded):
    names = sorted(c.name for c in svn(svn_recorded, "trunk/my dir").iterdir())

    assert names == [
        "100%.txt", "a&b.txt", "a+b.txt", "file with space.txt", "hash#.txt", "what?.txt"
    ]  # fmt: skip
    listed = [c.name for c in svn(svn_recorded, "trunk/ünï-çødé").iterdir()]
    assert listed == ["日本語.txt"]


def test_a_dot_segment_is_refused_before_anything_is_sent(svn_recorded):
    path = svn(svn_recorded, "trunk/src/..")

    assert not path.exists()
    with pytest.raises(ValueError):
        path.read_bytes()
    assert svn_recorded.requests == []


# -- links ------------------------------------------------------------------


def test_a_link_is_a_file_whose_text_is_the_target(svn_recorded):
    link = svn(svn_recorded, "trunk/src/link-to-a")

    assert link.is_symlink()
    assert stat.S_ISLNK(link.lstat().st_mode)
    assert link.readlink().as_posix() == "a.txt"
    assert (
        svn(svn_recorded, "trunk/src/link-to-docs").readlink().as_posix() == "../docs"
    )
    absolute = svn(svn_recorded, "trunk/src/link-absolute")
    assert absolute.readlink().as_posix() == "/etc/passwd"
    assert not svn(svn_recorded, "trunk/src/a.txt").is_symlink()
    with pytest.raises(OSError) as error:
        svn(svn_recorded, "trunk/src/a.txt").readlink()
    assert error.value.errno == 22


def test_following_a_link_reads_and_stats_its_target(svn_recorded):
    link = svn(svn_recorded, "trunk/src/link-to-a")

    assert link.read_text() == "one\ntwo\n"
    assert link.is_file() and link.is_symlink()
    assert link.stat().st_size == len("one\ntwo\n")
    assert stat.S_ISLNK(link.stat(follow_symlinks=False).st_mode)
    assert svn(svn_recorded, "trunk/src/link-to-docs").is_dir()


def test_a_link_out_of_the_tree_does_not_resolve(svn_recorded):
    link = svn(svn_recorded, "trunk/src/link-absolute")

    assert link.is_symlink()
    assert not link.exists()
    with pytest.raises(FileNotFoundError):
        link.read_bytes()


def test_a_listing_tells_links_from_files_without_a_request(svn_recorded):
    children = {c.name: c for c in svn(svn_recorded, "trunk/src").iterdir()}
    before = len(svn_recorded.requests)

    assert [n for n, c in sorted(children.items()) if c.is_symlink()] == [
        "link-absolute", "link-to-a", "link-to-docs"
    ]  # fmt: skip
    assert len(svn_recorded.requests) == before


def test_links_that_name_each_other_are_a_loop():
    tree = {"a": svn_fakes.Link("b"), "b": svn_fakes.Link("a")}
    with svn_fakes.svn_server(recorded=(), tree=lambda: tree) as served:
        path = SVNPath("a", instance=served.base_url + "/svn/fixture")

        assert path.is_symlink()
        assert not path.exists()
        with pytest.raises(OSError) as error:
            path.read_bytes()
    assert error.value.errno == errno.ELOOP


def test_the_protocols_the_kind_supports(svn_recorded):
    path = svn(svn_recorded, "trunk")

    for protocol in (Stat, Listing, BinaryOpen, Readlink, NativeChecksum):
        assert supports(path, protocol), protocol
    assert supports(SVNPath, Readlink)


# -- digests ----------------------------------------------------------------


def test_the_digests_come_with_the_stat_and_the_file_is_not_read(svn_recorded):
    readme = svn(svn_recorded, "trunk/README")

    md5, sha1 = readme.checksum(), readme.checksum("sha1")

    assert md5 == hashlib.md5(b"hello\n").hexdigest()
    assert sha1 == hashlib.sha1(b"hello\n").hexdigest()
    assert asked(svn_recorded, "GET") == []
    assert readme.supported_checksums() == {"md5", "sha1"}


def test_a_digest_the_server_cannot_state_is_not_implemented(svn_recorded):
    readme = svn(svn_recorded, "trunk/README")

    with pytest.raises(NotImplementedError):
        readme.checksum("sha256")
    with pytest.raises(NotImplementedError):
        svn(svn_recorded, "trunk/src/link-to-a").checksum()
    with pytest.raises(IsADirectoryError):
        svn(svn_recorded, "trunk/src").checksum()
    with pytest.raises(FileNotFoundError):
        svn(svn_recorded, "trunk/no-such").checksum()


# -- revisions --------------------------------------------------------------


def test_a_revision_reads_the_tree_as_it_was(svn_recorded):
    assert svn(svn_recorded, "trunk/src/a.txt", ref=1).read_text() == "one\n"
    assert svn(svn_recorded, "trunk/src/a.txt", ref=2).read_text() == "one\ntwo\n"
    assert svn(svn_recorded, "trunk/src/b.txt", ref=2).read_text() == "bee\n"
    assert not svn(svn_recorded, "trunk/src/b.txt", ref=3).exists()
    at_2 = svn(svn_recorded, "trunk/src", ref=2)
    assert sorted(c.name for c in at_2.iterdir()) == ["a.txt", "b.txt"]
    at_3 = svn(svn_recorded, "trunk/src", ref=3)
    assert sorted(c.name for c in at_3.iterdir()) == [
        "a.txt", "link-absolute", "link-to-a", "link-to-docs"
    ]  # fmt: skip
    assert [c.name for c in svn(svn_recorded, "", ref=1).iterdir()] == ["trunk"]


def test_the_revision_stub_is_asked_once_per_backend(svn_recorded):
    backend = RepoBackend()

    for name in ("trunk/src/a.txt", "trunk/src/b.txt"):
        svn(svn_recorded, name, ref=2, backend=backend).read_bytes()

    assert len(asked(svn_recorded, "OPTIONS")) == 1
    assert asked(svn_recorded, "OPTIONS")[0][1] == "/svn/open/"


def test_the_head_needs_no_options_request(svn_recorded):
    svn(svn_recorded, "trunk/README").read_bytes()

    assert asked(svn_recorded, "OPTIONS") == []


def test_a_revision_that_does_not_exist_yet_is_missing(svn_recorded):
    path = svn(svn_recorded, "trunk/src/a.txt", ref=99)

    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        path.stat()


def test_a_revision_joins_and_moves(svn_recorded):
    old = svn(svn_recorded, "trunk", ref=2)

    assert (old / "src").ref == "2"
    assert SVNPath(old, "src", ref="").ref is None
    assert SVNPath(old, "src", ref="HEAD").ref is None
    assert SVNPath(old, ref=3).ref == "3"
    assert (old / "src").as_uri().endswith("/svn/open/trunk/src?ref=2")


@pytest.mark.parametrize("ref", [-1, "r2", "2.5", "-3", "２"])
def test_a_revision_that_is_no_number_is_a_value_error(ref):
    with pytest.raises(ValueError):
        SVNPath(instance="http://h/svn", ref=ref)


@pytest.mark.parametrize("ref", [1.5, True, False, b"2", [2]])
def test_a_revision_of_another_type_is_a_type_error(ref):
    with pytest.raises(TypeError):
        SVNPath(instance="http://h/svn", ref=ref)


# -- authentication ---------------------------------------------------------


def test_a_read_that_needs_credentials_is_a_permission_error(svn_recorded):
    path = svn(svn_recorded, "trunk/README", base="/svn/locked")

    with pytest.raises(PermissionError):
        path.read_bytes()
    with pytest.raises(PermissionError):
        path.stat()
    assert not path.exists()
    assert svn_recorded.requests[0][3] is None


def test_a_wrong_password_is_a_permission_error(svn_recorded):
    path = svn(
        svn_recorded, "trunk", base="/svn/locked", username="alice", password="nope"
    )

    with pytest.raises(PermissionError):
        path.stat()
    assert svn_recorded.requests[0][3] == "alice:bad"


def test_a_user_and_password_are_sent_as_basic(svn_recorded):
    options = {"username": "alice", "password": svn_fakes.USERS["alice"]}
    root = svn(svn_recorded, "trunk", base="/svn/locked", **options)

    assert root.is_dir()
    assert (root / "README").read_text() == "hello\n"
    assert sorted(c.name for c in root.iterdir())[:3] == ["README", "big", "blob.bin"]
    pinned = svn(svn_recorded, "trunk/src/a.txt", base="/svn/locked", ref=2, **options)
    assert pinned.exists()
    assert {r[3] for r in svn_recorded.requests} == {"alice"}


def test_a_bearer_token_is_not_what_subversion_asks_for(svn_recorded):
    path = svn(svn_recorded, "trunk", base="/svn/locked", token="lab-token")

    with pytest.raises(PermissionError):
        path.stat()
    assert svn_recorded.requests[0][3] == "bearer"


def test_what_the_server_denies_a_user_is_a_permission_error(svn_recorded):
    bob = {"username": "bob", "password": svn_fakes.USERS["bob"]}
    secret = svn(svn_recorded, "trunk/secret/key.txt", base="/svn/partial-auth", **bob)

    with pytest.raises(PermissionError):
        secret.read_bytes()
    assert not secret.exists()
    trunk = svn(svn_recorded, "trunk", base="/svn/partial-auth", **bob)
    names = {c.name for c in trunk.iterdir()}
    assert "secret" not in names and "README" in names


def test_credentials_are_exclusive_and_paired():
    with pytest.raises(TypeError):
        SVNPath(instance="http://h/s", username="a")
    with pytest.raises(TypeError):
        SVNPath(instance="http://h/s", password="p")
    with pytest.raises(TypeError):
        SVNPath(instance="http://h/s", token="t", username="a", password="p")
    with pytest.raises(TypeError):
        SVNPath(instance="http://h/s", token="t", backend=RepoBackend())


def test_the_backend_sends_basic_and_shows_nothing(svn_recorded):
    backend = RepoBackend(auth=("alice", svn_fakes.USERS["alice"]))

    assert svn(svn_recorded, "trunk", base="/svn/locked", backend=backend).is_dir()
    assert svn_fakes.USERS["alice"] not in repr(backend)
    with pytest.raises(TypeError):
        RepoBackend(token="t", auth=("a", "b"))
    with pytest.raises(TypeError):
        RepoBackend(auth="a:b")


# -- the place --------------------------------------------------------------


def test_the_place_is_checked():
    for bad in (
        "ftp://h/s", "h/s", "http://", "http://u:p@h/s", "http://h/s?x=1",
        "http://h/s#f", "http://h/../s", "http://h/s/%2e%2e/x", "http://h:99999/s",
        "http://h/s x", "",
    ):  # fmt: skip
        with pytest.raises(ValueError):
            SVNPath(instance=bad)
    with pytest.raises(TypeError):
        SVNPath(instance=3)
    with pytest.raises(TypeError):
        SVNPath("x")


def test_a_call_of_repopath_makes_a_svn_path():
    path = RepoPath("trunk", type="svn", instance="https://H.example/svn/")

    assert isinstance(path, SVNPath) and path.kind == "svn"
    assert path.instance == "https://H.example/svn"
    assert "svn" in RepoPath.kinds()
    with pytest.raises(ValueError):
        RepoPath(instance="https://svn.example.org/repo")


def test_equality_ignores_the_case_of_the_host_and_the_backend():
    a = SVNPath("x", instance="https://H.example/svn")
    b = SVNPath("/x", instance="https://h.example/svn/", backend=RepoBackend())

    assert a == b and hash(a) == hash(b)
    assert a != SVNPath("x", instance="https://h.example/SVN")
    assert a != SVNPath("x", instance="https://h.example/svn", ref=3)
    assert a._same_filesystem(b)


def test_text_repr_and_pickle_show_no_credential():
    path = SVNPath(
        "a b", instance="https://h.example/svn", ref=3, username="u", password="pw"
    )

    assert str(path) == path.as_uri() == "svn+https://h.example/svn/a%20b?ref=3"
    assert "pw" not in repr(path) and "pw" not in str(path)
    restored = pickle.loads(pickle.dumps(path))
    assert restored == path and restored.backend.auth is None


def test_a_joined_path_keeps_the_place_and_the_backend():
    backend = RepoBackend()
    root = SVNPath(instance="https://h.example/svn", ref=4, backend=backend)

    child = root / "trunk" / "x.txt"

    assert child.backend is backend and child.ref == "4"
    assert child.parent.name == "trunk"
    assert SVNPath(child, "y").instance == "https://h.example/svn"
    with pytest.raises(NotImplementedError):
        from repopath import GitHubPath

        SVNPath(GitHubPath(owner="a", repo="b"), instance="https://h/s")


def test_nothing_is_written(svn_recorded):
    path = svn(svn_recorded, "trunk/README")

    with pytest.raises(NotImplementedError):
        path.open("w")
    with pytest.raises(NotImplementedError):
        path.write_text("x")
    with pytest.raises(NotImplementedError):
        path.unlink()
    with pytest.raises(NotImplementedError):
        path.parent.mkdir()
    assert svn_recorded.requests == []


# -- the recordings of public servers ---------------------------------------


def test_a_server_of_the_apache_foundation_replays(svn_recorded):
    base = svn_recorded.base_url + "/repos/asf/subversion/trunk"
    trunk = SVNPath(instance=base)
    readme = trunk / "README"

    assert trunk.is_dir()
    assert readme.stat().st_size == 2222
    assert readme.checksum() == "e831366f8998bb3e9695d952fd2cc022"
    assert readme.checksum("sha1") == "56d347e9953e71b32bd39d3193911cc0c12f5ac6"
    listed = {c.name: c for c in trunk.iterdir()}
    assert len(listed) == 26 and "README" in listed and listed["README"].is_file()
    pinned = SVNPath("README", ref=1932863, instance=base)
    assert pinned.stat().st_size == 2222
    assert asked(svn_recorded, "OPTIONS")[0][1] == "/repos/asf/subversion/trunk/"
    assert not (trunk / "no-such-entry-xyz").exists()


def test_a_server_of_sourceforge_replays(svn_recorded):
    root = SVNPath(instance=svn_recorded.base_url + "/p/docutils/code/trunk/docutils")

    assert root.is_dir()
    assert not (root / "no-such-entry-xyz").exists()
    # Its custom property `cvs2svn:cvs-rev` makes an `allprop` reply invalid
    # XML (the name holds two colons), which is why the client names the
    # properties it wants and the replies recorded for it are well formed.
    with pytest.raises(BadReplyError):
        (root / "README.rst").stat()


def test_the_client_names_the_properties_it_asks_for():
    from repopath import _svn

    for body in (_svn._LIST_BODY, _svn._STAT_BODY):
        assert b"<prop>" in body and b"allprop" not in body
    recorded = svn_fakes.records("local")
    bodies = {r.request_body for r in recorded.values() if r.request_body}
    assert _svn._LIST_BODY.decode() in bodies
    assert _svn._STAT_BODY.decode() in bodies
    assert _svn._OPTIONS_BODY.decode() in bodies


# -- replies that are not what Subversion sends -----------------------------


def _server(handler):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            status, headers, body = handler(self.command, self.path, self.headers)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_PROPFIND = do_OPTIONS = _answer

    return serve(Handler)


_XML = {"Content-Type": "text/xml"}
_ENTRY = (
    b'<D:response xmlns:D="DAV:"><D:href>{href}</D:href><D:propstat><D:prop>'
    b"<D:resourcetype/><D:getcontentlength>{size}</D:getcontentlength>"
    b"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
)
_DECLARATION = b'<?xml version="1.0"?>'


def _entry(href=b"/s/x", size=b"1"):
    return _ENTRY.replace(b"{href}", href).replace(b"{size}", size)


def _multistatus(*entries, doctype=b""):
    return (
        _DECLARATION
        + doctype
        + b'<D:multistatus xmlns:D="DAV:">'
        + b"".join(entries)
        + b"</D:multistatus>"
    )


@pytest.mark.parametrize(
    "status, body",
    [
        (207, b"not xml"),
        (207, b"<html/>"),
        (207, _multistatus()),
        (207, _multistatus(_entry(href=b""))),
        (207, _multistatus(_entry(size=b"big"))),
        (207, _multistatus(_entry(size=b"-1"))),
        (207, _multistatus(_entry(), doctype=b"<!DOCTYPE multistatus>")),
        (200, _multistatus(_entry())),
    ],
)
def test_a_reply_that_is_not_a_multistatus_is_a_bad_reply(status, body):
    seen = []

    def handler(method, path, headers):
        seen.append((method, path))
        return status, _XML, body

    with _server(handler) as base:
        path = SVNPath("x", instance=base + "/s")
        with pytest.raises(BadReplyError):
            path.stat()
        assert not path.exists()
    assert seen and all(m == "PROPFIND" for m, _ in seen)


@pytest.mark.parametrize("status", [301, 302, 307])
def test_a_redirect_to_another_origin_is_not_followed(status):
    seen = []

    def handler(method, path, headers):
        seen.append((method, path))
        return status, {"Location": "http://127.0.0.1:1/elsewhere/"}, b""

    with _server(handler) as base:
        path = SVNPath("x", instance=base + "/s")
        with pytest.raises(OSError) as raised:
            path.read_bytes()
        assert not path.exists()
    assert raised.value.errno == 5
    assert {m for m, _ in seen} == {"PROPFIND"}


def test_a_redirect_of_a_read_is_a_bad_reply_and_is_not_followed():
    seen = []

    def handler(method, path, headers):
        seen.append(method)
        if method == "PROPFIND":
            return 207, _XML, _multistatus(_entry())
        return 301, {"Location": "http://127.0.0.1:1/elsewhere"}, b""

    with _server(handler) as base:
        with pytest.raises(BadReplyError):
            SVNPath("x", instance=base + "/s").read_bytes()
    assert seen == ["PROPFIND", "GET"]


def test_other_statuses_are_the_errors_pathlib_raises():
    def handler(method, path, headers):
        return int(path.split("/")[-1]), {}, b"x"

    with _server(handler) as base:
        for status, error in (
            (404, FileNotFoundError),
            (401, PermissionError),
            (403, PermissionError),
            (500, OSError),
            (503, OSError),
        ):
            path = SVNPath(str(status), instance=base + "/s")
            with pytest.raises(error) as raised:
                path.stat()
            assert not isinstance(raised.value, BadReplyError)
            assert str(status) in str(raised.value) or error is not OSError


def test_a_repository_root_that_does_not_hold_the_instance_is_refused():
    def handler(method, path, headers):
        if method == "OPTIONS":
            reply = {
                "SVN-Repository-Root": "/other",
                "SVN-Rev-Root-Stub": "/other/!svn/rvr",
            }
            return 200, reply, b""
        return 207, _XML, _multistatus(_entry(href=b"/other/!svn/rvr/1/x"))

    with _server(handler) as base:
        with pytest.raises(BadReplyError):
            SVNPath("x", instance=base + "/s", ref=1).stat()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"SVN-Repository-Root": "/s"},
        {"SVN-Repository-Root": "http://x/s", "SVN-Rev-Root-Stub": "/s/!svn"},
        {"SVN-Repository-Root": "/s", "SVN-Rev-Root-Stub": "http://127.0.0.1:1/rvr"},
    ],
)
def test_a_server_that_names_no_usable_stub_is_a_bad_reply(headers):
    def handler(method, path, request_headers):
        if method == "OPTIONS":
            return 200, headers, b""
        return 207, _XML, _multistatus(_entry(href=path.encode()))

    with _server(handler) as base:
        with pytest.raises(BadReplyError):
            SVNPath("x", instance=base + "/s", ref=1).stat()


def test_a_property_the_server_does_not_have_does_not_hide_one_it_has():
    entry = (
        b'<D:response xmlns:D="DAV:"><D:href>/s/x</D:href>'
        b"<D:propstat><D:prop><D:resourcetype/><D:getcontentlength>5</D:getcontentlength>"
        b"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        b"<D:propstat><D:prop><D:getcontentlength/></D:prop>"
        b"<D:status>HTTP/1.1 404 Not Found</D:status></D:propstat></D:response>"
    )
    with _server(lambda *a: (207, _XML, _multistatus(entry))) as base:
        assert SVNPath("x", instance=base + "/s").stat().st_size == 5


def test_a_name_that_is_not_one_component_is_left_out_of_a_listing():
    def handler(method, path, headers):
        entries = [
            _entry(href=href)
            for href in (
                b"/s/ok",
                b"/s/a%2Fb",
                b"/s/%2e%2e",
                b"/s/deeper/x",
                b"/s/also",
            )
        ]
        own = _entry(href=b"/s/", size=b"0").replace(
            b"<D:resourcetype/>", b"<D:resourcetype><D:collection/></D:resourcetype>"
        )
        return 207, _XML, _multistatus(own, *entries)

    with _server(handler) as base:
        names = sorted(c.name for c in SVNPath(instance=base + "/s").iterdir())

    assert names == ["also", "ok"]


def test_a_directory_of_thousands_of_entries_is_one_request():
    tree = {f"big/file-{i:04d}.txt": b"x" for i in range(5000)}
    tree["big"] = None
    with svn_fakes.svn_server(recorded=(), tree=lambda: tree) as served:
        big = SVNPath("big", instance=served.base_url + "/svn/fixture")

        count = sum(1 for _ in big.iterdir())

    assert count == 5000
    assert len(served.requests) == 1


def test_a_tree_served_to_users_only_asks_for_credentials():
    tree = {"a.txt": b"a"}
    users = {"alice": svn_fakes.USERS["alice"]}
    with svn_fakes.svn_server(recorded=(), tree=lambda: tree, users=users) as served:
        url = served.base_url + "/svn/fixture"
        with pytest.raises(PermissionError):
            SVNPath("a.txt", instance=url).read_bytes()
        good = SVNPath("a.txt", instance=url, username="alice", password=users["alice"])
        assert good.read_bytes() == b"a"


# -- every recording replays through the client -----------------------------

_LOCAL = svn_fakes.records("local")
_RECORDED = sorted(
    (
        r
        for r in _LOCAL.values()
        # A directory is asked for with PROPFIND, never with GET.
        if r.label.split("_", 1)[1].startswith(("stat_", "list_", "get_"))
        and not r.label.endswith("get_directory.txt")
    ),
    key=lambda r: r.label,
)


def _client_for(served, record):
    """The path that sends the request `record` holds."""
    parts = urllib.parse.unquote(record.path).split("/", 3)
    mount = "/".join(parts[:3])
    rest = parts[3] if len(parts) > 3 else ""
    ref = None
    pinned = re.match(r"!svn/rvr/(\d+)/?(.*)", rest)
    if pinned:
        ref, rest = pinned.group(1), pinned.group(2)
    options = {}
    who = record.who
    if who == "bearer":
        options["token"] = "lab-token"
    elif who:
        user = who.split(":")[0]
        wrong = who.endswith(":bad")
        options.update(
            username=user, password="wrong" if wrong else svn_fakes.USERS[user]
        )
    names = [n for n in rest.split("/") if n]
    return SVNPath(*names, instance=served.base_url + mount, ref=ref, **options)


@pytest.mark.parametrize("record", _RECORDED, ids=lambda r: r.label)
def test_every_recorded_reply_replays_through_the_client(svn_recorded, record):
    kind = record.label.split("_", 1)[1].split("_", 1)[0]
    path = _client_for(svn_recorded, record)
    failures = {404: FileNotFoundError, 401: PermissionError, 403: PermissionError}
    # What the client makes of a 200 that is not the file itself.
    meaning = {
        "list_readme_as_dir": NotADirectoryError,
        "get_link_to_docs": IsADirectoryError,
        "get_link_absolute": FileNotFoundError,
    }
    name = record.label.split("_", 1)[1].removesuffix(".txt")
    action = {
        "stat": lambda: path.lstat(),
        "list": lambda: list(path.iterdir()),
        "get": lambda: path.read_bytes(),
    }[kind]
    if record.status in failures or name in meaning:
        with pytest.raises(meaning.get(name) or failures[record.status]):
            action()
    elif name == "get_link_to_a":
        assert action() == b"one\ntwo\n"
    elif kind == "get":
        assert action() == record.content
    else:
        action()
