"""`SVNPath`: a Subversion repository served over HTTP by `mod_dav_svn`, read
through WebDAV (`PROPFIND`, `GET`, `OPTIONS`) on `pathapi.http`."""

from __future__ import annotations

import email.utils as _email
import errno as _errno
import re as _re
import stat as _stat
import typing as _ty
import urllib.parse as _urlparse
import xml.etree.ElementTree as _xml

from pathapi import FileStat
from pathapi import providers as _providers
from pathapi.http import drop_body, raise_for_status, server_size

from ._backend import BaseRepoBackend, RepoBackend, translate_repo_errors
from ._hosted import _SAFE_SEGMENT
from ._kinds import RepoPath

#: The most links followed to reach a file, as in the kernel.
_MAX_LINKS = 40

_DAV = "DAV:"
_SVN_DAV = "http://subversion.tigris.org/xmlns/dav/"
_SVN = "http://subversion.tigris.org/xmlns/svn/"

_PROPFIND = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<propfind xmlns="DAV:" xmlns:s="http://subversion.tigris.org/xmlns/dav/" '
    'xmlns:v="http://subversion.tigris.org/xmlns/svn/"><prop>'
    "<resourcetype/><getcontentlength/><getlastmodified/><v:special/>{more}"
    "</prop></propfind>"
)
#: What a listing asks of every child: the type, the size, the time and whether
#: it is a link.
_LIST_BODY = _PROPFIND.format(more="").encode()
#: What a `stat()` and a `checksum()` ask of one entry.
_STAT_BODY = _PROPFIND.format(more="<s:md5-checksum/><s:sha1-checksum/>").encode()
_OPTIONS_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<D:options xmlns:D="DAV:"><D:activity-collection-set/></D:options>'
)
_XML_HEADERS = {"Content-Type": "text/xml; charset=utf-8"}
#: A document type declaration can define entities that expand without bound;
#: Subversion sends none.
_DECLARATION = _re.compile(rb"<!\s*(DOCTYPE|ENTITY)", _re.IGNORECASE)
_STATUS_OK = _re.compile(r"HTTP/\d(\.\d)?\s+200\b")
_HEX = {"md5": 32, "sha1": 40}
_LINK_PREFIX = b"link "
#: The revision of a path that names the head.
_HEAD = "HEAD"

_Props = "dict[str, _xml.Element]"


def checked_url(text: object) -> str:
    """`text` as the root of a tree: an `http` or `https` URL with a host, no
    credentials, query or fragment and no dot segment, without a trailing
    `/`; `TypeError` for a non-string, else `ValueError`."""
    if not isinstance(text, str):
        raise TypeError(f"instance= must be a str, not {type(text).__name__!r}")
    wrong = ValueError(
        f"instance= must be the http or https URL of a repository, not {text!r}"
    )
    if not text or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        raise wrong
    try:
        parts = _urlparse.urlsplit(text)
        host, _port = parts.hostname, parts.port
    except ValueError:
        raise wrong from None
    if (
        parts.scheme.lower() not in ("http", "https")
        or not host
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or "?" in text
        or "#" in text
    ):
        raise wrong
    path = parts.path.rstrip("/")
    if (
        any(_urlparse.unquote(part) in (".", "..") for part in path.split("/"))
        or "\\" in path
    ):
        raise wrong
    return f"{parts.scheme.lower()}://{parts.netloc}{path}"


def checked_revision(value: object) -> "str | None":
    """The revision number `value` names as text, or None for the head
    (`None`, `""`, `"HEAD"`); `TypeError` for another type, `ValueError` for a
    negative number or text that is no number."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.upper() == _HEAD:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"ref= must be a revision number, not {type(value).__name__!r}")
    text = str(value)
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"ref= must be a revision number or 'HEAD', not {value!r}")
    return str(int(text))


def _text(props: dict, tag: str) -> "str | None":
    element = props.get(tag)
    return None if element is None else (element.text or "").strip()


class SVNPath(RepoPath):
    """A path in a Subversion repository read over HTTP (`mod_dav_svn`),
    read-only.

    `SVNPath("trunk/README", instance="https://svn.example.org/repos/project",
    ref=1234)`: `instance` is the URL of the tree the names are relative to,
    normally the root of the repository; `ref` is a revision number (the head
    when omitted, which can move between two requests). `stat()` is one
    `PROPFIND`, a listing is one `PROPFIND` with `Depth: 1`, and the digests
    of `checksum()` come with the stat, without reading the file. A link is a
    file whose text is `link TARGET`: `readlink()` reads it, and a path that
    follows links follows the last name only. `username=` and `password=` send
    `Authorization: Basic`, `token=` a bearer token; `backend=` shares a
    backend between paths."""

    TYPE = "svn"
    INSTANCES = ()
    __slots__ = ()

    if _ty.TYPE_CHECKING:
        _backend: BaseRepoBackend
        _place: "tuple[str, str | None]"

    def __init__(
        self,
        *segments: "str | RepoPath",
        instance: "str | None" = None,
        ref: "int | str | None" = None,
        username: "str | None" = None,
        password: "str | None" = None,
        token: "str | None" = None,
        backend: "BaseRepoBackend | None" = None,
    ):
        text, donor = self._join(segments)
        place = self._place_of(instance, ref, donor)
        self._initialize(
            text, place, self._backend_of(donor, backend, token, username, password)
        )

    def _place_of(self, instance, ref, donor) -> tuple:
        """`(instance, ref)` from the keywords, else from the path this one is
        joined from."""
        inherited = donor._place if donor is not None else (None, None)
        if instance is None:
            if inherited[0] is None:
                raise TypeError(
                    f"{type(self).__name__} needs instance= (or a segment that is "
                    f"a {type(self).__name__})"
                )
            instance = inherited[0]
        else:
            instance = checked_url(instance)
        if ref is None:
            ref = inherited[1]
        else:
            ref = checked_revision(ref)
        return instance, ref

    @staticmethod
    def _backend_of(donor, backend, token, username, password):
        """The backend of a new path: the one given, the donor's, or one built
        from the credentials."""
        if backend is not None and (token or username is not None or password):
            raise TypeError("pass either backend= or credentials, not both")
        if token and (username is not None or password is not None):
            raise TypeError("pass either token= or username= and password=")
        if (username is None) != (password is None):
            raise TypeError("username= and password= go together")
        if backend is not None:
            if not callable(getattr(backend, "request", None)):
                raise TypeError("a backend is an object with a request() method")
            return backend
        if token:
            return RepoBackend(token=token)
        if username is not None:
            return RepoBackend(auth=(username, password))
        return donor._backend if donor is not None else RepoBackend()

    def _keywords(self) -> dict:
        instance, ref = self._place
        return {"instance": instance, "ref": ref}

    def _place_key(self) -> tuple:
        instance, ref = self._place
        scheme, _, rest = instance.partition("://")
        authority, slash, path = rest.partition("/")
        return f"{scheme}://{authority.lower()}{slash}{path}", ref or ""

    def _same_filesystem(self, other: "SVNPath") -> bool:
        return self._place_key() == other._place_key()

    @property
    def instance(self) -> str:
        """The URL the names are relative to, with no trailing `/`."""
        return self._place[0]

    @property
    def ref(self) -> "str | None":
        """The revision this path reads, as text, or None for the head."""
        return self._place[1]

    @property
    def repo_path(self) -> str:
        """The path below `instance`, with no leading or trailing `/`."""
        return "/".join(name for name in self._segments if name)

    def __repr__(self) -> str:
        shown = [repr(self.as_posix()), f"instance={self.instance!r}"]
        if self.ref:
            shown.append(f"ref={self.ref!r}")
        backend = self._backend
        if not (isinstance(backend, RepoBackend) and backend.api_base is None):
            shown.append(f"backend={backend!r}")
        return f"{type(self).__name__}({', '.join(shown)})"

    def __str__(self) -> str:
        return self.as_uri()

    def as_uri(self) -> str:
        """`svn+https://host/path/to/root/names?ref=REV`: the URL of the
        instance with the scheme prefixed by `svn+`, and `?ref=` when the path
        has a revision. It names no credential."""
        names = "/".join(
            _urlparse.quote(name, safe=_SAFE_SEGMENT, errors="surrogateescape")
            for name in self._segments
            if name
        )
        text = f"svn+{self.instance}" + (f"/{names}" if names else "")
        if self.ref:
            text += f"?ref={self.ref}"
        return text

    # -- addresses --------------------------------------------------------

    def _root_url(self) -> str:
        """The URL of the tree this path is in, with no trailing `/`."""
        return self._place[0]

    def _quoted_names(self) -> str:
        names = self.repo_path
        if any(part in (".", "..") for part in names.split("/")):
            raise ValueError(f"A dot segment cannot be part of a request URL: {self}")
        return "/".join(
            _urlparse.quote(part, safe="", errors="surrogateescape")
            for part in names.split("/")
            if part
        )

    def _url(self, *, directory: bool = False) -> str:
        """The URL that names this path: under the root when it reads the
        head, else through the revision stub the server announces. A directory
        is asked for with its trailing `/`."""
        names = self._quoted_names()
        root = self._root_url()
        if self.ref is None:
            url = root + (f"/{names}" if names else "")
        else:
            repository, stub = self._repository()
            parts = _urlparse.urlsplit(root)
            inside = _urlparse.unquote(parts.path)[len(repository) :].strip("/")
            quoted = "/".join(
                _urlparse.quote(part, safe="", errors="surrogateescape")
                for part in inside.split("/")
                if part
            )
            url = f"{parts.scheme}://{parts.netloc}{stub}/{self.ref}/"
            url += "/".join(part for part in (quoted, names) if part)
        if directory and not url.endswith("/"):
            url += "/"
        return url

    def _repository(self) -> "tuple[str, str]":
        """`(root, stub)` of the repository this tree is in: the server path
        of its root and the path under which it serves a revision. Asked of
        the server once per backend."""
        root = self._root_url()
        cache = self._backend_cache()
        key = ("svn_repository", root)
        if cache is not None and key in cache:
            return cache[key]
        resp = self._exchange(
            "OPTIONS", root + "/", headers=_XML_HEADERS, data=_OPTIONS_BODY
        )
        self._expect(resp, 200)
        repository = resp.headers.get("SVN-Repository-Root")
        stub = resp.headers.get("SVN-Rev-Root-Stub")
        if not (repository and stub):
            raise self._bad_reply("a server that names no repository root")
        path = _urlparse.unquote(_urlparse.urlsplit(root).path)
        for value in (repository, stub):
            if not value.startswith("/") or "?" in value:
                raise self._bad_reply("a repository root that is not a path")
        repository = _urlparse.unquote(repository).rstrip("/")
        if not (path == repository or path.startswith(repository + "/")):
            raise self._bad_reply("a repository root that does not hold the instance")
        found = (repository, stub.rstrip("/"))
        if cache is not None:
            cache[key] = found
        return found

    # -- requests ---------------------------------------------------------

    def _exchange(self, method, url, *, headers=None, data=None, stream=False):
        """One request whose status is below 400, or the error `pathlib`
        raises for it. A redirect is returned as it is, never followed."""
        with translate_repo_errors(self):
            resp = self._backend.request(
                method,
                url,
                headers=headers,
                data=data,
                stream=stream,
                allow_redirects=False,
            )
            if resp.status >= 400:
                try:
                    raise_for_status(resp)
                except BaseException:
                    drop_body(resp)
                    raise
        return resp

    def _expect(self, resp, status: int) -> None:
        if resp.status != status:
            drop_body(resp)
            raise self._bad_reply(f"HTTP {resp.status} where {status} was expected")

    def _multistatus(
        self, url: str, depth: str, body: bytes
    ) -> "list[tuple[str, dict]]":
        """The `(path, properties)` of every response of a `PROPFIND`, the
        properties being those the server found (status 200)."""
        headers = {"Depth": depth, **_XML_HEADERS}
        resp = self._exchange("PROPFIND", url, headers=headers, data=body)
        self._expect(resp, 207)
        data = resp.read()
        if _DECLARATION.search(data):
            raise self._bad_reply("a document type declaration")
        try:
            document = _xml.fromstring(data)
        except _xml.ParseError:
            raise self._bad_reply("a reply that is not XML") from None
        if document.tag != f"{{{_DAV}}}multistatus":
            raise self._bad_reply("a reply that is not a multistatus")
        found = []
        for response in document.findall(f"{{{_DAV}}}response"):
            href = response.findtext(f"{{{_DAV}}}href")
            if not href or not href.strip():
                raise self._bad_reply("a response without a href")
            props: dict = {}
            for propstat in response.findall(f"{{{_DAV}}}propstat"):
                status = propstat.findtext(f"{{{_DAV}}}status") or ""
                prop = propstat.find(f"{{{_DAV}}}prop")
                if prop is not None and _STATUS_OK.match(status.strip()):
                    props.update((child.tag, child) for child in prop)
            path = _urlparse.unquote(
                _urlparse.urlsplit(href.strip()).path, errors="surrogateescape"
            )
            found.append((path, props))
        if not found:
            raise self._bad_reply("a multistatus with no response")
        return found

    # -- what a response says about an entry -------------------------------

    def _is_collection(self, props: dict) -> bool:
        kind = props.get(f"{{{_DAV}}}resourcetype")
        return kind is not None and kind.find(f"{{{_DAV}}}collection") is not None

    def _is_link(self, props: dict) -> bool:
        return bool(_text(props, f"{{{_SVN}}}special"))

    def _stat_of(self, props: dict) -> FileStat:
        mtime = 0.0
        modified = _text(props, f"{{{_DAV}}}getlastmodified")
        if modified:
            try:
                mtime = _email.parsedate_to_datetime(modified).timestamp()
            except (TypeError, ValueError, IndexError):
                mtime = 0.0
        if self._is_collection(props):
            return FileStat(st_mtime=mtime, is_dir=True)
        length = _text(props, f"{{{_DAV}}}getcontentlength")
        size = server_size(length) if length is not None else None
        if size is None:
            raise self._bad_reply("a file without a size")
        if self._is_link(props):
            return FileStat(st_mode=_stat.S_IFLNK | 0o777, st_size=size, st_mtime=mtime)
        return FileStat(st_size=size, st_mtime=mtime)

    def _propfind_self(self, body: bytes) -> dict:
        """The properties of this entry (`PROPFIND` with `Depth: 0`)."""
        return self._multistatus(self._url(), "0", body)[0][1]

    # -- the protocols ----------------------------------------------------

    def _lstat(self) -> FileStat:
        hint = self._pop_stat_hint()
        if hint is not None:
            return hint
        return self._stat_of(self._propfind_self(_STAT_BODY))

    def _link_text(self) -> str:
        resp = self._exchange("GET", self._url(), stream=True)
        try:
            self._expect(resp, 200)
            body = resp.read()
        finally:
            resp.close()
        if not body.startswith(_LINK_PREFIX):
            raise self._bad_reply("a link whose text does not start with 'link '")
        return body[len(_LINK_PREFIX) :].decode("utf-8", "surrogateescape")

    def _link_target(self) -> "SVNPath":
        """The path the link here names, relative to its directory. A target
        that leaves the tree (an absolute one, or `..` above the root) does
        not exist."""
        text = self._link_text()
        names = [name for name in self._segments if name][:-1]
        if text.startswith("/"):
            names = None
        else:
            for part in text.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if not names:
                        names = None
                        break
                    names.pop()
                else:
                    names.append(part)
        if names is None:
            raise FileNotFoundError(
                _errno.ENOENT, "A link that leaves the tree", str(self)
            )
        return self._rebuild("/" + "/".join(names))

    def _resolved(self) -> "tuple[SVNPath, FileStat]":
        """The entry at the end of the links from here and its stat."""
        path = self
        for _ in range(_MAX_LINKS + 1):
            found = path._lstat()
            if not _stat.S_ISLNK(found.st_mode):
                return path, found
            path = path._link_target()
        raise OSError(_errno.ELOOP, "Too many levels of symbolic links", str(self))

    def stat(self, *, follow_symlinks=True):
        if follow_symlinks:
            return self._resolved()[1]
        return self._lstat()

    def readlink(self) -> "SVNPath":
        if not _stat.S_ISLNK(self._lstat().st_mode):
            raise OSError(_errno.EINVAL, "Invalid argument", str(self))
        return self._rebuild(self._link_text())

    def _scandir(self):
        url = self._url(directory=True)
        entries = self._multistatus(url, "1", _LIST_BODY)
        asked = _urlparse.unquote(
            _urlparse.urlsplit(url).path, errors="surrogateescape"
        )
        own = next((e for e in entries if e[0].rstrip("/") == asked.rstrip("/")), None)
        own = own if own is not None else entries[0]
        if not self._is_collection(own[1]):
            raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
        prefix = own[0].rstrip("/") + "/"
        for entry in entries:
            path, props = entry
            if entry is own or not path.startswith(prefix):
                continue
            name = path[len(prefix) :].rstrip("/")
            if not _providers.is_safe_child_name(name):
                continue
            yield name, self._stat_of(props)

    def _open(self, mode="r", buffering=-1):
        self._check_read_mode(mode)
        path, found = self._resolved()
        if found.is_dir():
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        resp = path._exchange("GET", path._url(), stream=True)
        try:
            if resp.status != 200:
                drop_body(resp)
                raise self._bad_reply(f"HTTP {resp.status} where 200 was expected")
            return self._reader(resp.read())
        finally:
            resp.close()

    def supported_checksums(self) -> "_ty.FrozenSet[str]":
        return frozenset(_HEX)

    def checksum(self, algorithm: str = "md5") -> str:
        """The `md5` or `sha1` digest of the file as the server states it,
        without reading the file. A link and any other algorithm raise
        `NotImplementedError`: the stored text of a link is not what a read
        returns."""
        name = algorithm.lower() if isinstance(algorithm, str) else None
        if name not in _HEX:
            raise NotImplementedError(f"checksum({algorithm!r}): md5 and sha1 only")
        props = self._propfind_self(_STAT_BODY)
        if self._is_collection(props):
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        if self._is_link(props):
            raise NotImplementedError("the digest of a link is that of its text")
        digest = _text(props, f"{{{_SVN_DAV}}}{name}-checksum")
        if not digest:
            raise NotImplementedError(f"the server states no {name} digest")
        if len(digest) != _HEX[name] or not _re.fullmatch(r"[0-9a-fA-F]+", digest):
            raise self._bad_reply(f"a {name} digest that is not hexadecimal")
        return digest.lower()
