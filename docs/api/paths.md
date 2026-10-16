# Paths and backends

The paths need no URI and no library beyond `pathapi`.

`GitHubPath` and `GitLabPath` share a base class: the properties it defines
(`owner`, `repo`, `ref`, `instance`, `repo_path`) are listed under each of them.

::: repopath
    options:
      show_if_no_docstring: true
      filters: ["!^_"]
      inherited_members:
        - repo_owner
        - repo
        - ref
        - instance
        - repo_path
        - as_uri
      members:
        - RepoPath
        - GitHubPath
        - GitLabPath
        - SVNPath
        - SourceForgePath
        - RepoBackend
        - BaseRepoBackend
