# Benchmarks

`bench.py` times `GitHubPath`, `GitLabPath` and `RepoPath` against the loopback fakes of
the GitHub and GitLab APIs in [`tests/api_fakes.py`](../tests/api_fakes.py), which serve a
generated tree of 197 files in 19 directories. The same operations run again through the
URI classes of `repopath.uri` (the `github:`, `gitlab:` and `repo:` schemes of `uripath`)
when `uripath` is installed. Method notes are on the docs site's Benchmarks page
([`docs/benchmarks.md`](../docs/benchmarks.md)).

Nothing here contacts an external host: the fakes listen on `127.0.0.1` and every path is
given a backend whose API root is one of them.

## Reproduce

From the repository root, in a virtualenv with `uripath` (the `dev` and `uri` extras):

```bash
python -m pip install -e ".[dev,uri]"
python benchmarks/bench.py --save
```

That runs the suites and writes
`benchmarks/results/<version>-py<major.minor>-<os>-<arch>.json`:

```bash
python benchmarks/bench.py --help
python benchmarks/bench.py --suite pure
python benchmarks/bench.py --save --samples 9
python benchmarks/bench.py --save --name my-run
```

| Option | Effect |
| --- | --- |
| `--suite NAME` | `pure`, `read`, `tree`, or `default` for all three. A suite other than `default` appends `-<suite>` to the default file name. |
| `--save` | Write the run to `benchmarks/results/<name>.json`. |
| `--name NAME` | File name without `.json`. Default `<version>-py<major.minor>-<os>-<arch>`. |
| `--results-dir DIR` | Write somewhere other than `benchmarks/results/`. |
| `--samples N` | Samples per metric for every case (1-100). Default: 9 for `pure`, 5 for `read` and `tree`. |
| `--note TEXT` | Free text stored in the JSON. |

Without `uripath` the `github-uri`, `gitlab-uri` and `repo-uri` groups are skipped and the
reason is recorded in `skipped`.

## Suites

A metric is named `<group>: <operation>`. The groups are `github`, `gitlab` and `repo`
(`RepoPath(instance="github.com")`), and `github-uri`, `gitlab-uri` and `repo-uri` for the
URI classes. The `repo` groups only run `pure`: `RepoPath` hands every read to the other
two kinds.

| Suite | Operations | Groups |
| --- | --- | --- |
| `pure` | construct; construct and read `owner`, `repo`, `repo_path`, `ref`; join with `/`; `parent`; `str()`; `as_uri()`; `==` of equal paths; `hash()`; `URIPath(text)` by scheme (URI groups) | all six |
| `read` | `stat()` of a file and of a directory; `iterdir()` of 100 entries; `read_bytes()` of a 1 KiB file; one `backend.request()` of the root listing, with no path | `github`, `gitlab`, and the two URI groups (the request alone: path groups only) |
| `tree` | `walk()` of the whole tree; `stat()` of a file on a GitLab backend whose default branch is not cached | `github`, `gitlab`, and the two URI groups (the cached stat: GitLab groups only) |

The `pure` operations run on a backend that raises if a request is made, so a request
sneaking into one would show as an error. Each sample times 1,000 calls of a `pure`
operation, and one call of a request.

The `counters` record how many requests an operation sends, by HTTP method, measured
with a `RepoBackend` subclass that counts them: each `read` operation (GitLab's again with
the default branch not known), `walk()` of the tree (one request per directory, not per
file), and the number of default-branch lookups over five reads when the paths share one
backend and when each read has its own.

## Result schema

`schema` is `repopath.bench/1`. A result holds `name`, `suite`, `source` (`ci` when run by
GitHub Actions, else `local`), `note`, `created_utc`, `package` (`name`, `version`), `git`
(`commit`, `dirty`), `python`, `interpreter` (`<major.minor>-<os>-<arch>`), `processor`,
`cpu_count`, and four maps keyed by the metric name:

| Key | Holds |
| --- | --- |
| `metrics` | per metric: `group`, `min_ms`, `median_ms` and `max_ms` per call, `samples`, `calls_per_sample` |
| `counters` | per counter: `group`, `requests` (the total), the number of requests of each HTTP method, and any quantity the counter names (`directories`, `files`, `reads`, `lookups`) |
| `errors` | per metric that raised: `group` and the error |
| `skipped` | per group not run: `group` and the reason |

Compare two runs on their medians, and only between runs from the same machine and
interpreter. A run on a developer machine is a sanity check, not a performance claim:
performance claims in the changelog or release notes come from CI runs (`test.yml` with
`benchmark` set).

## Reading the numbers

The fakes read the whole generated tree from disk on every request, so a request costs
time that grows with the tree (10 to 20 milliseconds in the committed baselines), and the
time of `read` and `tree` operations is mostly requests. `backend.request() of the root listing, no path` is
one such request with no path around it: subtract it to see what the path layer adds.
The counters, not the timings, show what a change in the number of requests does. The
`pure` timings include the call of a closure and a loop, which matters only for the
sub-microsecond operations.

## Committed results

`results/baseline-local-*.json` are local sanity baselines of the suites on one developer
machine, for 3.14 and 3.9.
