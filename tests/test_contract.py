"""The read-only part of the shipped path contract, run against the paths of the
two kinds and against the URI paths that delegate to them. The trees are the
loopback fakes of `api_fakes`; nothing reaches a real host."""

import pytest

from pathapi.testing import ReadPathContract
from repo_support import gitea, github, gitlab, make_path


class TestGitHubPathContract(ReadPathContract):
    # Git trees cannot hold an empty directory: empty_dir/ has a .gitkeep.
    supports_empty_directories = False

    @pytest.fixture
    def root(self, github_api_server, http_transport_kind):
        return github("path", github_api_server)


class TestGitLabPathContract(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitlab_api_server, http_transport_kind):
        return gitlab("path", gitlab_api_server)


class TestGiteaPathContract(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitea_api_server, http_transport_kind):
        return gitea("path", gitea_api_server)


class TestGitHubURIPathContract(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, github_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        pytest.importorskip("repopath.uri")
        return github("uri", github_api_server)


class TestGitLabURIPathContract(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitlab_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        pytest.importorskip("repopath.uri")
        return gitlab("uri", gitlab_api_server)


# `repo:` picks the kind from the host, and the fakes are reached through a
# backend that names their API root, so one host serves for each.
class TestRepoURIPathContractOnGitHub(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, github_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        from repopath import uri
        from repo_support import fake_backend

        owner, repo = github_api_server[1:]
        return uri.RepoURIPath(
            f"repo://github.com/{owner}/{repo}",
            backend=fake_backend("github", github_api_server),
        )


class TestGiteaURIPathContract(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitea_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        pytest.importorskip("repopath.uri")
        return gitea("uri", gitea_api_server)


class TestRepoURIPathContractOnGitea(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitea_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        from repopath import uri
        from repo_support import fake_backend

        owner, repo = gitea_api_server[1:]
        return uri.RepoURIPath(
            f"repo://codeberg.org/{owner}/{repo}",
            backend=fake_backend("gitea", gitea_api_server),
        )


class TestRepoURIPathContractOnGitLab(ReadPathContract):
    supports_empty_directories = False

    @pytest.fixture
    def root(self, gitlab_api_server, http_transport_kind):
        pytest.importorskip("uripath")
        from repopath import uri
        from repo_support import fake_backend

        owner, repo = gitlab_api_server[1:]
        return uri.RepoURIPath(
            f"repo://gitlab.com/{owner}/{repo}",
            backend=fake_backend("gitlab", gitlab_api_server),
        )
