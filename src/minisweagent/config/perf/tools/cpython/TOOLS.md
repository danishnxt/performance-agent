These commands are on your PATH. Run them from the repository root.

Rebuild after a C change: `make -C build -j`. Changes to `Lib/*.py` need no rebuild: `build/python.exe` reads `Lib/` from the source tree.

Keep a "before" build to compare against. Before you change any code, clone the whole tree; the clone's interpreter uses the clone's own `Lib/` and C code, so it stays as it was:

```
cp -cR . $TMPDIR/before                  # copy-on-write clone of the tree and its build, under a second
```

### `perf-profile`: Python-level profile of one workload

```
perf-profile w05                         # a workload from benchmarks/workloads/
perf-profile script.py                   # any script
```

It profiles with cProfile inside `build/python.exe` and prints the functions by own time and by cumulative time, with call counts and file:line. Functions written in C show as `{built-in method ...}` or `{method ... of ... objects}`. cProfile slows every Python call, so use `benchmarks/run.py` or `perf-compare` for timings. `--python PATH` profiles with another interpreter.

### `perf-sample`: CPU profile of the interpreter's C code

```
perf-sample w05                          # a workload, run over and over while sampling
perf-sample -- build/python.exe x.py     # any command
perf-sample --pid PID                    # a process that is already running
perf-sample --compare w05 w16            # two workloads: which C functions differ most in share
```

It runs the workload once to warm up, then samples the CPU for `--seconds` (default 5) while it runs, and prints the C functions that take the time, ranked by self time (the function's own code) and by inclusive time (including what it calls). Use it when the slow part is in C (a built-in function or method, or a module written in C). Time spent running Python code shows up as the interpreter loop (`_PyEval_EvalFrameDefault`), not as Python function names: use `perf-profile` for those.

`--compare A B` profiles two workloads and lists the functions whose share of CPU time differs most between them. Pick a slow workload and a similar one that is not slow: what the slow one spends more of its time in is where to look.

### `perf-compare`: is build B faster or slower than build A?

```
perf-compare $TMPDIR/before/build/python.exe build/python.exe            # every workload
perf-compare $TMPDIR/before/build/python.exe build/python.exe w05 w13    # only these
```

It runs the two interpreters alternately over the workloads and prints, per workload, the median time of each and a verdict: faster, SLOWER, or "no change beyond noise" (5% per workload, 2% for the suite total). Use it to decide whether to keep a change. All workloads take about a minute; name a few to go faster.
