import tempfile

import pytest

import api_fakes
import gitea_fakes
import svn_fakes
import hermetic
from hermetic import NetworkAccessBlocked  # noqa: F401  (re-exported for tests)


@pytest.fixture
def fixture_tree(tmp_path):
    """A small fixture tree for glob/walk/contract tests:

    root/
      a.txt
      b.py
      .hidden.txt
      sub/
        c.py
        nested/
          d.py
      empty_dir/

    Built by the shipped `pathapi.testing.populate_fixture_tree()`, so
    the in-repo contracts and downstream users share one definition.
    """
    from pathapi.testing import populate_fixture_tree

    return populate_fixture_tree(tmp_path)


@pytest.fixture
def serve_http():
    """The `api_fakes.serve(handler)` context manager, for tests that need a
    one-off handler of their own."""
    return api_fakes.serve


@pytest.fixture
def github_api_server(fixture_tree):
    """A fake of the GitHub contents API serving `fixture_tree`; yields
    `(base_url, owner, repo)` (see `api_fakes.github_api`)."""
    with api_fakes.github_api(fixture_tree) as served:
        yield served


@pytest.fixture
def gitlab_api_server(fixture_tree):
    """A fake of the GitLab REST API v4 serving `fixture_tree`; yields
    `(base_url, owner, repo)` (see `api_fakes.gitlab_api`)."""
    with api_fakes.gitlab_api(fixture_tree) as served:
        yield served


@pytest.fixture
def gitea_api_server(fixture_tree):
    """A fake of the Forgejo REST API serving `fixture_tree`; yields
    `(base_url, owner, repo)` (see `gitea_fakes.gitea_api`)."""
    with gitea_fakes.gitea_api(fixture_tree) as served:
        yield served


@pytest.fixture
def gitea_recorded():
    """A fake Forgejo that replays the recordings of `tests/data/gitea` (see
    `gitea_fakes`); yields its `Served` log. A request with no recording is
    answered 404 and fails the test at teardown."""
    with gitea_fakes.recorded() as served:
        yield served
    assert not served.unrecorded, f"requests with no recording: {served.unrecorded}"


@pytest.fixture
def svn_recorded():
    """A fake Subversion server that replays the recordings of `tests/data/svn`
    (see `svn_fakes`); yields its `Served` log. A request with no recording is
    answered 404 and fails the test at teardown."""
    with svn_fakes.svn_server() as served:
        yield served
    assert not served.unrecorded, f"requests with no recording: {served.unrecorded}"


@pytest.fixture
def svn_fixture_server(fixture_tree):
    """A fake Subversion server serving `fixture_tree` under `/svn/fixture`;
    yields its `Served` log."""
    tree = lambda: svn_fakes.snapshot_directory(fixture_tree)  # noqa: E731
    with svn_fakes.svn_server(recorded=(), tree=tree) as served:
        yield served


@pytest.fixture(params=["path", "uri"])
def flavor(request):
    """The class of path a test runs through: the path of the kind that needs
    no URI (`"path"`), or the URI path of its scheme (`"uri"`, skipped without
    uripath). `repo_support.make_path()` builds either."""
    if request.param == "uri":
        pytest.importorskip("uripath")
        pytest.importorskip("repopath.uri")
    return request.param


def pytest_addoption(parser):
    parser.addoption(
        "--http-transports",
        default="builtin",
        help="the HTTP transports the transport suite runs against, by the "
        "names of pathapi.http.transport_names(), separated by commas "
        "(for example builtin,requests)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: lift the autouse non-loopback network guard for a test "
        "that deliberately resolves or connects to a real host",
    )
    config.addinivalue_line(
        "markers",
        "allow_program(*names): let a test start these programs; any other "
        "except the interpreter and the host probes raises ProcessSpawnBlocked",
    )
    # The session's environment is set before the first test's snapshot of it
    # is taken, and put back when pytest exits.
    home = tempfile.TemporaryDirectory(prefix="repopath-home-")
    patch = pytest.MonkeyPatch()
    hermetic.isolate_environment(patch, home.name)
    config.add_cleanup(home.cleanup)
    config.add_cleanup(patch.undo)


_ENV_BEFORE = pytest.StashKey[dict]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    item.stash[_ENV_BEFORE] = hermetic.environment_snapshot()
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    result = yield
    before = item.stash.get(_ENV_BEFORE, None)
    changed = (
        hermetic.changed_variables(before, hermetic.environment_snapshot())
        if before is not None
        else {}
    )
    if changed:
        hermetic.restore_environment(before)
        raise AssertionError(
            "the test left the process environment changed (use monkeypatch): "
            + ", ".join(f"{k}: {old!r} -> {new!r}" for k, (old, new) in changed.items())
        )
    return result


@pytest.fixture(autouse=True)
def _block_non_loopback_network(request, monkeypatch):
    """Hermetic tests: any connect/sendto/name lookup aimed at a host other
    than loopback raises `NetworkAccessBlocked`, a program other than the
    interpreter raises `ProcessSpawnBlocked`, and a blocked attempt fails the
    test at teardown even if the code under test swallowed the error.
    Autouse rather than opt-in, because the failure it prevents (a refactor
    that quietly sends a monkeypatched HTTP test to the real internet, or a
    DNS lookup that stalls an offline CI runner) is only caught by a guard
    the test author did not have to remember. `@pytest.mark.allow_network`
    lifts the network half for a test that means to use the network."""
    blocked = []
    if not request.node.get_closest_marker("allow_network"):
        hermetic.install_network_guard(monkeypatch, blocked)
    allowed = [
        name
        for marker in request.node.iter_markers("allow_program")
        for name in marker.args
    ]
    hermetic.install_process_guard(monkeypatch, blocked, allowed)
    # Yielded so a test of the guard itself can inspect and clear it.
    yield blocked
    if blocked:
        pytest.fail(f"non-loopback network access attempted: {blocked}")


@pytest.fixture
def unused_tcp_port():
    """A port nobody is listening on, for connection-refused coverage."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def pytest_generate_tests(metafunc):
    if "http_transport_kind" in metafunc.fixturenames:
        kinds = [
            name.strip()
            for name in metafunc.config.getoption("--http-transports").split(",")
            if name.strip()
        ]
        metafunc.parametrize("http_transport_kind", kinds, indirect=True)


@pytest.fixture
def http_transport_kind(request, monkeypatch):
    """Runs the test once per HTTP transport (`--http-transports`): the
    default transport of every backend the test builds is that kind
    (`PATHAPI_HTTP_TRANSPORT`), and `http_fakes.make_transport(kind)` builds
    one of it."""
    monkeypatch.setenv("PATHAPI_HTTP_TRANSPORT", request.param)
    return request.param


@pytest.fixture
def http_transport(http_transport_kind):
    """A transport of the kind under test, closed after the test."""
    from http_fakes import make_transport

    with make_transport(http_transport_kind) as transport:
        yield transport
