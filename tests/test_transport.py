"""What a repository path does with the HTTP exchange itself, whichever transport
sends it: text from the other end in an error message, where a token goes after
a redirect, how much of a reply is read, what a token does not appear in, and
what a stalled server costs. Every request goes to a loopback server."""

import errno
import gzip
import http.server
import socket
import threading
import traceback

import pytest

import pathlib_next.http as core_http
from http_wire import Reply, Wire, response
from repo_support import make_path
from repopath import GitHubPath, GitLabPath, RepoBackend

pytestmark = pytest.mark.usefixtures("http_transport_kind")

ESC = "\x1b[31m"
CAP = 64 * 1024
BOMB = gzip.compress(bytes(1024 * 1024))


def _formatted(error: BaseException) -> str:
    """Everything a log line or traceback could show for `error`."""
    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    ) + "\n".join((str(error), repr(error)))


def _github(flavor, api_base, name="f.txt", **backend):
    return make_path(
        flavor,
        "github",
        name,
        owner="o",
        repo="r",
        backend=RepoBackend(api_base=api_base, **backend),
    )


def _gitlab(flavor, api_base, name="", **backend):
    return make_path(
        flavor,
        "gitlab",
        name,
        owner="o",
        repo="r",
        backend=RepoBackend(api_base=api_base + "/api/v4", **backend),
    )


# --- text from the other end is shown escaped and cut --------------------------


def _reason(reason, status=500):
    raw = f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n".encode("latin-1")
    return lambda request: Reply(raw)


def test_the_reason_phrase_of_an_api_reply_is_shown_escaped(flavor):
    with Wire(_reason(f"Oops {ESC}")) as wire:
        path = _github(flavor, wire.url)
        with pytest.raises(OSError) as excinfo:
            path.read_bytes()
    assert "\x1b" not in str(excinfo.value)
    assert "Oops \\x1b[31m" in str(excinfo.value)


def test_a_long_reason_phrase_is_cut(flavor):
    with Wire(_reason("R" * 20000)) as wire:
        with pytest.raises(OSError) as excinfo:
            _github(flavor, wire.url).read_bytes()
    assert len(str(excinfo.value)) < 400


def test_a_rate_limit_header_is_shown_escaped_and_cut(flavor):
    def script(request):
        return response(
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": ESC + "9" * 500},
            reason="Forbidden",
        )

    with Wire(script) as wire:
        with pytest.raises(OSError) as excinfo:
            _github(flavor, wire.url).read_bytes()
    message = str(excinfo.value)
    assert "\x1b" not in message and len(message) < 400


# --- a token goes to its own origin only -------------------------------------------


def sees_no_credential(wire):
    return [r.header("Authorization") for r in wire.requests] == [None] * len(
        wire.requests
    )


def test_the_token_is_sent_to_the_api_origin_only(flavor):
    raw = {
        "X-GitHub-Media-Type": "github.v3; param=raw",
        "Content-Type": "application/vnd.github.raw+json",
    }
    with Wire(lambda r: response(200, raw, b"abc")) as away:

        def home_script(request):
            return response(302, {"Location": away.url + "/elsewhere"}, reason="Found")

        with Wire(home_script) as home:
            path = _github(flavor, home.url, token="T0KEN")
            assert path.exists()
            assert path.read_bytes() == b"abc"
    assert home.requests[0].header("Authorization") == "Bearer T0KEN"
    assert away.requests and sees_no_credential(away)


# --- how much of a reply is read ----------------------------------------------------


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(core_http, "MAX_REPLY_BYTES", CAP)


def _bomb(status=200, extra=None):
    return response(status, {"Content-Encoding": "gzip", **(extra or {})}, BOMB)


def test_a_github_api_reply_over_the_limit_is_efbig_and_a_file_is_not_limited(
    flavor, small_cap
):
    raw = {
        "Content-Type": "application/vnd.github.raw+json",
        "X-GitHub-Media-Type": "github.v3; param=raw",
    }
    content = b"c" * (3 * CAP)

    def script(request):
        if request.target.startswith("/repos/o/r/contents/dir"):
            return _bomb(200, {"Content-Type": "application/json"})
        return response(200, raw, content)

    with Wire(script) as wire:
        directory = _github(flavor, wire.url, "dir")
        with pytest.raises(OSError) as excinfo:
            directory.stat()
        assert excinfo.value.errno == errno.EFBIG
        assert directory.exists() is False
        # The content of a file is what was asked for, not a document.
        file = _github(flavor, wire.url, "file.bin")
        assert file.read_bytes() == content


def test_a_gitlab_api_reply_over_the_limit_is_efbig(flavor, small_cap):
    with Wire(lambda r: _bomb(200, {"Content-Type": "application/json"})) as wire:
        path = _gitlab(flavor, wire.url)
        with pytest.raises(OSError) as excinfo:
            path.stat()
    assert excinfo.value.errno == errno.EFBIG


# --- a token is in no message ----------------------------------------------------


@pytest.fixture
def recording_server():
    """Answers 404 and records each request as `(method, path, headers)`."""
    seen = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command, self.path, dict(self.headers)))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("cls", [GitHubPath, GitLabPath])
def test_the_token_is_sent_as_a_bearer_and_is_in_no_message(recording_server, cls):
    base, seen = recording_server
    token = "ghp_REAL456"
    path = cls("a.txt", owner="acme", repo="widgets", token=token)
    path.backend.api_base = base
    with pytest.raises(FileNotFoundError) as excinfo:
        path.read_bytes()
    assert seen[-1][2]["Authorization"] == f"Bearer {token}"
    text = _formatted(excinfo.value)
    assert token not in text
    assert excinfo.value.__cause__ is None


# --- a server that never answers ------------------------------------------------------


@pytest.fixture
def stalled_server():
    """Accepts TCP connections and never sends a byte. Yields the port."""
    listener = socket.create_server(("127.0.0.1", 0))
    held = []
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            held.append(conn)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=5)
        for conn in held:
            conn.close()
        listener.close()


def test_a_stalled_server_times_out_with_an_error_that_names_the_path(
    flavor, stalled_server
):
    path = _github(
        flavor, f"http://127.0.0.1:{stalled_server}", "a.txt", timeout=(2, 0.5)
    )
    with pytest.raises(TimeoutError) as excinfo:
        path.stat()
    assert "a.txt" in str(excinfo.value)
