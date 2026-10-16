"""The constructor and the identity rules of `GitHubPath` and `GitLabPath`:
where the place comes from, which backend a path has, what makes two paths
equal, how a path is spelled as a URI and shown, and what a pickle carries.
None of these tests sends a request."""

from __future__ import annotations

import copy
import pickle

import pytest

from pathapi import Path
from repopath import BaseRepoBackend, GiteaPath, GitHubPath, GitLabPath, RepoBackend
from repopath import _kinds

KINDS = [GitHubPath, GitLabPath, GiteaPath]


class _Refuses(BaseRepoBackend):
    """A backend that fails the test when it is asked for anything."""

    picklable = False

    def request(self, method, url, **kwargs):
        raise AssertionError(f"a request was made: {method} {url}")


class _Picklable(_Refuses):
    """A backend that says a pickled path may carry it."""

    picklable = True

    def __init__(self, tag):
        self.tag = tag


def test_a_repository_path_is_a_pathapi_path_that_needs_no_uri():
    assert isinstance(GitHubPath(owner="o", repo="r"), Path)
    assert isinstance(GitLabPath(owner="o", repo="r"), Path)


# --- the place -----------------------------------------------------------------


@pytest.mark.parametrize("cls", KINDS)
def test_a_path_without_an_owner_or_a_repository_is_a_type_error(cls):
    with pytest.raises(TypeError, match="owner"):
        cls("f", repo="r")
    with pytest.raises(TypeError, match="repo"):
        cls("f", owner="o")
    with pytest.raises(TypeError, match="owner"):
        cls()


@pytest.mark.parametrize("cls", KINDS)
@pytest.mark.parametrize("keyword", ["owner", "repo"])
def test_the_owner_and_the_repository_are_strings(cls, keyword):
    keywords = {"owner": "o", "repo": "r", keyword: 3}
    with pytest.raises(TypeError, match=keyword):
        cls("f", **keywords)


@pytest.mark.parametrize(
    "instance",
    ["", "a b", "h/x", "u@h", "h?x", "h#x", "h\\x", "h:port", "h:99999", "[::1", "h\n"],
)
@pytest.mark.parametrize("cls", KINDS)
def test_an_instance_is_a_host_and_an_optional_port(cls, instance):
    with pytest.raises(ValueError, match="instance"):
        cls("f", owner="o", repo="r", instance=instance)


@pytest.mark.parametrize("cls", KINDS)
def test_an_instance_must_be_a_string(cls):
    with pytest.raises(TypeError, match="instance"):
        cls("f", owner="o", repo="r", instance=7)


@pytest.mark.parametrize(
    "instance", ["h", "GHE.Example", "h:8080", "[::1]", "[fd00::5]:443", "10.0.0.1"]
)
def test_an_instance_that_is_a_host_is_kept_as_given(instance):
    assert GitHubPath("f", owner="o", repo="r", instance=instance).instance == instance


def test_the_ref_is_a_string_or_none_and_empty_means_none():
    assert GitHubPath(owner="o", repo="r", ref="").ref is None
    with pytest.raises(TypeError, match="ref"):
        GitHubPath(owner="o", repo="r", ref=3)


@pytest.mark.parametrize("cls", KINDS)
def test_a_joined_path_keeps_the_place_and_a_keyword_moves_it(cls):
    root = cls(owner="o", repo="r", ref="v1", instance="h.example")
    same = cls(root, "a", "b")
    assert (same.repo_owner, same.repo, same.ref, same.instance) == (
        "o",
        "r",
        "v1",
        "h.example",
    )
    moved = cls(root, "a", owner="p", repo="q", instance="g.example")
    assert (moved.repo_owner, moved.repo, moved.ref, moved.instance) == (
        "p",
        "q",
        "v1",
        "g.example",
    )
    assert moved.backend is root.backend


# --- the backend -----------------------------------------------------------------


@pytest.mark.parametrize("cls", KINDS)
def test_a_path_builds_a_backend_of_its_own_and_a_join_shares_it(cls):
    root = cls(owner="o", repo="r")
    assert isinstance(root.backend, RepoBackend)
    assert root.backend.token is None
    assert (root / "a").backend is root.backend
    assert root.parent.backend is root.backend
    assert cls(owner="o", repo="r").backend is not root.backend


@pytest.mark.parametrize("cls", KINDS)
def test_backend_and_token_together_are_a_type_error(cls):
    with pytest.raises(TypeError, match="backend= or token="):
        cls(owner="o", repo="r", backend=_Refuses(), token="T")


@pytest.mark.parametrize("cls", KINDS)
def test_a_supplied_backend_is_used_as_it_is(cls):
    backend = _Refuses()
    assert cls("f", owner="o", repo="r", backend=backend).backend is backend


@pytest.mark.parametrize("cls", KINDS)
def test_a_backend_is_an_object_with_a_request_method(cls):
    with pytest.raises(TypeError, match="request"):
        cls(owner="o", repo="r", backend="https://api.github.com")


@pytest.mark.parametrize("cls", KINDS)
def test_a_token_over_a_joined_path_builds_a_new_backend(cls):
    root = cls(owner="o", repo="r")
    assert cls(root, "a", token="T").backend.token == "T"
    assert root.backend.token is None


def test_a_backend_is_a_context_manager():
    with GitHubPath(owner="o", repo="r", token="T").backend as backend:
        assert backend.token == "T"


# --- what is equal -----------------------------------------------------------------


@pytest.mark.parametrize("cls", KINDS)
def test_paths_are_equal_when_class_place_and_names_are(cls):
    a = cls("d", "f", owner="o", repo="r", ref="v1", instance="H.example")
    assert a == cls("/d/f/", owner="o", repo="r", ref="v1", instance="h.EXAMPLE")
    assert hash(a) == hash(
        cls("d/f", owner="o", repo="r", ref="v1", instance="h.example")
    )
    for other in (
        cls("d", "f", owner="o2", repo="r", ref="v1", instance="h.example"),
        cls("d", "f", owner="o", repo="r2", ref="v1", instance="h.example"),
        cls("d", "f", owner="o", repo="r", ref="v2", instance="h.example"),
        cls("d", "f", owner="o", repo="r", ref="v1", instance="g.example"),
        cls("d", "g", owner="o", repo="r", ref="v1", instance="h.example"),
    ):
        assert a != other


def test_the_backend_is_not_part_of_what_a_path_is_equal_to():
    one = GitHubPath("f", owner="o", repo="r", backend=_Refuses())
    other = GitHubPath("f", owner="o", repo="r", token="T")
    assert one == other and hash(one) == hash(other)


def test_paths_of_different_kinds_are_not_equal():
    assert GitHubPath("f", owner="o", repo="r") != GitLabPath("f", owner="o", repo="r")


def test_paths_order_by_place_and_names():
    paths = [
        GitHubPath("b", owner="o", repo="r"),
        GitHubPath("a", owner="o", repo="r", ref="v1"),
        GitHubPath("a", owner="o", repo="r"),
    ]
    assert [p.as_posix() for p in sorted(paths)] == ["a", "b", "a"]
    assert sorted(paths)[0].ref is None


def test_paths_are_one_tree_when_their_api_root_repository_and_ref_are():
    a = GitHubPath("f", owner="o", repo="r", backend=_Refuses())
    b = GitHubPath("g", owner="o", repo="r", token="T")
    assert a._same_filesystem(b)
    assert not a._same_filesystem(GitHubPath("f", owner="o", repo="x"))
    assert not a._same_filesystem(GitHubPath("f", owner="o", repo="r", ref="v1"))
    other_root = GitHubPath(
        "f", owner="o", repo="r", backend=RepoBackend(api_base="http://127.0.0.1:1")
    )
    assert not a._same_filesystem(other_root)


# --- how a path is spelled and shown -----------------------------------------------


@pytest.mark.parametrize(
    "path, uri",
    [
        (
            GitHubPath("src", "a b.py", owner="o", repo="r"),
            "github://github.com/o/r/src/a%20b.py",
        ),
        (
            GitHubPath("src", owner="o", repo="r", ref="feat/x&y"),
            "github://github.com/o/r/src?ref=feat%2Fx%26y",
        ),
        (
            GitHubPath("f", owner="o", repo="r", instance="ghe.example:8443"),
            "github://ghe.example:8443/o/r/f",
        ),
        (GitHubPath(owner="o", repo="r"), "github://github.com/o/r"),
        (GitHubPath("-", "f", owner="o", repo="r"), "github://github.com/o/r/-/f"),
        (GitLabPath("f", owner="o", repo="r"), "gitlab://gitlab.com/o/r/f"),
        (
            GitLabPath("f", owner="g/s", repo="r"),
            "gitlab://gitlab.com/g/s/r/-/f",
        ),
        (
            GitLabPath("-", "f", owner="o", repo="r", ref="main"),
            "gitlab://gitlab.com/o/r/-/-/f?ref=main",
        ),
        (
            GitLabPath("f", owner="o", repo="r", instance="[fd00::5]"),
            "gitlab://[fd00::5]/o/r/f",
        ),
    ],
)
def test_a_path_is_spelled_as_the_uri_of_its_kind(path, uri):
    assert path.as_uri() == uri
    assert str(path) == uri


@pytest.mark.parametrize("cls", KINDS)
def test_the_text_of_a_path_after_the_tree_is_its_names(cls):
    path = cls("a", "b", owner="o", repo="r")
    assert path.as_posix() == "a/b"
    assert (path.name, path.suffix, path.stem) == ("b", "", "b")
    assert path.parent.as_posix() == "a"
    assert str(path.parent.parent) == str(cls(owner="o", repo="r"))


def test_repr_names_the_place_and_never_a_credential():
    path = GitHubPath(
        "a",
        "b",
        owner="o",
        repo="r",
        ref="v1",
        instance="ghe.example",
        token="SECRET",
    )
    text = repr(path)
    assert (
        text
        == "GitHubPath('a/b', owner='o', repo='r', ref='v1', instance='ghe.example')"
    )
    assert "SECRET" not in text
    assert repr(GitLabPath(owner="a/b", repo="r")) == (
        "GitLabPath('', owner='a/b', repo='r')"
    )


def test_repr_names_a_backend_that_is_not_the_plain_one():
    custom = repr(GitHubPath(owner="o", repo="r", backend=_Refuses()))
    assert "backend=<" in custom
    rooted = repr(
        GitHubPath(
            owner="o", repo="r", backend=RepoBackend(api_base="http://h/x", token="T")
        )
    )
    assert rooted.endswith("backend=RepoBackend(api_base='http://h/x'))")
    assert "T'" not in rooted


def test_a_repository_is_never_this_machine():
    assert GitHubPath(owner="o", repo="r").is_local() is False


def test_building_and_joining_paths_asks_nothing_of_the_backend():
    backend = _Refuses()
    root = GitHubPath(owner="o", repo="r", backend=backend)
    child = root / "dir" / "f.txt"
    assert child.relative_to(root).as_posix() == "dir/f.txt"
    assert child.with_suffix(".md").name == "f.md"
    assert child.with_name("g").parent == root / "dir"
    assert [p.as_posix() for p in child.parents][:2] == ["dir", ""]
    assert child.is_relative_to(root)
    assert child.joinpath("..").as_posix() == "dir/f.txt/.."


# --- copies and pickles ------------------------------------------------------------


@pytest.mark.parametrize("cls", KINDS)
def test_a_copy_shares_the_backend(cls):
    path = cls("f", owner="o", repo="r", ref="v1", token="T")
    assert copy.copy(path).backend is path.backend
    assert copy.deepcopy(path).backend is path.backend
    assert copy.copy(path) == path


@pytest.mark.parametrize("cls", KINDS)
def test_a_pickle_carries_the_place_and_the_names_and_never_a_token(cls):
    path = cls(
        "a",
        "b",
        owner="o",
        repo="r",
        ref="v1",
        instance="h.example",
        token="s3cr3t-t0ken",
    )
    again = pickle.loads(pickle.dumps(path))
    assert again == path
    assert (again.repo_owner, again.repo, again.ref, again.instance) == (
        "o",
        "r",
        "v1",
        "h.example",
    )
    assert again.backend.token is None
    assert b"s3cr3t-t0ken" not in pickle.dumps(path)


def test_a_backend_that_says_it_may_be_pickled_travels_with_the_path():
    backend = _Picklable("one")
    path = GitHubPath("f", owner="o", repo="r", backend=backend)
    again = pickle.loads(pickle.dumps(path))
    assert again == path
    assert again.backend.tag == "one"


def test_a_pickle_of_a_path_keeps_its_names_when_they_are_rooted():
    path = GitHubPath("/a/b", owner="o", repo="r")
    assert pickle.loads(pickle.dumps(path)).segments == path.segments


def test_the_registered_kinds_are_untouched_by_building_paths():
    assert _kinds.RepoPath.kinds() == (
        "gitea",
        "github",
        "gitlab",
        "sourceforge",
        "svn",
    )
