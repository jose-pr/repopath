# Benchmarks

`repopath` includes a benchmark harness at `benchmarks/bench.py` that times `GitHubPath`,
`GitLabPath` and `RepoPath`, and the `github:`, `gitlab:` and `repo:` schemes of `uripath`
on the same operations. It needs only the `uri` extra for the URI classes:
`pip install -e ".[dev,uri]"`.

## Run It

```bash
python benchmarks/bench.py            # run the benchmarks
python benchmarks/bench.py --help     # options; runs nothing
python benchmarks/bench.py --suite pure
```

## Saved Results

Every metric is timed over several samples. `--save` writes the run as JSON to
`benchmarks/results/<name>.json`, with the minimum, median and maximum milliseconds per
call of each metric plus the interpreter, platform and git commit it ran on:

```bash
python benchmarks/bench.py --save
python benchmarks/bench.py --save --samples 9
```

`--samples N` applies one sample count to every case; without it the pure-path operations
take nine and the ones that send requests take five. The result schema and the committed
results are described in
[`benchmarks/README.md`](https://github.com/jose-pr/repopath/blob/main/benchmarks/README.md).

Compare two runs on their medians, and only between runs from the same machine and
interpreter. A run on a developer machine is a sanity check; performance claims in release
notes come from CI runs.

## What It Covers

- the pure-path operations (construct, join with `/`, `parent`, `str()`, `as_uri()`, `==`
  and `hash()`, and the parse of a URI) on a backend that refuses every request
- `stat()` of a file and of a directory, `iterdir()` of 100 entries and `read_bytes()` of a
  small file
- `walk()` of a tree of 197 files in 19 directories, and the number of requests it sends:
  one per directory, none per file
- GitLab's default-branch lookup: a `stat()` that has to look it up, and the number of
  lookups over several reads when the paths share one backend and when they do not
- each of the above through the path classes (metric groups `github`, `gitlab`, `repo`) and
  through the URI classes (`github-uri`, `gitlab-uri`, `repo-uri`), so the cost of the URI
  layer is the difference between the two groups

## Caveats

The API is a loopback fake in the benchmark's own process, with no network latency, so the
numbers show the overhead of the path layer and of the fake, not how a hosting service
behaves over a link. The fake reads its whole tree from disk on every request, so a
request costs the same few milliseconds whatever the path layer does: the timings of the
operations that send requests are mostly that, and the request counts show the difference a
change makes. Timings depend on the machine, the interpreter and what else is running;
compare medians, as described above.
