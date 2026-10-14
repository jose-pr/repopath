"""A GitLab listing that knows a file's type and not its size costs one request
per directory to walk, and none per file. A `stat()` of a listed child still
asks the server for its real size."""

import pytest

from http_fakes import spy_on_sends
from repo_support import gitlab


@pytest.fixture
def sent(monkeypatch, http_transport_kind):
    """`[(method, path)]` of every request sent, by either transport."""
    return spy_on_sends(
        monkeypatch,
        lambda method, url, **kwargs: (
            method,
            "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/",
        ),
    )


def _kinds(sent):
    """How many tree listings and how many single-file requests were made."""
    trees = [r for r in sent if "/repository/tree" in r[1]]
    files = [r for r in sent if "/repository/files/" in r[1]]
    return len(trees), len(files)


def test_gitlab_walk_sends_one_request_per_directory_and_none_per_file(
    flavor, gitlab_api_server, sent
):
    root = gitlab(flavor, gitlab_api_server)

    walked = [(top, files) for top, _dirs, files in root.walk()]

    names = sorted(name for _top, files in walked for name in files)
    # The six files of the fixture tree (git cannot hold an empty directory,
    # so the fake keeps a `.gitkeep` in it).
    assert names == [".gitkeep", ".hidden.txt", "a.txt", "b.py", "c.py", "d.py"]
    trees, files = _kinds(sent)
    assert files == 0
    assert trees == len(walked)


def test_gitlab_stat_of_a_listed_blob_is_exact_and_one_request(
    flavor, gitlab_api_server, sent
):
    root = gitlab(flavor, gitlab_api_server)
    child = next(p for p in root.iterdir() if p.name == "a.txt")
    sent.clear()

    stat = child.stat()

    assert (stat.st_size, stat.size_known) == (1, True)
    assert _kinds(sent) == (0, 1)


def test_gitlab_type_questions_about_listed_blobs_cost_nothing(
    flavor, gitlab_api_server, sent
):
    root = gitlab(flavor, gitlab_api_server)
    children = {c.name: c for c in root.iterdir()}
    others = {c.name: c for c in root.iterdir()}
    sent.clear()

    # A hint answers one question (it is single-use), so each is asked of its
    # own listed child.
    assert children["a.txt"].is_file() is True
    assert others["a.txt"].is_dir() is False
    assert children["sub"].is_dir() is True
    assert others["sub"].is_file() is False
    assert sent == []


def test_a_github_listing_gives_every_child_its_size(flavor, github_api_server, sent):
    from repo_support import github

    root = github(flavor, github_api_server)
    children = {c.name: c for c in root.iterdir()}
    sent.clear()

    assert children["a.txt"].stat().st_size == 1
    assert children["sub"].stat().is_dir()
    assert sent == []
