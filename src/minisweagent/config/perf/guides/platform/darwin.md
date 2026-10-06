This machine runs macOS.

- **CPU profile:** macOS `sample` (no sudo). If a `perf-sample` tool is listed under Tools, use it: it profiles only the query, not loading. Plain `sample PID SECONDS -file OUT` records everything the process does in that window, including startup, loading data and writing files; check the profile shows the work you meant to measure.
- **Not available:** `perf`, `valgrind`, `gprof`. `dtrace` and Instruments need root.
- **Process list:** `ps` is blocked; use `pgrep -fl NAME`.
- **CPU time and peak memory of one command:** `/usr/bin/time -l COMMAND`.
