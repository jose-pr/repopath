"""Loopback fakes of the REST APIs of GitHub and GitLab, and the server they run
on. The tree they serve is a directory (`fixture_tree`), read again on every
request, so a test may add files first."""

import contextlib
import http.server
import threading


@contextlib.contextmanager
def serve(handler):
    """Run a `ThreadingHTTPServer` for `handler` on an OS-assigned loopback
    port; yields its base URL (no trailing slash) and stops it on exit."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # A short poll interval: `shutdown()` waits up to one for the loop to see it.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _build_git_tree(local_dir):
    """Flatten a local directory into a git-tree-shaped {posix relpath:
    bytes} dict for the fake GitHub/GitLab servers below. Git has no
    concept of an empty directory (no tree entry can represent one with no
    blobs under it) -- a truly empty dir (fixture_tree's `empty_dir/`)
    would vanish from any listing derived purely from blob-path prefixes,
    so a `.gitkeep`-style placeholder blob is synthesized under it here,
    same as a real repo author would have to do. This only affects the
    fake server's in-memory tree, never `fixture_tree` itself."""
    import pathlib

    local_dir = pathlib.Path(local_dir)
    tree = {}
    for path in sorted(local_dir.rglob("*")):
        rel = path.relative_to(local_dir).as_posix()
        if path.is_dir():
            if not any(path.iterdir()):
                tree[f"{rel}/.gitkeep"] = b""
        else:
            tree[rel] = path.read_bytes()
    return tree


def _git_tree_children(tree, prefix):
    """Immediate children of `prefix` ("" for root) in a `_build_git_tree`
    dict -- name -> "dir"/"file", mirroring how GitHub's contents API and
    GitLab's tree API both report one directory level at a time."""
    prefix = f"{prefix}/" if prefix else ""
    children = {}
    for key in tree:
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if not rest:
            continue
        name, sep, _ = rest.partition("/")
        children[name] = "dir" if sep else "file"
    return children


@contextlib.contextmanager
def github_api(fixture_tree):
    """Faithful (not mocked) fake of the subset of the GitHub REST contents
    API (`GET /repos/{owner}/{repo}/contents/{path}`) that `GitHubPath`
    actually calls: directory listing (JSON array), file metadata
    (JSON object, base64 `content`), and raw file bodies (`Accept:
    .../vnd.github.raw+json`). A `HEAD` gets the headers of the `GET`, and
    every reply names its media type in `X-GitHub-Media-Type`. Two refs are served (`main` the default,
    `other-branch` with a divergent `a.txt`) so ref plumbing is verified
    end-to-end, not just default-branch reads. Like the real API, a
    contents listing stops at 1,000 entries with no truncation signal; the
    Git Trees API (`GET /repos/{owner}/{repo}/git/trees/{sha}`, `sha` from a
    listing entry or a ref name for the root) and `GET /repos/{owner}/{repo}`
    (`default_branch`) are served for the uncapped route. The tree is read
    from `fixture_tree` on every request, so a test may add files first.
    Yields `(base_url, owner, repo)`."""
    import base64
    import hashlib
    import json
    import urllib.parse

    owner, repo = "acme", "widgets"

    def _refs():
        main_tree = _build_git_tree(fixture_tree)
        other_tree = dict(main_tree)
        other_tree["a.txt"] = b"a-on-other-branch"
        return {"main": main_tree, "other-branch": other_tree}

    def _dir_sha(ref, path):
        return hashlib.sha1(f"{ref}:{path}".encode()).hexdigest()

    class _GitHubApiHandler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            # The headers of the GET and no body, as the API answers.
            self.do_GET()

        def _media_type(self):
            raw = "raw" in self.headers.get("Accept", "")
            return (
                "github.v3; param=raw; format=json" if raw else "github.v3; format=json"
            )

        def do_GET(self):
            split = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(split.query)
            refs = _refs()

            if split.path == f"/repos/{owner}/{repo}":
                self._send_json(200, {"default_branch": "main"})
                return
            trees = f"/repos/{owner}/{repo}/git/trees/"
            if split.path.startswith(trees):
                self._send_tree(refs, urllib.parse.unquote(split.path[len(trees) :]))
                return

            ref = qs.get("ref", ["main"])[0]
            tree = refs.get(ref)
            if tree is None:
                self._send_json(404, {"message": "Not Found"})
                return

            prefix = f"/repos/{owner}/{repo}/contents"
            if split.path == prefix:
                subpath = ""
            elif split.path.startswith(prefix + "/"):
                subpath = urllib.parse.unquote(split.path[len(prefix) + 1 :])
            else:
                self._send_json(404, {"message": "Not Found"})
                return

            if subpath in tree:
                self._send_file(subpath, tree[subpath])
                return

            children = _git_tree_children(tree, subpath)
            if children or subpath == "":
                entries = []
                for name, kind in sorted(children.items()):
                    childpath = f"{subpath}/{name}" if subpath else name
                    size = len(tree.get(childpath, b"")) if kind == "file" else 0
                    entries.append(
                        {
                            "name": name,
                            "path": childpath,
                            "type": kind,
                            "size": size,
                            "sha": _dir_sha(ref, childpath) if kind == "dir" else None,
                        }
                    )
                self._send_json(200, entries[:1000])
                return

            self._send_json(404, {"message": "Not Found"})

        def _send_tree(self, refs, sha):
            for ref, tree in refs.items():
                dirs = {""}
                for key in tree:
                    parts = key.split("/")[:-1]
                    dirs.update("/".join(parts[: i + 1]) for i in range(len(parts)))
                for path in dirs:
                    if (path == "" and sha == ref) or (
                        path and sha == _dir_sha(ref, path)
                    ):
                        entries = []
                        for name, kind in sorted(
                            _git_tree_children(tree, path).items()
                        ):
                            childpath = f"{path}/{name}" if path else name
                            entry = {"path": name, "mode": "100644"}
                            if kind == "dir":
                                entry.update(type="tree", sha=_dir_sha(ref, childpath))
                            else:
                                entry.update(type="blob", size=len(tree[childpath]))
                            entries.append(entry)
                        self._send_json(
                            200, {"sha": sha, "tree": entries, "truncated": False}
                        )
                        return
            self._send_json(404, {"message": "Not Found"})

        def _send_file(self, subpath, content):
            accept = self.headers.get("Accept", "")
            if "raw" in accept:
                self.send_response(200)
                self.send_header(
                    "Content-Type", "application/vnd.github.raw+json; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-GitHub-Media-Type", self._media_type())
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(content)
                return
            self._send_json(
                200,
                {
                    "name": subpath.rsplit("/", 1)[-1],
                    "path": subpath,
                    "type": "file",
                    "size": len(content),
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode(),
                },
            )

        def _send_json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-GitHub-Media-Type", self._media_type())
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve(_GitHubApiHandler) as base_url:
        yield base_url, owner, repo


@contextlib.contextmanager
def gitlab_api(fixture_tree):
    """Faithful (not mocked) fake of the subset of the GitLab REST API v4
    that `GitLabPath` actually calls: `.../repository/tree` (listing, no
    size -- matches the real endpoint's shape), `.../repository/files/:path`
    (file metadata) and `.../repository/files/:path/raw` (file body). Like
    the real API, the tree listing is paginated (`per_page` capped at 100,
    `page`, `X-Next-Page`), lists trees before blobs, 404s an unknown
    project, ref or tree path; only the `main` ref exists. The same tree is
    also served as the subgroup project `acme/team/widgets`. The tree is
    read from `fixture_tree` on every request, so a test may add files
    first. Yields `(base_url, owner, repo)`."""
    import json
    import urllib.parse

    owner, repo = "acme", "widgets"
    project_ids = [
        urllib.parse.quote(f"{owner}/{repo}", safe=""),
        urllib.parse.quote(f"{owner}/team/{repo}", safe=""),
    ]

    class _GitLabApiHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            split = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(split.query)
            tree = _build_git_tree(fixture_tree)

            # `GET /projects/:id` (default_branch lookup) -- no "/repository/"
            # suffix, must be checked before the repository-scoped prefix below.
            if split.path in [f"/api/v4/projects/{pid}" for pid in project_ids]:
                self._send_json(200, {"default_branch": "main"})
                return

            for project_id in project_ids:
                prefix = f"/api/v4/projects/{project_id}/repository/"
                if split.path.startswith(prefix):
                    break
            else:
                self._send_json(404, {"message": "404 Project Not Found"})
                return
            rest = split.path[len(prefix) :]
            if qs.get("ref", ["main"])[0] != "main":
                self._send_json(404, {"message": "404 Tree Not Found"})
                return

            if rest == "tree":
                path = qs.get("path", [""])[0]
                children = _git_tree_children(tree, path)
                if path and not children:
                    self._send_json(404, {"message": "404 Tree Not Found"})
                    return
                entries = [
                    {
                        "id": f"fake-{name}",
                        "name": name,
                        "type": "tree" if kind == "dir" else "blob",
                        "path": f"{path}/{name}" if path else name,
                        "mode": "040000" if kind == "dir" else "100644",
                    }
                    for name, kind in sorted(
                        children.items(), key=lambda item: (item[1] != "dir", item[0])
                    )
                ]
                per_page = min(int(qs.get("per_page", ["20"])[0]), 100)
                page = int(qs.get("page", ["1"])[0])
                chunk = entries[(page - 1) * per_page : page * per_page]
                has_next = page * per_page < len(entries)
                self._send_json(
                    200, chunk, {"X-Next-Page": str(page + 1) if has_next else ""}
                )
                return

            if rest.startswith("files/"):
                filerest = rest[len("files/") :]
                raw = filerest.endswith("/raw")
                encoded_path = filerest[: -len("/raw")] if raw else filerest
                path = urllib.parse.unquote(encoded_path)
                # Real GitLab requires `ref` on the files endpoints (both
                # metadata and /raw) -- omitting it is a 400, not a
                # default-branch fallback like the tree endpoint gets
                # (confirmed live against gitlab.com; GitLabPath resolves
                # and sends it, this just mirrors the real requirement).
                if "ref" not in qs:
                    self._send_json(400, {"error": "ref is missing, ref is empty"})
                    return
                content = tree.get(path)
                if content is None:
                    self._send_json(404, {"message": "404 File Not Found"})
                    return
                if raw:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(content)
                    return
                self._send_json(
                    200,
                    {
                        "file_name": path.rsplit("/", 1)[-1],
                        "file_path": path,
                        "size": len(content),
                    },
                    # The files endpoint describes the file in headers too,
                    # and answers HEAD with those headers alone.
                    {"X-Gitlab-Size": str(len(content))},
                )
                return

            self._send_json(404, {"message": "404 Not Found"})

        do_HEAD = do_GET

        def _send_json(self, status, payload, headers=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve(_GitLabApiHandler) as base_url:
        yield base_url, owner, repo
