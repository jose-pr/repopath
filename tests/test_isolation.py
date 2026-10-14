"""What importing and building paths needs, and what it never loads.

Each case runs in a fresh interpreter that blocks the import of the named
modules, so nothing is uninstalled and nothing connects."""

from __future__ import annotations

import pytest

from child import run

THIRD_PARTY = ["requests", "urllib3", "uritools", "netimps", "duho"]


def test_importing_the_package_loads_no_uripath_and_no_third_party_library():
    result = run("""
        import sys
        import repopath

        loaded = {"uripath", "repopath.uri", "requests", "urllib3"} & set(sys.modules)
        assert not loaded, loaded
        assert repopath.RepoPath and repopath.GitHubPath and repopath.GitLabPath
        print("ok")
        """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_building_deriving_and_reading_paths_needs_only_the_core():
    result = run(
        """
        from pathapi.http import HTTPResponse
        from repopath import GitHubPath, GitLabPath, RepoBackend, RepoPath

        class Transport:
            def __init__(self):
                self.sent = []

            def send(self, method, url, *, headers, body=None, timeout, stream=False):
                self.sent.append((method, url))
                return HTTPResponse(
                    200,
                    "OK",
                    {"Content-Type": "application/octet-stream"},
                    url,
                    b"hello",
                )

            def close(self):
                pass

        transport = Transport()
        backend = RepoBackend(transport=transport, api_base="http://127.0.0.1:1")
        paths = [
            GitHubPath("src/x", owner="o", repo="r", backend=backend),
            GitLabPath("src/x", owner="g/s", repo="r", backend=backend),
            RepoPath("src/x", owner="o", repo="r", instance="github.com", backend=backend),
        ]
        for path in paths:
            derived = [path / "y", path.parent, path.with_name("z"), path.joinpath("a", "b")]
            assert all(type(p) is type(path) and p.backend is backend for p in derived)
            str(path), repr(path), hash(path), path.as_uri()
        assert paths[0].read_bytes() == b"hello"
        assert transport.sent[0][0] == "GET"
        print("ok")
        """,
        THIRD_PARTY + ["uripath"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_uri_module_without_uripath_names_the_uri_extra():
    result = run(
        """
        try:
            import repopath.uri
        except ImportError as error:
            print(error, error.name)
        """,
        ["uripath"],
    )
    assert result.returncode == 0, result.stderr
    assert 'pip install "repopath[uri]"' in result.stdout
    assert result.stdout.split()[-1] == "uripath"


def test_importing_the_uri_module_loads_no_third_party_library():
    pytest.importorskip("uripath")
    result = run(
        """
        import sys
        import repopath.uri

        loaded = [m for m in ("requests", "urllib3") if sys.modules.get(m) is not None]
        assert not loaded, loaded
        print("ok")
        """,
        ["requests", "urllib3"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
