"""Loopback fakes of a Subversion server (`mod_dav_svn`) and of the SourceForge
REST API.

`records()` loads the replies recorded from real servers (`tests/data/svn`):
the server replays one byte for byte, keyed by method, path, `Depth` and who
asked (nobody, a user, a user with a wrong password, a bearer token). A tree
(`tree=` a callable returning `{path: bytes | None | Link}`) is served in the
same shapes for what no recording covers: a directory the contract suite
builds, a directory of thousands of entries."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import io
import pathlib
import re
import threading
import typing
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from api_fakes import serve

DATA = pathlib.Path(__file__).parent / "data" / "svn"
#: Who may ask the locked locations of the recordings, and their passwords.
USERS = {"alice": "lab-password", "bob": "lab-password-2"}
_DAV = "DAV:"
_SVN_DAV = "http://subversion.tigris.org/xmlns/dav/"
_SVN = "http://subversion.tigris.org/xmlns/svn/"
_RECORDED_HOST = "127.0.0.1:18080"
_MODIFIED = "Fri, 16 Oct 2026 15:52:18 GMT"


class Link(typing.NamedTuple):
    """A symbolic link of a served tree."""

    target: str


class Record(typing.NamedTuple):
    label: str
    method: str
    path: str
    depth: "str | None"
    who: "str | None"
    request_body: "str | None"
    status: int
    reason: str
    headers: "list[tuple[str, str]]"
    content: bytes


def _key(method: str, target: str, depth, who) -> tuple:
    path = urllib.parse.unquote(urllib.parse.urlsplit(target).path)
    if method == "PROPFIND":
        path = path.rstrip("/") or "/"
    return method, path, depth, who


def _who(authorization: "str | None") -> "str | None":
    """Who a request is, as a recording names it: `alice`, `alice:bad`,
    `bearer`, or None for nobody."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer":
        return "bearer"
    try:
        user, _, password = base64.b64decode(value).decode().partition(":")
    except ValueError:
        return "garbage"
    return user if USERS.get(user) == password else f"{user}:bad"


def parse_record(label: str, data: bytes) -> Record:
    stream = io.BytesIO(data)
    method, _, target = stream.readline().decode().rstrip("\n").partition(" ")
    depth = who = body = None
    while True:
        line = stream.readline().decode().rstrip("\n")
        if not line:
            break
        name, _, value = line[2:].partition(": ")
        if name == "Depth":
            depth = value
        elif name == "Body":
            body = value
        elif name == "Authorization":
            who = re.search(r"<(.*)>", value).group(1)
            who = "bearer" if value.startswith("Bearer") else who
    status_line = stream.readline().decode().rstrip("\n")
    _, status, reason = status_line.split(" ", 2)
    headers = []
    while True:
        line = stream.readline().decode("utf-8", "replace").rstrip("\n")
        if not line:
            break
        name, _, value = line.partition(": ")
        headers.append((name, value))
    rest = stream.read()
    if rest.startswith(b"[body: "):
        rest = rest.split(b"\n", 1)[1]
    if rest.rstrip().endswith(b"[... truncated ...]"):
        raise ValueError(f"{label} holds a truncated body")
    if rest.endswith(b"\n") and not any(
        n.lower() == "content-length" for n, _ in headers
    ):
        rest = rest[:-1]
    return Record(
        label, method, urllib.parse.urlsplit(target).path, depth, who, body,
        int(status), reason, headers, rest,
    )  # fmt: skip


def records(*directories: str) -> "dict[tuple, Record]":
    """The recordings under `tests/data/svn/<directory>`, by request key."""
    table = {}
    for directory in directories or ("local", "public"):
        for path in sorted((DATA / directory).glob("*.txt")):
            record = parse_record(path.name, path.read_bytes())
            table[_key(record.method, record.path, record.depth, record.who)] = record
    return table


def _hex_lower(text: str) -> str:
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), text)


def _href(mount: str, rel: str, directory: bool) -> str:
    quoted = urllib.parse.quote(rel, safe="/")
    return _hex_lower(
        mount + ("/" + quoted if quoted else "") + ("/" if directory else "")
    )


def _prop_element(
    name: str, namespace: str, value: "str | None" = None, inner=""
) -> str:
    prefix = {_DAV: "lp1", _SVN_DAV: "lp2", _SVN: "S"}[namespace]
    if value is None and not inner:
        return f"<{prefix}:{name}/>"
    return f"<{prefix}:{name}>{inner or escape(value)}</{prefix}:{name}>"


_PROPS = (
    ("resourcetype", _DAV),
    ("getcontentlength", _DAV),
    ("getlastmodified", _DAV),
    ("version-name", _DAV),
    ("md5-checksum", _SVN_DAV),
    ("sha1-checksum", _SVN_DAV),
    ("special", _SVN),
    ("executable", _SVN),
)


def _requested(body: bytes) -> "list[tuple[str, str]]":
    """The `(name, namespace)` of each property a `PROPFIND` names; everything
    for `allprop` and for no body."""
    if not body.strip():
        return list(_PROPS)
    document = ET.fromstring(body)
    prop = document.find(f"{{{_DAV}}}prop")
    if prop is None:
        return list(_PROPS)
    names = []
    for child in prop:
        namespace, _, name = child.tag[1:].partition("}")
        names.append((name, namespace))
    return names


def _available(node, name: str, namespace: str) -> "str | None":
    """The XML of a property of `node` (bytes, None for a directory, a `Link`),
    or None when the server has no such property for it."""
    if name == "resourcetype":
        inner = "<D:collection/>" if node is None else ""
        return (
            _prop_element("resourcetype", _DAV, inner=inner)
            if inner
            else "<lp1:resourcetype/>"
        )
    if name == "version-name":
        return _prop_element(name, _DAV, "1")
    if name == "getlastmodified":
        return _prop_element(name, _DAV, _MODIFIED)
    if node is None:
        return None
    content = f"link {node.target}".encode() if isinstance(node, Link) else node
    if name == "getcontentlength":
        return _prop_element(name, _DAV, str(len(content)))
    if name == "md5-checksum":
        return _prop_element(name, _SVN_DAV, hashlib.md5(content).hexdigest())
    if name == "sha1-checksum":
        return _prop_element(name, _SVN_DAV, hashlib.sha1(content).hexdigest())
    if name == "special" and isinstance(node, Link):
        return _prop_element(name, _SVN, "*")
    return None


def _response(mount: str, rel: str, node, wanted, href: str) -> str:
    found, missing = [], []
    for name, namespace in wanted:
        text = _available(node, name, namespace)
        if text is None:
            prefix = {_DAV: "g0", _SVN_DAV: "g1", _SVN: "g2"}[namespace]
            missing.append(f"<{prefix}:{name}/>")
        else:
            found.append(text)
    xmlns = (
        'xmlns:lp1="DAV:" xmlns:lp2="http://subversion.tigris.org/xmlns/dav/" '
        'xmlns:S="http://subversion.tigris.org/xmlns/svn/" '
        'xmlns:g0="DAV:" xmlns:g1="http://subversion.tigris.org/xmlns/dav/" '
        'xmlns:g2="http://subversion.tigris.org/xmlns/svn/"'
    )
    text = f"<D:response {xmlns}>\n<D:href>{escape(href)}</D:href>\n"
    text += "<D:propstat>\n<D:prop>\n" + "\n".join(found) + "\n</D:prop>\n"
    text += "<D:status>HTTP/1.1 200 OK</D:status>\n</D:propstat>\n"
    if missing:
        text += "<D:propstat>\n<D:prop>\n" + "\n".join(missing) + "\n</D:prop>\n"
        text += "<D:status>HTTP/1.1 404 Not Found</D:status>\n</D:propstat>\n"
    return text + "</D:response>\n"


class Served:
    """What the fake server did: the requests it saw and the ones with no
    recording."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.requests: "list[tuple[str, str, str | None, str | None]]" = []
        self.unrecorded: "list[tuple]" = []

    def count(self, method: str, prefix: str = "") -> int:
        return sum(
            1 for m, p, *_ in self.requests if m == method and p.startswith(prefix)
        )


def _handler(table, tree, mount, users_required, served):
    stub = f"{mount}/!svn/rvr"

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, reason, headers, content, head_only=False):
            self.send_response(status, reason)
            for name, value in headers:
                if name.lower() in (
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "date",
                ):
                    continue
                value = value.replace(_RECORDED_HOST, self.headers["Host"])
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if not head_only:
                self.wfile.write(content)

        def _answer(self, head_only=False):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            method, target = self.command, self.path
            depth = self.headers.get("Depth")
            who = _who(self.headers.get("Authorization"))
            served.requests.append((method, urllib.parse.unquote(target), depth, who))
            path = urllib.parse.unquote(urllib.parse.urlsplit(target).path)
            if tree is not None and (path == mount or path.startswith(mount + "/")):
                return self._tree(method, path, depth, who, body, head_only)
            record = table.get(_key(method, target, depth, who))
            if record is None and method == "OPTIONS":
                # The server answers an OPTIONS request the same for every path
                # of a repository: the nearest recorded one stands for the rest.
                prefix = urllib.parse.unquote(urllib.parse.urlsplit(target).path)
                held = [
                    r for k, r in table.items()
                    if k[0] == "OPTIONS" and k[3] == who
                    and prefix.rstrip("/").startswith(k[1].rstrip("/"))
                ]  # fmt: skip
                if held:
                    record = max(held, key=lambda r: len(r.path))
            if record is None and method == "HEAD":
                record = table.get(_key("GET", target, depth, who))
                head_only = record is not None
            if record is None:
                served.unrecorded.append((method, target, depth, who))
                return self._send(
                    404, "Not Found", [("X-Fake", "unrecorded")], b"unrecorded"
                )
            self._send(
                record.status, record.reason, record.headers, record.content, head_only
            )

        do_GET = do_PROPFIND = do_OPTIONS = do_POST = do_PUT = _answer

        def do_HEAD(self):
            self._answer(head_only=True)

        # -- generated from a tree -------------------------------------

        def _tree(self, method, path, depth, who, body, head_only):
            if users_required is not None and who not in users_required:
                return self._send(
                    401, "Unauthorized",
                    [("WWW-Authenticate", 'Basic realm="fake"'), ("Content-Type", "text/html")],
                    b"<html>401</html>",
                )  # fmt: skip
            snapshot = tree()
            if method == "OPTIONS":
                headers = [
                    ("SVN-Youngest-Rev", "1"),
                    ("SVN-Repository-Root", mount),
                    ("SVN-Rev-Root-Stub", stub),
                ]
                return self._send(
                    200, "OK", headers, b"<D:options-response xmlns:D='DAV:'/>"
                )
            rel = path[len(mount) :]
            if rel.startswith("/!svn/rvr/"):
                rel = "/" + rel.split("/", 4)[4] if rel.count("/") >= 4 else ""
            rel = rel.strip("/")
            if rel and rel not in snapshot:
                return self._send(
                    404,
                    "Not Found",
                    [("Content-Type", "text/html")],
                    b"<html>404</html>",
                )
            node = snapshot.get(rel) if rel else None
            if method == "PROPFIND":
                return self._propfind(snapshot, rel, node, path, depth, body)
            if method in ("GET", "HEAD"):
                if node is None:
                    if not path.endswith("/"):
                        location = f"http://{self.headers['Host']}{path}/"
                        return self._send(
                            301, "Moved Permanently", [("Location", location)], b""
                        )
                    return self._send(
                        200,
                        "OK",
                        [("Content-Type", "text/html")],
                        b"<html>index</html>",
                        head_only,
                    )
                content = (
                    f"link {node.target}".encode() if isinstance(node, Link) else node
                )
                return self._send(
                    200, "OK", [("Content-Type", "text/plain")], content, head_only
                )
            return self._send(400, "Bad Request", [], b"")

        def _propfind(self, snapshot, rel, node, path, depth, body):
            if depth not in ("0", "1"):
                return self._send(
                    403,
                    "Forbidden",
                    [("Content-Type", "text/html")],
                    b"<html>no infinity</html>",
                )
            wanted = _requested(body)
            directory = node is None
            parts = [
                _response(
                    mount,
                    rel,
                    node,
                    wanted,
                    path.rstrip("/") + ("/" if directory else ""),
                )
            ]
            if depth == "1" and directory:
                prefix = rel + "/" if rel else ""
                children = [
                    key for key in snapshot
                    if key.startswith(prefix) and key != rel and "/" not in key[len(prefix):]
                ]  # fmt: skip
                for key in sorted(children, reverse=True):
                    child = snapshot[key]
                    base = path.rstrip("/")
                    name = urllib.parse.quote(key[len(prefix) :], safe="")
                    href = _hex_lower(f"{base}/{name}" + ("/" if child is None else ""))
                    parts.append(_response(mount, key, child, wanted, href))
            text = '<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">\n'
            text += "".join(parts) + "</D:multistatus>\n"
            self._send(
                207,
                "Multi-Status",
                [("Content-Type", 'text/xml; charset="utf-8"')],
                text.encode(),
            )

    return Handler


@contextlib.contextmanager
def svn_server(
    *,
    recorded=("local", "public"),
    tree: "typing.Callable[[], dict] | None" = None,
    mount: str = "/svn/fixture",
    users: "dict | None" = None,
):
    """A fake Subversion server on loopback. Yields a `Served`. `recorded`
    names the recordings it replays; `tree` serves a tree under `mount`
    (and, given `users`, only to them)."""
    table = records(*recorded) if recorded else {}
    served = Served("")
    required = None
    if users is not None:
        required = {
            user for user, password in users.items() if USERS.get(user) == password
        }
    handler = _handler(table, tree, mount, required, served)
    with serve(handler) as base_url:
        served.base_url = base_url
        yield served


def snapshot_directory(directory) -> dict:
    """`{posix path: bytes | None}` of a local directory (a file is its
    content, a directory None)."""
    root = pathlib.Path(directory)
    found = {}
    for path in sorted(root.rglob("*")):
        found[path.relative_to(root).as_posix()] = (
            None if path.is_dir() else path.read_bytes()
        )
    return found


class SourceForged(typing.NamedTuple):
    """The fake REST reply of one tool of a SourceForge project."""

    kind: str
    clone_url: "str | None"


@contextlib.contextmanager
def sourceforge_api(projects: "dict[tuple[str, str], SourceForged]", *, recorded=True):
    """A fake of `GET /rest/p/PROJECT/MOUNT/`: the JSON of the tools in
    `projects`, else the recorded 404 page of an unknown project. Yields a
    `Served`."""
    table = records("public") if recorded else {}
    missing = next(r for r in table.values() if r.label.startswith("19_"))
    served = Served("")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            import json

            served.requests.append(("GET", self.path, None, None))
            match = re.fullmatch(
                r"/rest/p/([^/]+)/([^/]+)/", urllib.parse.urlsplit(self.path).path
            )
            tool = projects.get((match.group(1), match.group(2))) if match else None
            if tool is None:
                body = missing.content
                self.send_response(404, "Not Found")
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                reply = {"name": tool.kind, "mount_point": match.group(2)}
                if tool.clone_url is not None:
                    reply["clone_url_https_anon"] = tool.clone_url
                body = json.dumps(reply).encode()
                self.send_response(200, "OK")
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with serve(Handler) as base_url:
        served.base_url = base_url
        yield served
