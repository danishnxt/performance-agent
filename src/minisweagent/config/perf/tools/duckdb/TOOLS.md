These commands are on your PATH.

### `perf-profile`: per-operator profile of one DuckDB query

```
perf-profile benchmark/tpch/sf10/q05.benchmark     # a benchmark file: uses that benchmark's query and database
perf-profile query.sql --db file.duckdb            # a SQL file
perf-profile -c "SELECT ..." --db file.duckdb      # inline SQL
```

It runs the query once with DuckDB's JSON profiling and prints one row per operator: share of time, CPU seconds, actual rows, estimated rows, and the estimate/actual ratio. Joins are listed again with probe-side and build-side row counts. It flags a hash join whose build side is larger than its probe side, and estimates off by 10x or more. It uses `build/release/duckdb` or `build/reldebug/duckdb`, whichever exists; rebuild first after a code change. `--all` shows every operator, `--json PATH` keeps the raw profile.

It measures one run. Use the benchmark runner for timings you compare.
