These commands are on your PATH.

Rebuild after a code change: `make -C build/reldebug -j shell benchmark_runner` (the CLI target is `shell`, not `duckdb`).

### `perf-profile`: per-operator profile of one DuckDB query

```
perf-profile benchmark/tpch/sf10/q05.benchmark     # a benchmark file: uses that benchmark's query and database
perf-profile query.sql --db file.duckdb            # a SQL file
perf-profile -c "SELECT ..." --db file.duckdb      # inline SQL
```

It runs the query once with DuckDB's JSON profiling and prints one row per operator: share of time, CPU seconds, actual rows, estimated rows, and the estimate/actual ratio. Joins are listed again with probe-side and build-side row counts. It flags a hash join whose build side is larger than its probe side. On operators with at least 5% of the time, it also lists row estimates off by 10x or more, as hints: a bad estimate matters only if it led to a slow choice. It uses `build/release/duckdb` or `build/reldebug/duckdb`, whichever exists; rebuild first after a code change. `--all` shows every operator, `--json PATH` keeps the raw profile.

It measures one run. Use the benchmark runner for timings you compare.

### `perf-sample`: CPU profile of one DuckDB query

```
perf-sample benchmark/tpch/sf10/q19.benchmark     # a benchmark file: uses that benchmark's query and database
perf-sample query.sql --db file.duckdb            # a SQL file
perf-sample -c "SELECT ..." --db file.duckdb      # inline SQL
perf-sample --pid PID                             # a process that is already running
perf-sample --compare SLOW.benchmark FAST.benchmark   # two queries: which functions differ most in share
```

It runs the query once to warm up, then runs it repeatedly while sampling the CPU for `--seconds` (default 5), so loading the database is not in the profile. It prints the functions that take the CPU time, ranked by self time (the function's own code) and by inclusive time (including what it calls), with their source files. Idle threads are dropped. Use it when `perf-profile` shows where the time goes by operator but not why that operator is slow. The query runs slower while sampled; use the benchmark runner for timings. `--top N` shows more rows, `--raw PATH` keeps the profiler's output.

`--compare A B` profiles two queries and lists the functions whose share of CPU time differs most between them. Pick a slow query and a similar one that is not slow (for example, both scanning the same table): what the slow one spends more of its time in is where to look.

### `perf-compare`: is build B faster or slower than build A?

```
cp build/reldebug/benchmark/benchmark_runner $TMPDIR/runner_before     # before you change the code
# ... change the code, rebuild ...
perf-compare $TMPDIR/runner_before build/reldebug/benchmark/benchmark_runner              # every TPC-H SF10 query
perf-compare $TMPDIR/runner_before build/reldebug/benchmark/benchmark_runner "benchmark/tpch/sf10/q0[69].benchmark"
```

It runs the two benchmark runners alternately and prints, per query, the median time of each and a verdict: faster, SLOWER, or "no change beyond noise" (8% per query, 3% for the suite total). Use it to decide whether to keep a change. The full suite takes about 2 minutes; pass a pattern to compare only some queries.
