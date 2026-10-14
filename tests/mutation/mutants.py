"""The mutation list: one single-line change to a guard of the library per
entry, and the tests that must fail because of it.

`run.py` applies each entry to a temporary copy of `src/repopath`. `old`
is the exact text of the line (or lines) to replace and must occur once in
`file`, which is relative to `src/repopath`; when the code moves on and
the text goes, the entry is reported stale until it is re-expressed. `tests`
are pytest arguments (node ids, or a file when any of its tests may be the
one to catch the mutant) that all pass on the unmutated code. README.md says
how to add an entry.
"""

from __future__ import annotations

from typing import NamedTuple


class Mutant(NamedTuple):
    id: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]
    note: str
    platform: str | None = None


MUTANTS: list[Mutant] = []


def mutant(
    id: str,
    file: str,
    old: str,
    new: str,
    tests,
    note: str,
    platform: str | None = None,
) -> None:
    """Add an entry to the list. `tests` is one pytest argument or a sequence;
    `platform` is the `os.name` its tests need ("nt"), or None for any."""
    if isinstance(tests, str):
        tests = (tests,)
    MUTANTS.append(Mutant(id, file, old, new, tuple(tests), note, platform))


# --- the backend: what is sent, what is warned about, what an error says -----------

_REQUESTS = "tests/test_requests.py"
_TRANSPORT = "tests/test_transport.py"
_REPO = "tests/test_repo.py"
_REPLIES = "tests/test_replies.py"

mutant(
    "backend-token-is-not-sent",
    "_backend.py",
    '            auth=f"Bearer {self.token}" if self.token else None,\n',
    "            auth=None,\n",
    f"{_REQUESTS}::test_token_over_plain_http_to_another_host_warns_and_is_still_sent",
    "the bearer token never reaches the API",
)
mutant(
    "backend-warns-without-a-credential",
    "_backend.py",
    "    if not authorized:\n        return\n",
    "    if False:\n        return\n",
    f"{_REQUESTS}::test_plain_http_without_a_credential_does_not_warn",
    "plain http warns although nothing secret is sent",
)
mutant(
    "backend-warns-for-loopback",
    "_backend.py",
    "    if _is_loopback(host):\n        return\n",
    "    if False:\n        return\n",
    f"{_REQUESTS}::test_token_to_loopback_or_https_does_not_warn",
    "a token sent to this machine over plain http warns",
)
mutant(
    "backend-header-credential-does-not-warn",
    "_backend.py",
    '        authorized = bool(self.token) or any(\n            name.lower() == "authorization" for name in (*self.headers, *sent)\n        )\n',
    "        authorized = bool(self.token)\n",
    f"{_REQUESTS}::test_authorization_header_over_plain_http_warns",
    "an Authorization header given in headers= goes over plain http without a warning",
)
mutant(
    "backend-reason-phrase-unescaped",
    "_backend.py",
    '        reason = peer_text(response.reason or "")\n',
    '        reason = response.reason or ""\n',
    f"{_TRANSPORT}::test_the_reason_phrase_of_an_api_reply_is_shown_escaped",
    "an API error's reason phrase reaches the message as the server sent it",
)
mutant(
    "backend-reset-header-unescaped",
    "_backend.py",
    '        when = f"resets at {peer_text(reset, 40)}"\n',
    '        when = f"resets at {reset}"\n',
    f"{_TRANSPORT}::test_a_rate_limit_header_is_shown_escaped_and_cut",
    "a rate-limit header reaches the message as the server sent it",
)
mutant(
    "backend-plain-403-is-a-rate-limit",
    "_backend.py",
    '    if response.status == 403 and remaining != "0" and retry_after is None:\n        return None\n',
    "    if False:\n        return None\n",
    f"{_REPO}::test_plain_403_is_still_permission_error",
    "a 403 that is not a rate limit is reported as one",
)
mutant(
    "backend-retry-after-is-ignored",
    "_backend.py",
    "    if retry_after is not None:\n        when =",
    "    if False:\n        when =",
    f"{_REPO}::test_github_primary_and_secondary_rate_limits_raise_eagain",
    "a secondary rate limit does not say when to retry",
)
mutant(
    "backend-repr-shows-the-token",
    "_backend.py",
    '        return f"{type(self).__name__}(api_base={self.api_base!r})"\n',
    '        return f"{type(self).__name__}(api_base={self.api_base!r}, token={self.token!r})"\n',
    f"{_REQUESTS}::test_no_display_form_of_a_repository_path_shows_the_token",
    "repr() of a backend, and of a path with a custom backend, names the token",
)
mutant(
    "backend-session-is-dropped",
    "_backend.py",
    '            transport = get_transport("requests")(session, **requests_args)\n',
    "            transport = None\n",
    "tests/test_session.py::test_a_backend_builds_the_installed_transport_from_a_session",
    "RepoBackend(session=...) sends with the built-in transport and drops the session",
)

# --- the place and the identity of a path ---------------------------------------------

_HOSTED = "tests/test_hosted_paths.py"

mutant(
    "hosted-owner-or-repo-may-be-a-dot-segment",
    "_hosted.py",
    '    if any(part in ("", ".", "..") for part in parts) or (not nested and "/" in value):\n',
    "    if False:\n",
    "tests/test_requests.py::test_github_owner_or_repo_that_is_not_one_name_is_refused",
    "an owner or a repository can climb out of the repository's API root",
)
mutant(
    "hosted-instance-may-hold-anything",
    "_hosted.py",
    "    if not text or _FORBIDDEN_IN_INSTANCE & set(text):\n",
    "    if not text:\n",
    f"{_HOSTED}::test_an_instance_is_a_host_and_an_optional_port",
    "an instance can hold a slash, an at sign or a space and so redirect the API URL",
)
mutant(
    "hosted-instance-case-matters",
    "_hosted.py",
    '        return instance.lower(), owner, repo, ref or ""\n',
    '        return instance, owner, repo, ref or ""\n',
    f"{_HOSTED}::test_paths_are_equal_when_class_place_and_names_are",
    "two spellings of one host are two trees",
)
mutant(
    "hosted-dot-segment-reaches-the-api-url",
    "_hosted.py",
    '        if any(part in (".", "..") for part in text.split("/")):\n',
    "        if False:\n",
    f"{_REQUESTS}::test_github_dot_segment_cannot_reach_the_api_url",
    "a .. name makes a request outside the repository",
)
mutant(
    "hosted-reply-shape-is-not-checked",
    "_hosted.py",
    "        if not isinstance(data, expected):\n",
    "        if False:\n",
    f"{_REPLIES}::test_github_listing_of_a_reply_that_is_not_a_directory_is_eio",
    "JSON of the wrong shape is read as if it were right",
)
mutant(
    "hosted-negative-size-is-accepted",
    "_hosted.py",
    "        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:\n",
    "        if isinstance(size, int) and not isinstance(size, bool):\n",
    f"{_REPLIES}::test_github_stat_of_a_reply_that_is_not_a_contents_object_is_eio",
    "a negative size is reported as the size of a file",
)
mutant(
    "hosted-backend-with-a-token-is-accepted",
    "_hosted.py",
    "        if backend is not None and token is not None:\n",
    "        if False:\n",
    f"{_HOSTED}::test_backend_and_token_together_are_a_type_error",
    "a token given beside a backend is silently dropped",
)
mutant(
    "hosted-pickle-carries-the-backend",
    "_kinds.py",
    '        if getattr(self._backend, "picklable", False) is True:\n',
    "        if True:\n",
    f"{_HOSTED}::test_a_pickle_carries_the_place_and_the_names_and_never_a_token",
    "a pickle of a path with a token carries the token",
)
mutant(
    "kinds-a-name-another-class-holds-is-replaced",
    "_kinds.py",
    "        if held is not None and not (replace or _same_definition(held, cls)):\n",
    "        if False:\n",
    "tests/test_kinds.py::test_a_name_another_class_holds_is_refused",
    "a second kind silently takes the name of the first",
)
mutant(
    "kinds-an-instance-another-kind-holds-is-replaced",
    "_kinds.py",
    "                owner is not None\n                and owner is not held\n                and not (replace or _same_definition(owner, cls))\n",
    "                False\n",
    "tests/test_kinds.py::test_an_instance_another_kind_is_detected_for_is_refused",
    "a new kind takes the detection of github.com from the one that ships",
)
mutant(
    "kinds-detection-is-case-sensitive",
    "_kinds.py",
    "                return detected[instance.lower()]\n",
    "                return detected[instance]\n",
    "tests/test_kinds.py::test_the_kind_is_detected_from_a_public_instance",
    "GitHub.COM is not recognised as github.com",
)
mutant(
    "kinds-type-wins-over-the-instance-no-more",
    "_kinds.py",
    "        if name is not None:\n            if not isinstance(name, str):",
    "        if False:\n            if not isinstance(name, str):",
    "tests/test_kinds.py::test_type_wins_over_the_instance_it_is_detected_for",
    "type= is ignored when the instance can be detected",
)
mutant(
    "kinds-paths-of-two-kinds-join",
    "_kinds.py",
    '        return next((c for c in cls.__mro__ if c.__dict__.get("TYPE")), RepoPath)\n',
    "        return RepoPath\n",
    "tests/test_kinds.py::test_a_path_of_one_kind_does_not_join_with_one_of_another",
    "a GitHubPath takes the place of a GitLabPath it is joined with",
)
mutant(
    "kinds-type-only-hint-is-returned-as-a-stat",
    "_kinds.py",
    "        if hint is not None and not hint.size_known:\n            return None\n        return hint\n",
    "        return hint\n",
    "tests/test_listing_hints.py::test_gitlab_stat_of_a_listed_blob_is_exact_and_one_request",
    "stat() of a listed GitLab file answers with a size the listing never had",
)

# --- GitHub ---------------------------------------------------------------------------

mutant(
    "github-head-believes-a-reply-that-is-not-the-api-s",
    "_github.py",
    '            if "X-GitHub-Media-Type" not in resp.headers:\n                return None\n            raise_for_status(resp)',
    "            raise_for_status(resp)",
    "tests/test_github_stat.py::test_a_reply_that_is_not_the_api_s_own_is_not_believed",
    "a proxy's reply to a HEAD is read as the size of the file, or as its absence",
)
mutant(
    "github-head-takes-an-encoded-length",
    "_github.py",
    '            or headers.get("Content-Encoding", "identity").strip().lower() != "identity"\n',
    "            or False\n",
    "tests/test_github_stat.py::test_a_reply_that_states_no_file_size_is_followed_by_the_get",
    "the Content-Length of a gzip reply is read as the size of the file",
)
mutant(
    "github-listing-keeps-unsafe-names",
    "_github.py",
    '            if not _providers.is_safe_child_name(entry["name"]):\n                continue\n',
    "            if False:\n                continue\n",
    f"{_REPO}::test_github_listing_skips_names_that_are_not_one_component",
    "a listed name such as .. becomes a child",
)
mutant(
    "github-cap-is-not-noticed",
    "_github.py",
    "        if len(entries) >= self._CONTENTS_LIMIT:\n",
    "        if len(entries) > self._CONTENTS_LIMIT:\n",
    f"{_REPO}::test_github_listing_past_contents_cap_is_complete",
    "a directory of more than 1,000 entries lists the first 1,000",
)
mutant(
    "github-truncated-tree-is-a-listing",
    "_github.py",
    '        if data.get("truncated"):\n',
    "        if False:\n",
    f"{_REPLIES}::test_github_truncated_tree_is_still_eio",
    "a partial Git tree is returned as a complete listing",
)
mutant(
    "github-a-portal-page-is-a-file",
    "_github.py",
    '        if media_type == "text/html" and "X-GitHub-Media-Type" not in resp.headers:\n',
    "        if False:\n",
    f"{_REPLIES}::test_github_read_of_an_html_page_the_api_did_not_send_is_eio",
    "a captive portal's page is read as the content of a file",
)
mutant(
    "repo-write-mode-reads",
    "_kinds.py",
    '        if mode.replace("b", "").replace("t", "") != "r":\n',
    "        if False:\n",
    f"{_REPO}::test_github_write_modes_raise_not_implemented",
    "open('w') on a repository path returns a reader",
)

# --- GitLab ---------------------------------------------------------------------------

mutant(
    "gitlab-listing-stops-at-the-first-page",
    "_gitlab.py",
    "            if not next_page.isdigit() or int(next_page) <= page:\n",
    "            if True:\n",
    f"{_REPO}::test_gitlab_listing_over_one_page_is_complete",
    "a directory of more than 100 entries lists the first 100",
)
mutant(
    "gitlab-refused-head-is-an-error",
    "_gitlab.py",
    "                if resp.status not in (405, 501):\n",
    "                if True:\n",
    "tests/test_replies.py::test_gitlab_stat_falls_back_to_the_metadata_without_a_usable_head",
    "a server that refuses HEAD makes stat() fail instead of asking for the metadata",
)
mutant(
    "gitlab-listing-keeps-unsafe-names",
    "_gitlab.py",
    '            if not _providers.is_safe_child_name(entry["name"]):\n                continue\n',
    "            if False:\n                continue\n",
    f"{_REPO}::test_gitlab_listing_skips_names_that_are_not_one_component",
    "a listed name such as .. becomes a child",
)
mutant(
    "gitlab-a-blob-hint-claims-a-size",
    "_gitlab.py",
    "                hint = FileStat(is_dir=False, size_known=False)\n",
    "                hint = FileStat(is_dir=False)\n",
    f"{_REPO}::test_gitlab_scandir_blob_entries_are_type_only_hints",
    "a listed GitLab file is given a size of 0",
)
mutant(
    "gitlab-separator-is-not-written",
    "_gitlab.py",
    '        if len(owner) > 1 or "-" in names:\n',
    "        if len(owner) > 1:\n",
    f"{_HOSTED}::test_a_path_is_spelled_as_the_uri_of_its_kind",
    "the URI of a path with a name - reads as a project in a subgroup",
)
mutant(
    "gitlab-separator-is-searched-too-early",
    "_gitlab.py",
    '            index = list(parts).index("-", 2)\n',
    '            index = list(parts).index("-", 1)\n',
    "tests/test_uri_repo.py::test_gitlab_reads_the_separator_that_ends_the_project",
    "a - right after the owner is read as the separator",
)

# --- the schemes of uripath ---------------------------------------------------------------

_URI = "tests/test_uri_repo.py"

mutant(
    "uri-posix-text-shows-the-user",
    "uri.py",
    '        return f"{host}:{self.path}" if host else self.path\n',
    "        return str(self.source.reveal()) + self.path\n",
    f"{_URI}::test_the_token_is_the_password_or_else_the_bare_user_and_is_never_shown",
    "as_posix() of a repository path shows the token",
)
mutant(
    "uri-token-is-the-user-only",
    "uri.py",
    "        return RepoBackend(token=password or user or None)\n",
    "        return RepoBackend(token=user or None)\n",
    f"{_URI}::test_the_token_is_the_password_or_else_the_bare_user_and_is_never_shown",
    "the password slot of a git-style userinfo is not read as the token",
)
mutant(
    "uri-repo-detects-any-host",
    "uri.py",
    "        if kind is None or not issubclass(kind, HostedRepoPath):\n",
    "        if False:\n",
    f"{_URI}::test_repo_refuses_a_host_it_cannot_detect_and_names_the_pinned_schemes",
    "repo:// on a host no kind is detected for is accepted",
)
mutant(
    "uri-dash-child-reads-as-the-separator",
    "uri.py",
    '        if name == "-" and self._separator_index() is None and len(self.segments) > 2:\n',
    "        if False:\n",
    f"{_URI}::test_a_directory_named_dash_gets_the_separator_in_front_of_it",
    "the child of a project called - reads as the end of the project path",
)
mutant(
    "uri-error-keeps-the-name-of-the-core-path",
    "uri.py",
    '                for attribute in ("strerror", "filename", "filename2"):\n',
    '                for attribute in ("filename", "filename2"):\n',
    f"{_URI}::test_an_error_names_the_uri_and_not_the_path_it_was_delegated_to",
    "an error of a repo: path names github:// in its message",
)
mutant(
    "uri-path-is-resolved-as-local",
    "uri.py",
    '    def is_local(self) -> bool:\n        """Whether the tree is on this machine: never. The host of the URI is\n        a hosting service and is not resolved."""\n        return False\n',
    '    def is_local(self) -> bool:\n        """Whether the tree is on this machine: never. The host of the URI is\n        a hosting service and is not resolved."""\n        return True\n',
    f"{_URI}::test_a_uri_path_is_never_this_machine",
    "a repository path is taken for a local one",
)
