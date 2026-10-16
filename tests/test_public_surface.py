"""The public surface: what the package exports and the constructors as a type
checker reads them."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import repopath

PACKAGE = pathlib.Path(repopath.__file__).resolve().parent
HEADER = PACKAGE / "AGENTS.md"

# The names the shipped header documents, by the module they are imported from.
DOCUMENTED = {
    "repopath": {
        "BadReplyError",
        "BaseRepoBackend",
        "GitHubPath",
        "GitLabPath",
        "InsecureTransportWarning",
        "RateLimitError",
        "RepoBackend",
        "RepoPath",
        "RepoPathError",
        "SVNPath",
        "__version__",
    },
    "repopath.exceptions": {
        "BadReplyError",
        "InsecureTransportWarning",
        "RateLimitError",
        "RepoPathError",
    },
}
URI_DOCUMENTED = {"repopath.uri": {"GitHubURIPath", "GitLabURIPath", "RepoURIPath"}}


def _documented():
    found = dict(DOCUMENTED)
    if importlib.util.find_spec("uripath") is not None:
        found.update(URI_DOCUMENTED)
    return found


@pytest.mark.parametrize("module", sorted(_documented()))
def test_documented_names_stay_importable_from_where_they_are_documented(module):
    imported = importlib.import_module(module)
    missing = sorted(
        name for name in _documented()[module] if not hasattr(imported, name)
    )
    assert missing == []


@pytest.mark.parametrize("module", sorted(_documented()))
def test_a_public_module_exports_exactly_the_documented_names(module):
    exported = importlib.import_module(module).__all__
    assert set(exported) == _documented()[module]
    assert len(exported) == len(set(exported))


def test_every_public_module_declares_all_and_the_others_are_private():
    import pkgutil

    public = sorted(
        info.name
        for info in pkgutil.walk_packages(repopath.__path__, "repopath.")
        if not any(part.startswith("_") for part in info.name.split(".")[1:])
    )
    assert public == ["repopath.exceptions", "repopath.uri"]
    for name in ["repopath", *_documented()]:
        module = importlib.import_module(name)
        assert isinstance(module.__all__, (list, tuple)), name
        assert all(hasattr(module, member) for member in module.__all__), name


def test_the_root_re_exports_the_objects_of_the_modules_that_define_them():
    from repopath import exceptions

    for name in DOCUMENTED["repopath.exceptions"]:
        assert getattr(repopath, name) is getattr(exceptions, name)


def _header_import_lines():
    text = HEADER.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
    for block in blocks:
        for line in block.splitlines():
            if re.match(r"(from \S+ import |import )", line):
                yield line


def test_the_import_lines_of_the_header_run():
    for line in _header_import_lines():
        exec(line, {})


def test_inspect_reads_the_constructor_a_call_runs():
    from repopath import GitHubPath, GitLabPath, RepoPath

    assert list(inspect.signature(RepoPath).parameters) == [
        "segments",
        "type",
        "instance",
        "options",
    ]
    for kind in (GitHubPath, GitLabPath):
        assert list(inspect.signature(kind).parameters) == [
            "segments",
            "owner",
            "repo",
            "ref",
            "instance",
            "token",
            "backend",
        ]


CONSUMER = """\
from repopath import (
    BaseRepoBackend,
    GitHubPath,
    GitLabPath,
    RepoBackend,
    RepoPath,
)

backend: BaseRepoBackend = RepoBackend(token="t", timeout=5)
shared = GitHubPath("src/x.py", owner="acme", repo="widgets", backend=backend)
own = GitLabPath("src/x.py", owner="acme/team", repo="widgets", token="t")
anonymous = GitHubPath(owner="acme", repo="widgets", ref="v1", instance="ghe.example")
child = own / "y"
pure = own.parent.with_name("z")
owner: str = own.repo_owner
ref: "str | None" = anonymous.ref
generic = RepoPath("src", owner="acme", repo="widgets", instance="github.com")
named = RepoPath("src", owner="acme", repo="widgets", instance="git.example", type="gitlab")
names: "tuple[str, ...]" = RepoPath.kinds()
kind: str = generic.kind
"""


def _consumer(tmp_path):
    # Outside any dot-directory: pyright silently skips those.
    directory = tmp_path / "consumer"
    directory.mkdir()
    (directory / "consumer.py").write_text(CONSUMER, encoding="utf-8")
    return directory


def test_mypy_accepts_the_constructors(tmp_path, monkeypatch):
    api = pytest.importorskip("mypy.api")
    directory = _consumer(tmp_path)
    monkeypatch.setenv("MYPYPATH", str(PACKAGE.parent))
    out, err, status = api.run(
        [
            "--follow-imports=silent",
            "--no-incremental",
            "--cache-dir=" + str(tmp_path / "mypy-cache"),
            str(directory / "consumer.py"),
        ]
    )
    assert status == 0, out + err


@pytest.mark.allow_program("pyright", "basedpyright")
def test_pyright_accepts_the_constructors(tmp_path):
    checker = shutil.which("pyright") or shutil.which("basedpyright")
    if checker is None:
        pytest.skip("pyright is not installed")
    directory = _consumer(tmp_path)
    (directory / "pyrightconfig.json").write_text(
        json.dumps(
            {"typeCheckingMode": "standard", "extraPaths": [str(PACKAGE.parent)]}
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [checker, "--pythonpath", sys.executable, "consumer.py"],
        capture_output=True,
        text=True,
        cwd=directory,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_module_postpones_the_evaluation_of_annotations():
    # A bare `X | Y` in an annotation must not be evaluated on Python 3.9.
    without = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if "from __future__ import annotations" not in path.read_text(encoding="utf-8")
    )
    assert without == []
