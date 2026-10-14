# Exceptions

`RepoPathError` is the base of what this package raises on its own. A caller's bad
argument is a plain `ValueError` or `TypeError`, and a failed request is the `OSError`
of the kind `pathlib` raises; the two failures no builtin names are `RateLimitError`
and `BadReplyError`.

::: repopath.exceptions
    options:
      show_if_no_docstring: true
      filters: ["!^_"]
