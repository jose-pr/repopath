"""`RepoPath` and its kinds: which class a call returns, how a kind registers,
and what the registry refuses. The kinds of the tests are defined here, under
names of their own, and forgotten after each test."""

import pickle

import pytest

from repopath import GitHubPath, GitLabPath, RepoPath
from repopath import _kinds


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """Every test starts with the registry it found and leaves it so."""
    monkeypatch.setattr(_kinds, "_KINDS", dict(_kinds._KINDS))
    monkeypatch.setattr(_kinds, "_DETECTED", dict(_kinds._DETECTED))


def _repo(*segments, **keywords):
    keywords.setdefault("owner", "o")
    keywords.setdefault("repo", "r")
    return RepoPath(*segments, **keywords)


# --- the two kinds that ship --------------------------------------------------


def test_the_kinds_that_ship_are_github_gitlab_sourceforge_and_svn():
    assert RepoPath.kinds() == ("github", "gitlab", "sourceforge", "svn")
    assert GitHubPath.kinds() == RepoPath.kinds()


@pytest.mark.parametrize(
    "instance, kind",
    [
        ("github.com", GitHubPath),
        ("GitHub.COM", GitHubPath),
        ("www.github.com", GitHubPath),
        ("gitlab.com", GitLabPath),
        ("GitLab.com", GitLabPath),
        ("www.gitlab.com", GitLabPath),
    ],
)
def test_the_kind_is_detected_from_a_public_instance(instance, kind):
    path = _repo("f", instance=instance)
    assert type(path) is kind
    assert isinstance(path, RepoPath)
    assert path.instance == instance


@pytest.mark.parametrize("name, kind", [("github", GitHubPath), ("gitlab", GitLabPath)])
def test_type_names_the_kind_for_any_instance(name, kind):
    path = _repo("f", instance="git.example", type=name)
    assert type(path) is kind
    assert path.instance == "git.example"


def test_type_without_an_instance_gets_the_default_instance_of_the_kind():
    assert _repo("f", type="github").instance == "github.com"
    assert _repo("f", type="gitlab").instance == "gitlab.com"


def test_type_wins_over_the_instance_it_is_detected_for():
    assert type(_repo("f", instance="github.com", type="gitlab")) is GitLabPath


def test_an_instance_no_kind_is_detected_for_is_a_value_error_naming_the_choices():
    with pytest.raises(ValueError) as excinfo:
        _repo("f", instance="git.example")
    message = str(excinfo.value)
    assert "git.example" in message and "type=" in message
    assert "github" in message and "gitlab" in message


def test_an_unregistered_type_is_a_value_error_listing_the_registered_ones():
    with pytest.raises(ValueError) as excinfo:
        _repo("f", instance="git.example", type="bzr")
    message = str(excinfo.value)
    assert "'bzr'" in message and "github, gitlab, sourceforge, svn" in message


def test_neither_type_nor_instance_is_a_type_error():
    with pytest.raises(TypeError, match="type= or instance="):
        _repo("f")


@pytest.mark.parametrize("keyword", ["type", "instance"])
def test_type_and_instance_must_be_strings(keyword):
    with pytest.raises(TypeError, match=keyword):
        _repo("f", **{"instance": "github.com", keyword: 3})


def test_a_path_joined_from_a_repository_path_is_of_its_kind():
    root = GitLabPath(owner="o", repo="r", instance="git.example")
    child = RepoPath(root, "dir", "f")
    assert type(child) is GitLabPath
    assert (child.instance, child.repo_path) == ("git.example", "dir/f")


def test_a_path_of_one_kind_does_not_join_with_one_of_another():
    hub = GitHubPath("a", owner="o", repo="r")
    lab = GitLabPath("b", owner="o", repo="r")
    with pytest.raises(NotImplementedError):
        GitHubPath(lab, "x")
    with pytest.raises(TypeError):
        hub / lab
    assert hub != lab


# --- a kind of a distribution that is not this one ------------------------------


class _Backend:
    def request(self, *args, **kwargs):
        raise AssertionError("nothing is requested")


def _throwaway(name, instances=(), **statement):
    """A kind with an endpoint of its own: a `url` and no owner or repository."""

    class ThrowawayKindPath(RepoPath, **statement):
        TYPE = name
        INSTANCES = instances
        __slots__ = ()

        def __init__(self, *segments, url=None, instance=None, backend=None):
            text, donor = self._join(segments)
            url = url if url is not None else donor._place[0]
            self._initialize(text, (url,), backend or _Backend())

        def _keywords(self):
            return {"url": self._place[0]}

        def as_uri(self):
            return f"{name}:{self.as_posix()}"

        @property
        def url(self):
            return self._place[0]

    return ThrowawayKindPath


def test_a_kind_defined_in_a_class_body_registers_under_its_type():
    kind = _throwaway("throwaway")
    assert "throwaway" in RepoPath.kinds()
    path = RepoPath("a", "b", type="throwaway", url="svn://h/repo")
    assert type(path) is kind
    assert isinstance(path, RepoPath)
    assert path.url == "svn://h/repo"
    assert path.as_posix() == "a/b"


def test_a_kind_takes_its_own_keywords_and_not_those_of_the_others():
    _throwaway("throwaway")
    with pytest.raises(TypeError):
        RepoPath("a", type="throwaway", owner="o", repo="r")
    with pytest.raises(TypeError):
        RepoPath("a", type="github", url="svn://h")


def test_a_kind_is_detected_for_the_instances_it_lists():
    kind = _throwaway("throwaway", ("svn.example", "WWW.SVN.EXAMPLE"))
    assert type(RepoPath("a", instance="svn.example", url="u")) is kind
    assert type(RepoPath("a", instance="Www.Svn.Example", url="u")) is kind


def test_a_join_of_a_throwaway_kind_keeps_its_place():
    _throwaway("throwaway")
    root = RepoPath(type="throwaway", url="svn://h/repo")
    child = RepoPath(root, "x")
    assert (type(child), child.url) == (type(root), "svn://h/repo")
    assert (root / "y").url == "svn://h/repo"


def test_a_pickle_of_a_throwaway_kind_is_rebuilt_from_its_keywords():
    kind = _throwaway("throwaway")
    # pickle finds a class by module and name.
    kind.__qualname__ = "ThrowawayKindPath"
    globals()["ThrowawayKindPath"] = kind
    try:
        path = RepoPath("a", "b", type="throwaway", url="svn://h/repo")
        assert pickle.loads(pickle.dumps(path)) == path
    finally:
        del globals()["ThrowawayKindPath"]


# --- what the registry refuses ----------------------------------------------------


def test_a_name_another_class_holds_is_refused():
    first = _throwaway("throwaway")
    first.__qualname__ = "First"
    with pytest.raises(ValueError, match="already registered"):
        _throwaway("throwaway")
    assert type(RepoPath(type="throwaway", url="u")) is first


def test_replace_takes_a_name_over():
    first = _throwaway("throwaway")
    first.__qualname__ = "First"
    newer = _throwaway("throwaway", replace=True)
    assert type(RepoPath(type="throwaway", url="u")) is newer


def test_the_same_class_statement_run_again_takes_its_name_over():
    def define():
        class Again(RepoPath):
            TYPE = "again"
            INSTANCES = ("again.example",)
            __slots__ = ()

        return Again

    first, second = define(), define()
    assert first is not second
    assert RepoPath._kind_for((), "again", None) is second
    assert RepoPath._kind_for((), None, "again.example") is second


def test_an_instance_another_kind_is_detected_for_is_refused():
    with pytest.raises(ValueError, match="github.com"):
        _throwaway("throwaway", ("GitHub.com",))
    assert "throwaway" not in RepoPath.kinds()
    assert type(_repo("f", instance="github.com")) is GitHubPath


def test_replace_takes_an_instance_over():
    kind = _throwaway("throwaway", ("github.com",), replace=True)
    assert type(RepoPath("f", instance="github.com", url="u")) is kind
    assert type(_repo("f", instance="www.github.com")) is GitHubPath


@pytest.mark.parametrize("name", ["", "GitHub", "git hub", "1svn", 3])
def test_a_kind_name_is_a_lower_case_name(name):
    with pytest.raises(ValueError, match="TYPE"):
        _throwaway(name)


def test_the_instances_of_a_kind_are_a_tuple_of_strings():
    with pytest.raises(TypeError, match="INSTANCES"):
        _throwaway("throwaway", "svn.example")
    assert "throwaway" not in RepoPath.kinds()


def test_a_class_without_a_type_is_not_a_kind():
    class Helper(RepoPath):
        __slots__ = ()

    assert RepoPath.kinds() == ("github", "gitlab", "sourceforge", "svn")


def test_a_subclass_of_a_kind_is_of_its_family():
    class Mine(GitHubPath):
        __slots__ = ()

    mine = Mine("a", owner="o", repo="r")
    assert Mine._family() is GitHubPath
    assert GitHubPath(mine, "b").repo_owner == "o"
    assert type(RepoPath(mine, "b")) is GitHubPath
    assert RepoPath.kinds() == ("github", "gitlab", "sourceforge", "svn")
