"""`repopath.__version__` and the places that name it."""

from __future__ import annotations

import importlib.metadata
import re

import repopath


def test_the_version_is_the_installed_distributions():
    assert repopath.__version__ == importlib.metadata.version("repopath")
    assert re.fullmatch(r"\d+(\.\d+)*([a-z]+\d+)?", repopath.__version__)
    assert "__version__" in repopath.__all__


def test_the_version_is_read_from_metadata_and_not_written_in_the_source():
    import pathlib

    source = (pathlib.Path(repopath.__file__).parent / "_version.py").read_text(
        encoding="utf-8"
    )
    assert "importlib" in source or "metadata" in source
    assert repopath.__version__ not in source
