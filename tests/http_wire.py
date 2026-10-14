"""A loopback server scripted at the byte level: an origin that records what it
receives and answers from a script. Nothing here leaves the loopback
interface."""

from __future__ import annotations

import select
import socket
import ssl
import threading
import time
import typing

CRLF = b"\r\n"

# How often a blocked accept() looks at the stop flag. Closing a listening
# socket from another thread does not wake an accept() on every platform
# (Linux leaves it blocked), so the accept loops never rely on it.
_POLL = 0.05


def _stop_listening(stop, thread, listener, sockets, lock):
    """Stop an accept loop and drop every connection it accepted."""
    stop.set()
    thread.join(timeout=10)  # a safety net: the loop sees the flag within _POLL
    try:
        listener.close()
    except OSError:
        pass
    with lock:
        accepted = list(sockets)
    for sock in accepted:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


class Request(typing.NamedTuple):
    """One request as the server received it."""

    method: str
    target: str
    headers: "list[tuple[str, str]]"
    body: bytes
    connection: int  # which accepted connection carried it, from 0

    def header(self, name: str):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class Reply(typing.NamedTuple):
    """Bytes to send, whether to close the connection after them, and bytes
    to send `delay` seconds later on the same connection."""

    raw: bytes
    close: bool = False
    later: bytes = b""
    delay: float = 0.0


class Drop:
    """Close the connection without answering."""


class Hang:
    """Never answer; the connection stays open until the server closes."""


def response(status=200, headers=None, body=b"", reason="OK", length=True) -> bytes:
    """A complete HTTP/1.1 response; `Content-Length` is added unless
    `length` is false or `headers` names one."""
    head = [f"HTTP/1.1 {status} {reason}"]
    names = {k.lower() for k in (headers or {})}
    for key, value in (headers or {}).items():
        head.append(f"{key}: {value}")
    if length and "content-length" not in names and "transfer-encoding" not in names:
        head.append(f"Content-Length: {len(body)}")
    return "\r\n".join(head).encode("latin-1") + CRLF + CRLF + body


def _read_until(
    sock, marker: bytes, limit=1 << 20, start: bytes = b""
) -> "tuple[bytes, bytes] | None":
    data = start
    while marker not in data:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        data += chunk
        if len(data) > limit:
            return None
    head, _, rest = data.partition(marker)
    return head, rest


def _read_exactly(sock, rest: bytes, count: int) -> bytes:
    while len(rest) < count:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return rest


def _read_chunked(sock, rest: bytes) -> "tuple[bytes, bytes]":
    """The body, and the bytes that followed it on the connection."""
    body = b""
    while True:
        while CRLF not in rest:
            chunk = sock.recv(65536)
            if not chunk:
                return body, b""
            rest += chunk
        line, _, rest = rest.partition(CRLF)
        size = int(line.split(b";")[0], 16)
        rest = _read_exactly(sock, rest, size + 2)
        body += rest[:size]
        rest = rest[size + 2 :]
        if size == 0:
            return body, rest


class Wire:
    """An origin on a loopback port. `script(request)` returns what to send:
    `bytes`, a `Reply`, `Drop()` or `Hang()`; the default answers `200 ok`.
    `requests` lists what was received, `connections` how many connections
    were accepted, and `closed` the connections the client closed. With `tls`
    (an `ssl.SSLContext`) every connection is wrapped by the server first."""

    def __init__(self, script=None, tls: "ssl.SSLContext | None" = None):
        self.script = script or (lambda request: response(body=b"ok"))
        self.tls = tls
        self.requests: "list[Request]" = []
        self._connections = 0
        self._taking = threading.Lock()
        self.closed: "list[int]" = []
        self.handshakes_failed = 0
        self._lock = threading.Lock()
        self._sockets: "list[socket.socket]" = []
        self._stop = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.setblocking(False)
        self.port = self._listener.getsockname()[1]
        self.host = "127.0.0.1"
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        _stop_listening(
            self._stop, self._thread, self._listener, self._sockets, self._lock
        )

    @property
    def connections(self) -> int:
        """How many connections the client has made. A connection waiting in
        the kernel's queue is taken first, so a test asking "was one made?"
        does not depend on the accept thread having run."""
        self._take()
        return self._connections

    def _accept(self):
        while not self._stop.is_set():
            try:
                select.select([self._listener], [], [], _POLL)
            except (OSError, ValueError):
                return
            self._take()

    def _take(self):
        """Accept every connection that is waiting, and start serving it."""
        with self._taking:
            while True:
                try:
                    sock, _ = self._listener.accept()
                except OSError:  # nothing waiting, or the listener is closed
                    return
                sock.setblocking(True)
                with self._lock:
                    index = self._connections
                    self._connections += 1
                    self._sockets.append(sock)
                threading.Thread(
                    target=self._serve, args=(sock, index), daemon=True
                ).start()

    def _serve(self, sock, index):
        try:
            if self.tls is not None:
                try:
                    sock = self.tls.wrap_socket(sock, server_side=True)
                except (OSError, ssl.SSLError):
                    with self._lock:
                        self.handshakes_failed += 1
                    return
                with self._lock:
                    self._sockets.append(sock)
            carry = b""  # what the client sent beyond the request just read
            while not self._stop.is_set():
                parsed = _read_until(sock, CRLF + CRLF, start=carry)
                if parsed is None:
                    return
                head, rest = parsed
                lines = head.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                headers = [tuple(line.split(":", 1)) for line in lines[1:]]
                headers = [(k, v.strip()) for k, v in headers]
                length = next(
                    (int(v) for k, v in headers if k.lower() == "content-length"), 0
                )
                chunked = any(
                    k.lower() == "transfer-encoding" and "chunked" in v.lower()
                    for k, v in headers
                )
                if chunked:
                    body, carry = _read_chunked(sock, rest)
                else:
                    data = _read_exactly(sock, rest, length)
                    body, carry = data[:length], data[length:]
                request = Request(method, target, headers, body, index)
                with self._lock:
                    self.requests.append(request)
                action = self.script(request)
                if isinstance(action, Drop):
                    return
                if isinstance(action, Hang):
                    self._stop.wait(30)
                    return
                if isinstance(action, bytes):
                    action = Reply(action)
                sock.sendall(action.raw)
                if action.later:
                    time.sleep(action.delay)
                    sock.sendall(action.later)
                if action.close:
                    return
        except (OSError, ssl.SSLError, ValueError):
            # A request line that does not parse, as after bytes the client
            # should not have sent, ends the connection as a server would.
            return
        finally:
            with self._lock:
                self.closed.append(index)
            try:
                sock.close()
            except OSError:
                pass
