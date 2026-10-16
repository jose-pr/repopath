"""The `requests` transport as a backend asks for it: a `RepoBackend` given a
session builds the transport that an installed distribution declares in the
entry-point group `pathapi.http_transports`, and raises `ImportError`
naming the distribution when there is none."""

from __future__ import annotations

import pytest

from fake_distribution import install, run
from pathapi.http import _transports
from repopath import RepoBackend

# pathapi words the message itself; the transport's name is what this package
# controls.
REQUESTS_NAMED = "'requests'"


class _Entry:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        return self._target


@pytest.fixture
def nothing_declared(monkeypatch):
    """No installed distribution declares a transport."""
    monkeypatch.setattr(_transports, "_declared", lambda: {})


@pytest.mark.parametrize("arguments", [{"session": object()}, {"verify": False}])
def test_a_backend_given_a_session_names_the_distribution(nothing_declared, arguments):
    with pytest.raises(ImportError) as raised:
        RepoBackend(**arguments)
    assert REQUESTS_NAMED in str(raised.value)


def test_a_session_and_a_transport_together_are_refused(monkeypatch):
    monkeypatch.setattr(
        _transports, "_declared", lambda: {"requests": _Entry("requests", object)}
    )
    with pytest.raises(TypeError):
        RepoBackend(transport=object(), session=object())


# --- a distribution that is installed ---------------------------------------

PROVIDER = """
    class Fake:
        def __init__(self, session=None, **arguments):
            self.session = session
            self.arguments = arguments
            self.closed = 0

        def send(self, method, url, *, headers, body=None, timeout, stream=False):
            raise NotImplementedError

        def close(self):
            self.closed += 1
"""


@pytest.fixture
def provider(tmp_path):
    return install(
        tmp_path,
        "fake-transports",
        {"pathapi.http_transports": {"requests": "fake_transports:Fake"}},
        {"fake_transports": PROVIDER},
    )


def test_a_backend_builds_the_installed_transport_from_a_session(provider):
    found = run(
        """
        import json
        from repopath import RepoBackend
        session = object()
        backend = RepoBackend(session=session, verify=False)
        print(json.dumps([
            type(backend.transport).__name__,
            backend.transport.session is session,
            backend.transport.arguments,
        ]))
        """,
        provider,
    )
    assert found == ["Fake", True, {"verify": False}]


def test_the_variable_names_the_installed_transport_a_path_sends_through(provider):
    found = run(
        """
        import json
        from repopath import GitHubPath
        path = GitHubPath("f", owner="o", repo="r")
        transport = path.backend.transport
        print(json.dumps([type(transport).__name__, transport.session]))
        """,
        provider,
        env={"PATHAPI_HTTP_TRANSPORT": "requests"},
    )
    assert found == ["Fake", None]


def test_a_backend_closes_the_transport_it_built_and_not_one_it_was_given(provider):
    found = run(
        """
        import json
        from repopath import RepoBackend

        class Given:
            closed = 0
            def send(self, *args, **kwargs):
                raise NotImplementedError
            def close(self):
                Given.closed += 1

        with RepoBackend() as built:
            transport = built.transport
        with RepoBackend(transport=Given()):
            pass
        print(json.dumps([type(transport).__name__, transport.closed, Given.closed]))
        """,
        provider,
        env={"PATHAPI_HTTP_TRANSPORT": "requests"},
    )
    assert found == ["Fake", 1, 0]
