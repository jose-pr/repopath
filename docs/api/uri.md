# The `repo:`, `github:`, `gitlab:`, `gitea:`, `svn+https:`, `svn+http:` and `sourceforge:` schemes

Needs `uripath`: `pip install "repopath[uri]"`.

::: repopath.uri
    options:
      show_if_no_docstring: true
      filters: ["!^_"]
      inherited_members:
        - kind
        - instance
        - repo_owner
        - repo
        - repo_path
        - ref
        - project
        - mount
