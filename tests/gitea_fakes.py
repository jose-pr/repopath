"""Loopback fakes of the REST API of Gitea and Forgejo.

`recorded()` replays the replies recorded from a Forgejo 14 (`tests/data/gitea`):
a request is answered by the record of the same method, path and `ref`, whatever
page it asks for. `gitea_api(fixture_tree)` serves a directory in the same
shapes (the Git Trees API in pages, the `raw` route), for what no recording
covers: the contract suite's tree and directories of thousands of entries."""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import json
import pathlib
import urllib.parse

from api_fakes import _build_git_tree, _git_tree_children, serve

DATA = pathlib.Path(__file__).parent / "data" / "gitea"
#: The token the fakes treat as wrong, as the real server treats an unknown one.
WRONG_TOKEN = "wrong-token"


class Record:
    def __init__(self, label: str, data: bytes):
        self.label = label
        head, _, rest = data.partition(b"\n\n[body: ")
        lines = head.decode("utf-8", "replace").split("\n")
        self.method, _, self.target = lines[0].partition(" ")
        self.authorized = False
        self.wrong = "wrong" in label
        self.headers = {}
        replied = False
        for line in lines[1:]:
            if line.startswith("> "):
                self.authorized |= line.startswith("> Authorization:")
            elif line.startswith("HTTP "):
                _, status, self.reason = line.split(" ", 2)
                self.status = int(status)
                replied = True
            elif replied:
                name, _, value = line.partition(": ")
                if name in ("Content-Type", "ETag", "Etag", "Location"):
                    self.headers[name] = value
        size, _, body = rest.partition(b" bytes]\n")
        self.content = body[: int(size)]


def _key(method: str, target: str, who: "str | None") -> tuple:
    split = urllib.parse.urlsplit(target)
    ref = urllib.parse.parse_qs(split.query).get("ref", [""])[0]
    return method, split.path, ref, who


def records() -> "dict[tuple, Record]":
    """The recordings of `tests/data/gitea`, by request key."""
    table = {}
    for path in sorted(DATA.glob("*.txt")):
        record = Record(path.stem, path.read_bytes())
        who = "wrong" if record.wrong else "ok" if record.authorized else None
        table[_key(record.method, record.target, who)] = record
    return table


class Served:
    """What the fake server did: the requests it saw and the ones with no
    recording."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.requests: "list[tuple[str, str]]" = []
        self.unrecorded: "list[str]" = []
        self.authorizations: "list[str | None]" = []

    def count(self, method: str, prefix: str = "") -> int:
        return sum(1 for m, p in self.requests if m == method and p.startswith(prefix))


def _who(authorization: "str | None") -> "str | None":
    if not authorization:
        return None
    return "wrong" if authorization.endswith(WRONG_TOKEN) else "ok"


@contextlib.contextmanager
def recorded():
    """A server that replays the recordings; yields its `Served` log. A request
    with no recording is answered 404 and listed in `unrecorded`."""
    table = records()
    served: Served

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            who = _who(self.headers.get("Authorization"))
            served.requests.append((self.command, self.path))
            served.authorizations.append(self.headers.get("Authorization"))
            record = table.get(_key("GET", self.path, who))
            if record is None and who == "ok":
                # A token changes nothing on a public tree.
                record = table.get(_key("GET", self.path, None))
            if record is None:
                served.unrecorded.append(f"{self.command} {self.path} as {who}")
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(record.status)
            for name, value in record.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(record.content)))
            self.end_headers()
            self.wfile.write(record.content)

        def log_message(self, format, *args):
            pass

    with serve(Handler) as base_url:
        served = Served(base_url)
        yield served


@contextlib.contextmanager
def gitea_api(fixture_tree, *, pages=None):
    """Faithful (not mocked) fake of the subset of the Forgejo API that
    `GiteaPath` calls: `GET /api/v1/repos/{o}/{r}` (`default_branch`), the Git
    Trees API (`.../git/trees/{ref-or-tree-id}`: `per_page` capped at 1,000,
    `page`, `total_count`, `X-Total-Count`, `truncated` true on every page of a
    tree longer than one; a ref or id that is not there is 400) and
    `.../raw/{path}?ref=`. Two refs are served (`main` the default, and
    `other-branch` whose `a.txt` differs) and the tree is read from
    `fixture_tree` on every request. `pages`, a list, gets the `(tree, page,
    per_page)` of every trees request. Yields `(base_url, owner, repo)`."""
    owner, repo = "acme", "widgets"

    def _refs():
        main_tree = _build_git_tree(fixture_tree)
        other_tree = dict(main_tree)
        other_tree["a.txt"] = b"a-on-other-branch"
        return {"main": main_tree, "other-branch": other_tree}

    def _sha(ref, path):
        return hashlib.sha1(f"{ref}:{path}".encode()).hexdigest()

    def _find_tree(refs, treeish):
        for ref, tree in refs.items():
            if treeish == ref:
                return ref, tree, ""
            dirs = set()
            for key in tree:
                parts = key.split("/")[:-1]
                dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
            for path in dirs:
                if treeish == _sha(ref, path):
                    return ref, tree, path
        return None

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            split = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(split.query)
            refs = _refs()
            base = f"/api/v1/repos/{owner}/{repo}"
            if split.path == base:
                self._send_json(200, {"default_branch": "main"})
            elif split.path.startswith(base + "/git/trees/"):
                treeish = urllib.parse.unquote(split.path[len(base + "/git/trees/") :])
                self._send_tree(refs, treeish, qs)
            elif split.path.startswith(base + "/raw/"):
                self._send_raw(
                    refs,
                    urllib.parse.unquote(split.path[len(base + "/raw/") :]),
                    qs.get("ref", ["main"])[0] or "main",
                )
            else:
                self._send_json(404, {"message": "not found"})

        def _send_tree(self, refs, treeish, qs):
            found = _find_tree(refs, treeish)
            if found is None:
                self._send_json(400, {"message": f"sha not found [{treeish}]"})
                return
            ref, tree, path = found
            per_page = min(int(qs.get("per_page", ["1000"])[0]) or 1000, 1000)
            page = max(int(qs.get("page", ["1"])[0]), 1)
            if pages is not None:
                pages.append((treeish, page, per_page))
            entries = []
            for name, kind in sorted(_git_tree_children(tree, path).items()):
                child = f"{path}/{name}" if path else name
                if kind == "dir":
                    entries.append(
                        {
                            "path": name,
                            "mode": "040000",
                            "type": "tree",
                            "size": 0,
                            "sha": _sha(ref, child),
                        }
                    )
                else:
                    entries.append(
                        {
                            "path": name,
                            "mode": "100644",
                            "type": "blob",
                            "size": len(tree[child]),
                            "sha": hashlib.sha1(tree[child]).hexdigest(),
                        }
                    )
            chunk = entries[(page - 1) * per_page : page * per_page]
            self._send_json(
                200,
                {
                    "sha": treeish,
                    "tree": chunk,
                    "truncated": len(entries) > per_page,
                    "page": page,
                    "total_count": len(entries),
                },
                {"X-Total-Count": str(len(entries))},
            )

        def _send_raw(self, refs, path, ref):
            tree = refs.get(ref)
            if tree is None or path not in tree:
                self._send_json(404, {"message": "getBlobForEntry", "errors": []})
                return
            body = tree[path]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status, payload, headers=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json;charset=utf-8")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve(Handler) as base_url:
        yield base_url, owner, repo
