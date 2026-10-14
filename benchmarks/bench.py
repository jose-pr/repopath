"""repopath benchmark runner.

Run `python benchmarks/bench.py --help` for the suites. `--save` writes the run
as structured JSON into `benchmarks/results/`; the schema is documented in
`benchmarks/README.md`.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import functools
import json
import os
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# The checkout's sources win over an installed copy: a run measures the tree it
# is started from.
sys.path.insert(0, str(REPO_ROOT / "src"))

from pathlib_next.http import HTTPTransport  # noqa: E402
from repopath import BaseRepoBackend, RepoBackend  # noqa: E402

SCHEMA = "repopath.bench/1"
MAX_SAMPLES = 100
PURE_SAMPLES = 9
PURE_CALLS = 1000
REQUEST_SAMPLES = 5

#: The groups of the saved metrics: the path classes, and the same operations
#: through the URI classes of `repopath.uri`. A group maps to `(kind, is_uri)`;
#: the `repo` kind is `RepoPath(instance="github.com")` and `repo:`.
GROUPS = {
    "github": ("github", False),
    "github-uri": ("github", True),
    "gitlab": ("gitlab", False),
    "gitlab-uri": ("gitlab", True),
    "repo": ("repo", False),
    "repo-uri": ("repo", True),
}
#: The groups that send requests: `repo` is dispatch to the other two.
REQUEST_GROUPS = ("github", "github-uri", "gitlab", "gitlab-uri")
HOSTS = {"github": "github.com", "gitlab": "gitlab.com", "repo": "github.com"}

# The repository the fakes of tests/api_fakes.py serve.
OWNER = "acme"
REPO = "widgets"

# The generated tree: `listed/` holds LISTED files, `tree/` holds BRANCHES
# directories of LEAVES directories of PER_LEAF files, and `one.txt` is 1 KiB.
LISTED = 100
BRANCHES = 4
LEAVES = 3
PER_LEAF = 8
DIRECTORIES = 3 + BRANCHES + BRANCHES * LEAVES
FILES = 1 + LISTED + BRANCHES * LEAVES * PER_LEAF

#: The names `read` and `walk` operate on, in the generated tree.
FILE = "one.txt"
LISTING = "listed"
#: The reads that share one backend when the default branch lookups are counted.
LOOKUP_READS = (
    ("read_bytes", FILE),
    ("stat", FILE),
    ("read_bytes", "listed/f000.txt"),
    ("iterdir", LISTING),
    ("stat", "tree/b0/l0/f0.txt"),
)


class _Recorder:
    """Collects every measured metric of one run for `--save`.

    A timing metric stores min/median/max milliseconds per call over its
    samples; each sample times `calls_per_sample` calls of the operation.
    """

    def __init__(self):
        self.metrics = {}
        self.counters = {}
        self.errors = {}
        self.skipped = {}

    def timing(self, name, samples, *, group, calls=1):
        per_call_ms = sorted(sample / calls * 1000.0 for sample in samples)
        self.metrics[name] = {
            "group": group,
            "min_ms": round(per_call_ms[0], 6),
            "median_ms": round(statistics.median(per_call_ms), 6),
            "max_ms": round(per_call_ms[-1], 6),
            "samples": len(per_call_ms),
            "calls_per_sample": calls,
        }

    def counter(self, name, values, *, group):
        self.counters[name] = {"group": group, **values}

    def error(self, name, message, *, group):
        self.errors[name] = {"group": group, "error": message}

    def skip(self, name, reason, *, group):
        self.skipped[name] = {"group": group, "reason": reason}


RECORDER = _Recorder()
_SAMPLES_OVERRIDE = None


def _reps(default):
    """Sample count for a case: `--samples` when given, else the case default."""
    return _SAMPLES_OVERRIDE or default


class _Prepared:
    """An operation that needs untimed preparation ahead of every call.

    `_measure` calls `before()` ahead of the warm-up call and of every sample.
    """

    def __init__(self, operate, prepare):
        self._operate = operate
        self.before = prepare

    def __call__(self):
        self._operate()


def _measure(operation, *, repeat, name, group, calls=1):
    """Time `repeat` samples of `calls` calls of `operation` after one warm-up
    sample, and record them. An operation with a `before` attribute has it
    called, untimed, ahead of the warm-up and of every sample. An exception is
    recorded as an error. Returns the median seconds per call, or None."""
    before = getattr(operation, "before", None)

    def timed():
        if before is not None:
            before()
        start = time.perf_counter()
        for _ in range(calls):
            operation()
        return time.perf_counter() - start

    try:
        timed()
        samples = [timed() for _ in range(repeat)]
    except Exception as error:
        RECORDER.error(name, f"{type(error).__name__}: {error}", group=group)
        return None
    RECORDER.timing(name, samples, group=group, calls=calls)
    return statistics.median(samples) / calls


def _count(name, group, compute):
    """Record the counter `compute()` returns; an exception is an error."""
    try:
        values = compute()
    except Exception as error:
        RECORDER.error(name, f"{type(error).__name__}: {error}", group=group)
        return
    RECORDER.counter(name, values, group=group)


# --- backends ----------------------------------------------------------------


class _RefusingBackend(BaseRepoBackend):
    """A backend that fails on use: an operation that must be pure runs on it."""

    __slots__ = ("api_base", "cache")

    def __init__(self):
        self.api_base = "https://api.example.test"
        self.cache = {}

    def request(self, method, url, headers=None, params=None, **kwargs):
        raise AssertionError(f"a request was made: {method} {url}")


class _CountingBackend(RepoBackend):
    """A `RepoBackend` that records the method and URL of each request."""

    __slots__ = ("calls",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []

    def request(self, method, url, *args, **kwargs):
        self.calls.append((method, url))
        return super().request(method, url, *args, **kwargs)


def _tally(calls, **extra):
    """The requests of `calls` as a counter: the total and each HTTP method."""
    methods = collections.Counter(method for method, _url in calls)
    return {"requests": len(calls), **dict(sorted(methods.items())), **extra}


# --- the paths ---------------------------------------------------------------


def _uri_unavailable():
    """Why the URI classes cannot be used, or None when they can."""
    try:
        import uripath  # noqa: F401
        import repopath.uri  # noqa: F401
    except ImportError as error:
        return f"uripath is not installed ({error})"
    return None


def _groups(wanted):
    """The groups of `wanted` that can run, with the URI groups recorded as
    skipped when `uripath` is missing."""
    reason = _uri_unavailable()
    groups = []
    for group in wanted:
        if GROUPS[group][1] and reason is not None:
            RECORDER.skip(group, reason, group=group)
        else:
            groups.append(group)
    return groups


def _path_class(group):
    kind, is_uri = GROUPS[group]
    if is_uri:
        from repopath import uri

        return {
            "github": uri.GitHubURIPath,
            "gitlab": uri.GitLabURIPath,
            "repo": uri.RepoURIPath,
        }[kind]
    import repopath

    return {
        "github": repopath.GitHubPath,
        "gitlab": repopath.GitLabPath,
        "repo": repopath.RepoPath,
    }[kind]


def _uri_text(group, name, ref=None):
    kind = GROUPS[group][0]
    text = f"{kind}://{HOSTS[kind]}/{OWNER}/{REPO}"
    if name:
        text += f"/{name}"
    return f"{text}?ref={ref}" if ref else text


def _make_path(group, name, backend, ref=None):
    """The path `name` (below the root of the repository) in `group`."""
    kind, is_uri = GROUPS[group]
    cls = _path_class(group)
    if is_uri:
        return cls(_uri_text(group, name, ref), backend=backend)
    names = [part for part in name.split("/") if part]
    extra = {"instance": HOSTS[kind]} if kind == "repo" else {}
    return cls(*names, owner=OWNER, repo=REPO, ref=ref, backend=backend, **extra)


# --- the fakes ---------------------------------------------------------------


def _populate(root):
    """Write the generated tree under `root`."""
    (root / FILE).write_bytes(b"x" * 1024)
    listing = root / LISTING
    listing.mkdir()
    for number in range(LISTED):
        (listing / f"f{number:03}.txt").write_bytes(b"x")
    for branch in range(BRANCHES):
        for leaf in range(LEAVES):
            directory = root / "tree" / f"b{branch}" / f"l{leaf}"
            directory.mkdir(parents=True)
            for number in range(PER_LEAF):
                (directory / f"f{number}.txt").write_bytes(b"x")


class _Fakes:
    """The API roots of the loopback fakes and the transport that reaches them."""

    def __init__(self, roots, transport):
        self.roots = roots
        self.transport = transport

    def backend(self, kind, cls=RepoBackend):
        return cls(api_base=self.roots[kind], transport=self.transport)


@contextlib.contextmanager
def _fakes():
    """The fakes of the GitHub and GitLab APIs, on loopback, serving the
    generated tree. The tests directory holds the fakes: they are a helper
    module there, not a test."""
    tests = str(REPO_ROOT / "tests")
    if tests not in sys.path:
        sys.path.append(tests)
    import api_fakes

    with contextlib.ExitStack() as stack:
        root = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
        _populate(root)
        github = stack.enter_context(api_fakes.github_api(root))
        gitlab = stack.enter_context(api_fakes.gitlab_api(root))
        if (github[1], github[2], gitlab[1], gitlab[2]) != (OWNER, REPO, OWNER, REPO):
            raise RuntimeError("the fakes serve another repository than expected")
        transport = HTTPTransport()
        stack.callback(transport.close)
        yield _Fakes({"github": github[0], "gitlab": f"{gitlab[0]}/api/v4"}, transport)


# --- the suites --------------------------------------------------------------


def _pure_cases(group, backend):
    """`(label, operation)` pairs of the operations that send no request."""
    name = "src/pkg/mod.py"
    make = functools.partial(_make_path, group, backend=backend, ref="main")
    path = make(name)
    same = make(name)

    def parsed():
        made = make(name)
        return made.repo_owner, made.repo, made.repo_path, made.ref

    yield "construct", lambda: make(name)
    yield "construct and read repo_owner, repo, repo_path, ref", parsed
    yield "join with /", lambda: path / "other.py"
    yield "parent", lambda: path.parent
    yield "str()", lambda: str(path)
    yield "as_uri()", lambda: path.as_uri()
    yield "== of equal paths", lambda: path == same
    yield "hash()", lambda: hash(path)
    if GROUPS[group][1]:
        from uripath import URIPath

        text = _uri_text(group, name, "main")
        yield "URIPath(text), by scheme", lambda: URIPath(text, backend=backend)


def benchmark_pure():
    """Pure-path operations on a backend that refuses every request."""
    results = {}
    backend = _RefusingBackend()
    for group in _groups(GROUPS):
        for label, operation in _pure_cases(group, backend):
            results[(group, label)] = _measure(
                operation,
                repeat=_reps(PURE_SAMPLES),
                calls=PURE_CALLS,
                name=f"{group}: {label}",
                group=group,
            )
    return results


def _read_cases(make):
    """`(label, operation)` pairs of the single-path reads of the fake."""
    yield "stat() of a file", lambda: make(FILE).stat()
    yield "stat() of a directory", lambda: make(LISTING).stat()
    yield f"iterdir() of {LISTED} entries", lambda: list(make(LISTING).iterdir())
    yield "read_bytes() of a 1 KiB file", lambda: make(FILE).read_bytes()


def _root_listing_url(kind, api):
    if kind == "github":
        return f"{api}/repos/{OWNER}/{REPO}/contents"
    return f"{api}/projects/{OWNER}%2F{REPO}/repository/tree"


def benchmark_read():
    """`stat()`, `iterdir()` and `read_bytes()` against the loopback fakes, and
    the requests each sends."""
    results = {}
    with _fakes() as fakes:
        for group in _groups(REQUEST_GROUPS):
            kind, is_uri = GROUPS[group]
            backend = fakes.backend(kind)
            make = functools.partial(_make_path, group, backend=backend)
            for label, operation in _read_cases(make):
                results[(group, label)] = _measure(
                    operation,
                    repeat=_reps(REQUEST_SAMPLES),
                    name=f"{group}: {label}",
                    group=group,
                )
            if not is_uri:
                # The cost of one request through the backend, with no path:
                # the share of the numbers above that is the fake and the transport.
                url = _root_listing_url(kind, fakes.roots[kind])
                label = "backend.request() of the root listing, no path"
                results[(group, label)] = _measure(
                    lambda: backend.request("GET", url),
                    repeat=_reps(REQUEST_SAMPLES),
                    name=f"{group}: {label}",
                    group=group,
                )
            _count_reads(group, fakes)
    return results


def _count_reads(group, fakes):
    """Record the requests of each single-path read, with the default branch
    already known; GitLab's reads are counted again without it."""
    kind = GROUPS[group][0]
    counting = fakes.backend(kind, _CountingBackend)
    make = functools.partial(_make_path, group, backend=counting)
    for label, operation in _read_cases(make):

        def requests(operation=operation):
            operation()
            counting.calls.clear()
            operation()
            return _tally(counting.calls)

        _count(f"{group}: requests of {label}", group, requests)
        if kind == "gitlab":

            def cold(operation=operation):
                counting.cache.clear()
                counting.calls.clear()
                operation()
                return _tally(counting.calls)

            _count(
                f"{group}: requests of {label}, default branch not known",
                group,
                cold,
            )


def _walked(path):
    """Walk `path` to the end; returns its directory and file counts."""
    directories = files = 0
    for _top, _dirnames, filenames in path.walk():
        directories += 1
        files += len(filenames)
    return directories, files


def _lookups(calls):
    """The GitLab default-branch lookups among `calls`."""
    ending = f"/projects/{OWNER}%2F{REPO}"
    return sum(1 for _method, url in calls if url.endswith(ending))


def _do_read(make, how, name):
    path = make(name)
    if how == "iterdir":
        return list(path.iterdir())
    return getattr(path, how)()


def benchmark_tree():
    """`walk()` of the whole generated tree, and GitLab's default-branch lookup
    on a backend shared between paths."""
    results = {}
    label = f"walk() of the tree ({DIRECTORIES} directories, {FILES} files)"
    with _fakes() as fakes:
        for group in _groups(REQUEST_GROUPS):
            kind = GROUPS[group][0]
            backend = fakes.backend(kind)
            make = functools.partial(_make_path, group, backend=backend)
            results[(group, label)] = _measure(
                lambda: _walked(make("")),
                repeat=_reps(REQUEST_SAMPLES),
                name=f"{group}: {label}",
                group=group,
            )
            if kind == "gitlab":
                # An empty cache sends the default-branch lookup ahead of the stat.
                cold = "stat() of a file, default branch not cached"
                results[(group, cold)] = _measure(
                    _Prepared(lambda: make(FILE).stat(), backend.cache.clear),
                    repeat=_reps(REQUEST_SAMPLES),
                    name=f"{group}: {cold}",
                    group=group,
                )
            _count_tree(group, fakes)
    return results


def _count_tree(group, fakes):
    kind = GROUPS[group][0]

    def walk():
        counting = fakes.backend(kind, _CountingBackend)
        found = _walked(_make_path(group, "", counting))
        if found != (DIRECTORIES, FILES):
            raise RuntimeError(f"the walk found {found}, not {(DIRECTORIES, FILES)}")
        return _tally(counting.calls, directories=found[0], files=found[1])

    _count(f"{group}: requests of walk() of the tree", group, walk)
    if kind != "gitlab":
        return

    def lookups(shared):
        def run():
            calls = []
            counting = fakes.backend(kind, _CountingBackend)
            for how, name in LOOKUP_READS:
                if not shared:
                    counting = fakes.backend(kind, _CountingBackend)
                _do_read(
                    functools.partial(_make_path, group, backend=counting), how, name
                )
                calls.extend(counting.calls)
                counting.calls.clear()
            return _tally(calls, reads=len(LOOKUP_READS), lookups=_lookups(calls))

        return run

    reads = len(LOOKUP_READS)
    _count(
        f"{group}: default-branch lookups over {reads} reads, one shared backend",
        group,
        lookups(True),
    )
    _count(
        f"{group}: default-branch lookups over {reads} reads, a backend per read",
        group,
        lookups(False),
    )


SUITES = {
    "pure": benchmark_pure,
    "read": benchmark_read,
    "tree": benchmark_tree,
}


def _format(value):
    if value is None:
        return "n/a"
    ms = value * 1000
    return f"{ms * 1000:.2f} us" if ms < 0.1 else f"{ms:.3f} ms"


def print_results(results, *, markdown=False):
    groups = [group for group in GROUPS if any(g == group for g, _label in results)]
    labels = []
    for _group, label in results:
        if label not in labels:
            labels.append(label)
    if markdown:
        print("| Operation | " + " | ".join(groups) + " |")
        print("| --- | " + " | ".join("---" for _ in groups) + " |")
    for label in labels:
        cells = [_format(results.get((group, label))) for group in groups]
        if markdown:
            print(f"| {label} | " + " | ".join(cells) + " |")
        else:
            print(f"{label}: " + ", ".join(f"{g} {c}" for g, c in zip(groups, cells)))


def print_counters():
    for name, values in sorted(RECORDER.counters.items()):
        shown = {key: value for key, value in values.items() if key != "group"}
        print(f"{name}: {shown}")


def main(suite="default"):
    print("Running benchmarks...")
    all_results = []
    for name in SUITES if suite == "default" else [suite]:
        print(f"\n== {name}")
        results = SUITES[name]()
        print_results(results)
        all_results.append(results)
    print()
    print_counters()
    # Markdown table format, for copy-pasting.
    for results in all_results:
        print()
        print_results(results, markdown=True)


# --- the saved result --------------------------------------------------------


def _pyproject_version():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _git_state():
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": dirty}


def _interpreter_tag():
    """`<major.minor>-<os>-<arch>`, with the arch the interpreter was built for."""
    arch = sysconfig.get_platform().rsplit("-", 1)[-1]
    os_name = "darwin" if sys.platform == "darwin" else os.name
    return f"{sys.version_info[0]}.{sys.version_info[1]}-{os_name}-{arch}"


def build_result(suite, *, name, note=None):
    """The JSON document `--save` writes (schema: benchmarks/README.md)."""
    return {
        "schema": SCHEMA,
        "name": name,
        "suite": suite,
        "source": "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
        "note": note,
        "created_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "package": {"name": "repopath", "version": _pyproject_version()},
        "git": _git_state(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "interpreter": _interpreter_tag(),
        "processor": f"{platform.system()}-{platform.machine()}",
        "cpu_count": os.cpu_count(),
        "metrics": dict(sorted(RECORDER.metrics.items())),
        "counters": dict(sorted(RECORDER.counters.items())),
        "errors": dict(sorted(RECORDER.errors.items())),
        "skipped": dict(sorted(RECORDER.skipped.items())),
    }


def default_result_name(suite):
    name = f"{_pyproject_version()}-py{_interpreter_tag()}"
    return name if suite == "default" else f"{name}-{suite}"


def save_result(suite, *, name=None, results_dir=None, note=None):
    name = name or default_result_name(suite)
    result = build_result(suite, name=name, note=note)
    directory = (
        pathlib.Path(results_dir)
        if results_dir
        else REPO_ROOT / "benchmarks" / "results"
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    with open(target, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(f"\nSaved {len(result['metrics'])} metrics to {target}")
    return target


def _samples_arg(value):
    count = int(value)
    if not 1 <= count <= MAX_SAMPLES:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_SAMPLES}")
    return count


def cli(argv=None):
    global _SAMPLES_OVERRIDE

    parser = argparse.ArgumentParser(
        description="Run repopath benchmarks: GitHubPath, GitLabPath and RepoPath, "
        "and their URI classes when uripath is installed, against the loopback "
        "fakes of the GitHub and GitLab APIs in tests/api_fakes.py.",
    )
    parser.add_argument(
        "--suite",
        choices=["default", *SUITES],
        default="default",
        help="pure: path operations that send no request; read: stat(), "
        "iterdir() and read_bytes(); tree: walk() and the default-branch "
        "lookup; default: all three",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="write the run as JSON to benchmarks/results/<name>.json",
    )
    parser.add_argument(
        "--name",
        help="result name for --save, without .json "
        "(default: <version>-py<major.minor>-<os>-<arch>, with -<suite> "
        "appended for a suite other than default)",
    )
    parser.add_argument(
        "--results-dir",
        help="directory for --save (default: the benchmarks/results directory beside this script)",
    )
    parser.add_argument(
        "--samples",
        type=_samples_arg,
        help=f"samples per metric for every case, 1-{MAX_SAMPLES} "
        f"(default: each case's own count, {PURE_SAMPLES} for pure-path "
        f"operations and {REQUEST_SAMPLES} for requests)",
    )
    parser.add_argument("--note", help="free-text note stored in the saved JSON")
    args = parser.parse_args(argv)
    _SAMPLES_OVERRIDE = args.samples

    main(args.suite)
    if args.save:
        save_result(
            args.suite, name=args.name, results_dir=args.results_dir, note=args.note
        )


if __name__ == "__main__":
    cli()
