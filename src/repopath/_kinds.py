"""`RepoPath`, the family of repository trees on a hosting service, and the
registry of the kinds that make it up."""

from __future__ import annotations

import inspect as _inspect
import io as _io
import re as _re
import stat as _stat
import threading as _threading
import typing as _ty

from pathapi import FileStat
from pathapi.providers import TreePath

if _ty.TYPE_CHECKING:
    from ._backend import BaseRepoBackend

_KINDS: "dict[str, type[RepoPath]]" = {}
"""Kind name -> the class that registered it."""

_DETECTED: "dict[str, type[RepoPath]]" = {}
"""Instance (folded to lower case) -> the kind a call that names no `type`
gets for it."""

_LOCK = _threading.Lock()

_NAME = _re.compile(r"[a-z][a-z0-9_-]*")


class _Kinds(type(TreePath)):
    """The metaclass that lets calling `RepoPath` itself return a path of one
    of its kinds, chosen by `type=` or by the `instance=` it names."""

    def __call__(cls, *args, **kwargs):
        if cls is RepoPath:
            kind = RepoPath._kind_for(
                args, kwargs.pop("type", None), kwargs.get("instance")
            )
            return kind(*args, **kwargs)
        return super().__call__(*args, **kwargs)

    @property
    def __signature__(cls) -> _inspect.Signature:
        """What `inspect.signature()` reads: the constructor a call of the
        class runs, not the `__call__` above."""
        if cls is RepoPath:
            parameter = _inspect.Parameter
            return _inspect.Signature(
                [
                    parameter("segments", parameter.VAR_POSITIONAL),
                    parameter("type", parameter.KEYWORD_ONLY, default=None),
                    parameter("instance", parameter.KEYWORD_ONLY, default=None),
                    parameter("options", parameter.VAR_KEYWORD),
                ]
            )
        signature = _inspect.signature(cls.__init__)
        return signature.replace(parameters=list(signature.parameters.values())[1:])


def _same_definition(a: type, b: type) -> bool:
    """Whether two classes come from one `class` statement run twice (a
    reload, a function body called again): same module, same qualified name."""
    return a.__module__ == b.__module__ and a.__qualname__ == b.__qualname__


def _register(cls: type, name: object, instances: object, replace: bool) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(
            f"TYPE of {cls.__qualname__} must be a lower-case name "
            f"(letters, digits, '_' and '-'), not {name!r}"
        )
    if isinstance(instances, str) or not all(isinstance(i, str) for i in instances):
        raise TypeError(f"INSTANCES of {cls.__qualname__} must be a tuple of str")
    folded = tuple(dict.fromkeys(i.lower() for i in instances))
    with _LOCK:
        held = _KINDS.get(name)
        if held is not None and not (replace or _same_definition(held, cls)):
            raise ValueError(
                f"repository kind {name!r} is already registered by "
                f"{held.__module__}.{held.__qualname__}; pass replace=True in "
                f"the class statement of {cls.__qualname__} to replace it"
            )
        for instance in folded:
            owner = _DETECTED.get(instance)
            if (
                owner is not None
                and owner is not held
                and not (replace or _same_definition(owner, cls))
            ):
                raise ValueError(
                    f"instance {instance!r} is already detected as the "
                    f"{owner.TYPE!r} kind; pass replace=True in the class "
                    f"statement of {cls.__qualname__} to replace it"
                )
        if held is not None:
            for instance in [k for k, v in _DETECTED.items() if v is held]:
                del _DETECTED[instance]
        _KINDS[name] = cls
        for instance in folded:
            _DETECTED[instance] = cls


class RepoPath(TreePath, metaclass=_Kinds):
    """A path in the repository tree of a hosting service, read-only, over the
    service's REST API.

    `RepoPath(*segments, instance=..., type=None, **options)` returns a path
    of one *kind*: a `GitHubPath` or a `GitLabPath`. `type` names the kind
    (`"github"`, `"gitlab"`); without it the kind is detected from `instance`
    (`github.com` and `gitlab.com`), and any other instance is a `ValueError`
    that asks for `type=`. The segments are the path inside the repository;
    the options are those of the kind (`owner=`, `repo=`, `ref=`, `token=`,
    `backend=`). Every kind is a subclass, so `isinstance(path, RepoPath)`
    holds for all of them; `RepoPath.kinds()` lists the registered names.

    A kind is a subclass whose class body sets `TYPE` (its name) and
    `INSTANCES` (the instances it is detected for): defining the class
    registers it. A name or an instance another class holds is a `ValueError`
    unless the class statement passes `replace=True`.

    A path holds the names below the root of the tree, the tree it is in (the
    place) and the backend that reaches the service. Nothing connects when a
    path is built. A path joined from another keeps its place and its
    backend."""

    __slots__ = ("_backend", "_place", "_hint")

    #: The name of the kind, set in the class body of a kind.
    TYPE: "str | None" = None
    #: The instances (hosts) a call that names no `type` gets this kind for.
    INSTANCES: "tuple[str, ...]" = ()

    if _ty.TYPE_CHECKING:
        _segments: "tuple[str, ...]"

        def __init__(
            self,
            *segments: str | RepoPath,
            type: str | None = None,
            instance: str | None = None,
            **options: _ty.Any,
        ) -> None: ...

    def __init_subclass__(cls, replace: bool = False, **kwargs):
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("TYPE")
        if name is not None:
            _register(cls, name, cls.__dict__.get("INSTANCES", ()), replace)

    @staticmethod
    def kinds() -> "tuple[str, ...]":
        """The names of the registered kinds, sorted."""
        with _LOCK:
            return tuple(sorted(_KINDS))

    @staticmethod
    def _kind_for(segments, name: object, instance: object) -> "type[RepoPath]":
        """The kind a call to `RepoPath(...)` makes: the one `name` registers,
        else the one `instance` is detected for, else the one of the first
        segment that is a repository path."""
        with _LOCK:
            kinds = dict(_KINDS)
            detected = dict(_DETECTED)
        known = ", ".join(sorted(kinds)) or "none"
        if name is not None:
            if not isinstance(name, str):
                raise TypeError(f"type= must be a str, not {type(name).__name__!r}")
            try:
                return kinds[name]
            except KeyError:
                raise ValueError(
                    f"no repository kind {name!r}; the registered kinds are: {known}"
                ) from None
        if instance is not None:
            if not isinstance(instance, str):
                raise TypeError(
                    f"instance= must be a str, not {type(instance).__name__!r}"
                )
            try:
                return detected[instance.lower()]
            except KeyError:
                raise ValueError(
                    f"cannot tell the kind of instance {instance!r}; pass type= "
                    f"with one of: {known}"
                ) from None
        for segment in segments:
            if isinstance(segment, RepoPath):
                return segment._family()
        raise TypeError(f"RepoPath needs type= or instance= (kinds: {known})")

    @classmethod
    def _family(cls) -> type:
        """The kind `cls` is, or derives from: its instances can donate a
        place and a backend to a join, and a path of another kind cannot."""
        return next((c for c in cls.__mro__ if c.__dict__.get("TYPE")), RepoPath)

    def _initialize(self, text: str, place: tuple, backend) -> None:
        self._segments = self._parse(text)
        self._place = place
        self._backend = backend
        self._hint = None

    def _derived(self, segments: "tuple[str, ...]"):
        """A path of this class, place and backend with the names `segments`."""
        path = object.__new__(type(self))
        path._segments = segments
        path._place = self._place
        path._backend = self._backend
        path._hint = None
        return path

    def _rebuild(self, text: str):
        return self._derived(self._parse(text))

    def _keywords(self) -> dict:
        """The constructor keywords that name the place of this path."""
        raise NotImplementedError

    def _place_key(self) -> tuple:
        """What of the place `==`, `hash()` and ordering compare."""
        return self._place

    def _identity(self) -> tuple:
        # Relative and rooted spellings name one node: the tree has no
        # working directory.
        return (self._place_key(), tuple(name for name in self._segments if name))

    def __eq__(self, other: object):
        if type(self) is not type(other):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash((type(self), self._identity()))

    def _order_key(self):
        return self._identity()

    def __reduce__(self):
        # The place and the names; a backend travels only when it says it can
        # (its token is a credential), else the receiving process builds a
        # backend of its own.
        keywords = self._keywords()
        if getattr(self._backend, "picklable", False) is True:
            keywords["backend"] = self._backend
        return _rebuild_path, (type(self), self._segments, keywords)

    @property
    def kind(self) -> str:
        """The name of the kind of this path (`"github"`, `"gitlab"`)."""
        return self._family().TYPE

    @property
    def backend(self) -> "BaseRepoBackend":
        """The backend that sends the requests of this path."""
        return self._backend

    def is_local(self) -> bool:
        """Whether the tree is on this machine: never. A repository is on a
        hosting service, so nothing is resolved."""
        return False

    def _pop_stat_hint(self) -> "FileStat | None":
        """The stat that the listing of the parent attached to this child,
        once; the next call asks the service. A hint that knows the type and
        not the size is consumed here and not returned: `stat()` has to give
        a size."""
        hint, self._hint = self._hint, None
        if hint is not None and not hint.size_known:
            return None
        return hint

    def _st_mode(self, *, follow_symlinks=True):
        # The type questions are answered by a type-only hint, without a request.
        hint = self._hint
        if hint is not None and not hint.size_known:
            self._hint = None
            mode = hint.st_mode
            if _stat.S_IFMT(mode):
                return mode
        return super()._st_mode(follow_symlinks=follow_symlinks)

    def _make_child_relpath(self, name: str, stat_hint=None, **kwargs):
        """The child called exactly `name`, with the stat its listing gave."""
        segments = self._segments
        names = ("", name) if segments == ("", "") else (*segments, name)
        child = self._derived(names)
        child._hint = stat_hint
        return child

    def iterdir(self):
        for name, listed in self._scandir():
            yield self._make_child_relpath(name, stat_hint=listed)

    @staticmethod
    def _check_read_mode(mode: str) -> None:
        # `open()` hands `_open()` a canonical mode ("r", "r+", "w", ...);
        # anything but plain "r" would write, and writes are unsupported.
        if mode.replace("b", "").replace("t", "") != "r":
            raise NotImplementedError(f"open(mode={mode!r}): a repository is read-only")

    @staticmethod
    def _reader(data: bytes) -> _ty.BinaryIO:
        # Read-only like a local file opened "rb": a write raises
        # io.UnsupportedOperation instead of landing in a detached buffer.
        return _io.BufferedReader(_io.BytesIO(data))


def _rebuild_path(cls, segments, keywords):
    """Unpickles a path: `cls(**keywords)` with the names it had."""
    path = cls(**keywords)
    path._segments = segments
    return path
