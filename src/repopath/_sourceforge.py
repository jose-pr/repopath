"""`SourceForgePath`: the Subversion tool of a SourceForge project, found
through the REST API of the site and read as an `SVNPath`."""

from __future__ import annotations

import re as _re
import typing as _ty
import urllib.parse as _urlparse

from pathapi.http import json_body, peer_text

from ._backend import BaseRepoBackend, RepoBackend
from ._hosted import _SAFE_SEGMENT, checked_instance
from ._kinds import RepoPath
from ._svn import SVNPath, checked_revision, checked_url

#: The names of projects and of tool mount points on SourceForge.
_NAME = _re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DEFAULT_MOUNT = "code"
_DEFAULT_INSTANCE = "sourceforge.net"


def _checked_name(what: str, value: object, inherited: "str | None") -> str:
    if value is None:
        if inherited is None:
            raise TypeError(f"SourceForgePath needs {what}=")
        return inherited
    if not isinstance(value, str):
        raise TypeError(f"{what}= must be a str, not {type(value).__name__!r}")
    if not _NAME.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"{what}= must be a SourceForge name, not {value!r}")
    return value


class SourceForgePath(SVNPath):
    """A path in the Subversion tool of a SourceForge project, read-only.

    `SourceForgePath("README", project="docutils")` asks
    `https://sourceforge.net/rest/p/docutils/code/` once per backend for the
    kind of the tool at `mount` (`"code"` unless given) and for the URL it is
    served at, and is then an `SVNPath` on that URL: the names are relative to
    the URL SourceForge gives for a checkout (the `trunk` of the project). A
    tool that is not Subversion is `NotImplementedError` naming its kind, and
    a project or tool that does not exist is `FileNotFoundError`. The reads
    are anonymous; `backend=` shares a backend between paths."""

    TYPE = "sourceforge"
    INSTANCES = (_DEFAULT_INSTANCE, "www." + _DEFAULT_INSTANCE)
    __slots__ = ()

    if _ty.TYPE_CHECKING:
        _place: "tuple[str, str | None, str, str]"

    def __init__(
        self,
        *segments: "str | RepoPath",
        project: "str | None" = None,
        mount: "str | None" = None,
        ref: "int | str | None" = None,
        instance: "str | None" = None,
        backend: "BaseRepoBackend | None" = None,
    ):
        text, donor = self._join(segments)
        place = self._place_of(project, mount, ref, instance, donor)
        self._initialize(
            text, place, self._backend_of(donor, backend, None, None, None)
        )

    def _place_of(self, project, mount, ref, instance, donor) -> tuple:  # type: ignore[override]
        """`(instance, ref, project, mount)` from the keywords, else from the
        path this one is joined from, else the defaults."""
        inherited = donor._place if donor is not None else (None, None, None, None)
        if instance is None:
            instance = inherited[0] or _DEFAULT_INSTANCE
        else:
            checked_instance(instance)
        project = _checked_name("project", project, inherited[2])
        if mount is None and inherited[3] is None:
            mount = _DEFAULT_MOUNT
        mount = _checked_name("mount", mount, inherited[3])
        if ref is None:
            ref = inherited[1]
        else:
            ref = checked_revision(ref)
        return instance, ref, project, mount

    def _keywords(self) -> dict:
        instance, ref, project, mount = self._place
        return {"instance": instance, "ref": ref, "project": project, "mount": mount}

    def _place_key(self) -> tuple:
        instance, ref, project, mount = self._place
        return instance.lower(), project, mount, ref or ""

    @property
    def project(self) -> str:
        """The name of the project."""
        return self._place[2]

    @property
    def mount(self) -> str:
        """The mount point of the tool in the project (`code` by default)."""
        return self._place[3]

    @property
    def instance(self) -> str:  # type: ignore[override]
        """The host of the site, as given."""
        return self._place[0]

    def __repr__(self) -> str:
        shown = [
            repr(self.as_posix()),
            f"project={self.project!r}",
        ]
        if self.mount != _DEFAULT_MOUNT:
            shown.append(f"mount={self.mount!r}")
        if self.ref:
            shown.append(f"ref={self.ref!r}")
        if self.instance.lower() != _DEFAULT_INSTANCE:
            shown.append(f"instance={self.instance!r}")
        backend = self._backend
        if not (isinstance(backend, RepoBackend) and backend.api_base is None):
            shown.append(f"backend={backend!r}")
        return f"{type(self).__name__}({', '.join(shown)})"

    def as_uri(self) -> str:
        """`sourceforge://PROJECT/names`, with `?mount=`, `?ref=` and
        `?instance=` for what is not the default."""
        names = "/".join(
            _urlparse.quote(name, safe=_SAFE_SEGMENT, errors="surrogateescape")
            for name in self._segments
            if name
        )
        text = f"sourceforge://{self.project}" + (f"/{names}" if names else "")
        query = []
        if self.mount != _DEFAULT_MOUNT:
            query.append(("mount", self.mount))
        if self.ref:
            query.append(("ref", self.ref))
        if self.instance.lower() != _DEFAULT_INSTANCE:
            query.append(("instance", self.instance))
        if query:
            text += "?" + _urlparse.urlencode(query)
        return text

    def _rest_url(self) -> str:
        """The REST address of the tool: on the site, or on the API root the
        backend names."""
        base = getattr(self._backend, "api_base", None) or f"https://{self.instance}"
        project = _urlparse.quote(self.project, safe="")
        mount = _urlparse.quote(self.mount, safe="")
        return f"{base.rstrip('/')}/rest/p/{project}/{mount}/"

    def _root_url(self) -> str:
        url = self._rest_url()
        cache = self._backend_cache()
        key = ("sourceforge_tool", url)
        if cache is not None and key in cache:
            return cache[key]
        resp = self._exchange("GET", url, headers={"Accept": "application/json"})
        self._expect(resp, 200)
        try:
            tool = json_body(resp, self)
        except ValueError:
            tool = None
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise self._bad_reply("a tool that is not a JSON object with a name")
        kind = tool["name"]
        if kind != "svn":
            raise NotImplementedError(
                f"the {self.mount!r} tool of the SourceForge project "
                f"{self.project!r} is {peer_text(kind, 40)!r}, not Subversion"
            )
        clone = tool.get("clone_url_https_anon")
        try:
            found = checked_url(clone)
        except (TypeError, ValueError):
            raise self._bad_reply(
                "a Subversion tool with no usable clone URL"
            ) from None
        if cache is not None:
            cache[key] = found
        return found
