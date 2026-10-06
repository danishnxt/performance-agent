This machine runs Linux.

- **CPU profile:** `perf`. If a `perf-sample` tool is listed under Tools, use it: it profiles only the query, not loading. Plain `perf record -g -p PID -- sleep SECONDS` records everything the process does in that window; check the profile shows the work you meant to measure. If `perf` is refused, `/proc/sys/kernel/perf_event_paranoid` is too strict.
- **Hardware counters:** `perf stat -e cycles,instructions,cache-misses,branch-misses COMMAND`.
- **CPU time and peak memory of one command:** `/usr/bin/time -v COMMAND`.
