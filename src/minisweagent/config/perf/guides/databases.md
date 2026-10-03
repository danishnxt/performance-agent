# Database engine performance: what to check

What an analytical (OLAP) database engine should be doing, how slowdowns show up, and where in the source each kind of decision lives. Applies to columnar, vectorized engines in the style of DuckDB, ClickHouse or Velox. This is domain knowledge only; how to run an investigation is covered elsewhere.

## 1. Database-specific measurement traps

- Check the build type: benchmark a release (optimized) build. Debug builds and builds with assertions on are much slower and distort the profile.
- Keep thread count, memory limit and data identical between runs. A lower memory limit can cause spilling.
- A cold page cache vs a warm one changes scan times a lot. Drop the first run.
- Plans and per-operator row counts are deterministic for the same data and settings. If they differ between two runs of the same build, something other than code changed.

## 2. Reading a profiled plan

For each operator, look at: **time** (own time, not including children), **actual output rows**, **estimated rows**, and (if shown) memory, spilled bytes and thread count.

- **Find the hot operator.** Sort by own time. Usually one or two operators explain the whole change.
- **Compare estimated vs actual rows.** An error of 10x or more is a cardinality problem (Section 3). Errors compound up the plan.
- **Check the plan's shape.** Is the join order sensible (small results first)? Is each hash join building on its smaller input? Are filters as low in the plan as they can go? Is there a nested-loop join or cross product where an equality join is possible? Is there a full ORDER BY where a LIMIT allows Top-N? If you have an older plan to compare against, check what changed.
- **Look at rows flowing between operators.** A scan that outputs far more rows than the filters should let through → a filter or pushdown was lost. A join that outputs far more rows than either input → a bad join order, or a missing join condition.
- **Sensible plan and row counts, but one operator slow for its input size** → an execution change in that operator: hash table, expression, memory, string handling.
- Check thread counts or per-thread times. One thread doing all the work means parallelism was lost.

## 3. Cardinality estimation and statistics

Almost every plan choice depends on the estimated row counts. A bad estimate gives a bad join order, the wrong build side and wrong memory sizing.

- **Symptoms:** estimates off by orders of magnitude, often starting at a filter or a join. An estimate of exactly 1 or 0 is a classic sign of a missing or default statistic.
- **Usual causes:** statistics not collected, not loaded, or not passed through an operator; a wrong default selectivity for some predicate type (LIKE, IN, OR, functions); assuming independence between correlated columns; a wrong distinct-count (NDV) estimate for join keys; a new scan source (a file format, an external table) that reports no statistics.
- **Where it lives:** statistics propagation (per-operator "propagate statistics" or "estimate cardinality" code), the cardinality estimator / join-cost model, table and column statistics (min/max, NDV, null count, HyperLogLog sketches), and the per-table-function or per-scan hook that reports row counts.

## 4. Joins

**Join order.** Joins should first combine tables that shrink the result. A bad order produces huge intermediate results.

- **Symptom:** a join outputs far more rows than its inputs justify; large tables are joined before the filters that shrink them.
- **Where:** the join-order optimizer (dynamic programming or greedy enumeration over a join graph), its cost function (usually just the sum of intermediate cardinalities), and the code that builds the join graph from filters (if a condition is not recognized as a join edge, you get a cross product).

**Build side.** In a hash join, the hash table should be built on the **smaller** input; the larger input probes it.

- **Symptom:** the build side has many more rows than the probe side; big memory use or spilling in the join.
- **Where:** the build/probe side selection pass (compares estimated sizes and swaps children), and the physical planner.

**Join algorithm.**

| Seen in plan | Usually means |
|---|---|
| Nested-loop join or cross product + filter on an equality condition | Equality condition not detected (type cast on a key, an expression on the key, OR conditions) |
| Hash join → sort-merge join (or the reverse) | A threshold or cost rule changed |
| Broadcast vs partitioned join flipped (distributed engines) | Size estimate crossed a threshold |
| Semi/anti join became an inner join + distinct | Subquery decorrelation / rewrite changed |

- **Where:** physical plan generation for joins, the subquery flattening/decorrelation rules, and the code that classifies join conditions (equality vs other).

## 5. Hash tables (joins and aggregation)

Same plan, same rows, but the join or aggregate is slower: look at the hash table.

- **Sizing:** initial capacity from the estimate (too small → many resizes and rehashing; too big → wasted memory and cache misses). Load factor / resize threshold changes.
- **Hash function:** a weaker hash gives many collisions and long probe chains. A slower hash costs on every row. Watch for hashing that now runs per-row instead of per-vector, or that no longer special-cases fixed-size keys.
- **Layout:** linear probing vs chaining; storing a few hash bits ("salt") next to the pointer to skip key comparisons; row layout (keys packed together vs scattered). Losing any of these increases cache misses.
- **Key handling:** fast paths for small integer keys (direct array / perfect hashing, or packing several small keys into one integer) can silently stop being used after a type or range change.
- **Where:** the join hash table / aggregate hash table classes, the row layout / tuple data collection code, the vector hashing functions, and the resize/capacity logic.

## 6. Aggregation (GROUP BY)

- **Symptoms:** aggregate slow with the same row counts; memory use up; spilling.
- **Check:**
  - **Two phases:** each thread pre-aggregates locally, then the partial results are merged. If the local phase stops reducing rows (e.g. it gets disabled or its table is too small), the merge does all the work.
  - **Adaptive switches:** many engines skip local pre-aggregation when few duplicates are seen. A wrong threshold here either wastes work or floods the merge.
  - **Partitioning** of the merge phase (radix partitioning): too few partitions → poor parallelism.
  - **Aggregate functions:** a fast specialized path (e.g. COUNT(*), SUM on integers, ungrouped aggregates, "perfect hash" for small-range keys) replaced by the generic path. DISTINCT aggregates are expensive.
- **Where:** the hash aggregate operator, the partitioned/radix aggregation code, aggregate function implementations and their registration (which specialized version gets chosen).

## 7. Filters and predicate pushdown

Filters should run as early as possible, ideally inside the scan.

- **Symptom:** a scan outputs many more rows than its filters should allow; a FILTER node sits above a join or scan when it could be inside the scan.
- **Common causes:**
  - The pushdown rule stopped matching (a cast was added around a column, a new expression type, a function marked non-deterministic or "may throw").
  - A filter cannot pass through some operator (a projection, a window, an outer join on the wrong side, a union).
  - Filters not combined or simplified (constant folding, IN-list → range, redundant conditions).
- **Filter order:** with several conditions on one row (AND), run the cheapest and most selective one first. A change in that ordering can slow down string or function-heavy filters.
- **Where:** the filter pushdown optimizer rules, expression rewriting/simplification rules, the table-filter / scan-filter conversion, and the expression reordering logic.

## 8. Scans, compression, zone maps, late materialization

- **Zone maps / min-max indexes:** each block of rows stores min/max. Blocks that cannot match are skipped. Check that the filter still reaches the scan in a form the zone-map check understands, and that stats are still written for every column type.
- **Compression:** reading a dictionary-, RLE- or bit-packed column should not decompress more than needed. Check for a fast path that filters directly on compressed data (e.g. comparing dictionary codes) being lost, or a change in which compression gets chosen at write time.
- **Column pruning:** only read the columns the query uses. Symptom: a scan reading many more bytes or columns.
- **Late materialization:** read the filter columns first, then fetch the other columns only for surviving rows. If that is lost, scans of wide tables with selective filters get much slower.
- **Formats like Parquet:** row-group skipping via statistics, page indexes, and prefetching/buffer sizes.
- **Where:** column readers / segment scan code, compression codecs, zone-map / statistics checks in the scan, the scan's projection and filter pushdown handling, the file-format readers.

## 9. Runtime (dynamic) filters

After a hash join builds its table, it knows which key values exist. It can send a filter (min/max range, IN-list, or Bloom filter) to the scan on the probe side, so rows that cannot match are dropped early.

- **Symptom:** the probe-side scan outputs far more rows than before; the join drops most of them.
- **Common causes:** the filter is no longer created (a size or distinct-count threshold changed); it cannot reach the scan (the key is modified by an expression, or an exchange/projection is in the way); it arrives too late (the scan started before the build finished); the Bloom filter is too small (high false positive rate).
- Also related: TOP-N dynamic filters (the current Nth value prunes rows in the scan) and aggregate min/max filters.
- **Where:** hash join build finalization, the dynamic/runtime filter classes, scan code that accepts and applies them.

## 10. Vectorized expression evaluation

Operators process data in batches (vectors) of e.g. 1K–8K values. Most speed comes from tight loops over a vector.

- **Lost fast paths:** constant vectors, dictionary vectors, flat vs encoded vectors, "no nulls" paths, all-valid vs selection-vector paths. If a change makes the code fall into the generic per-row path, it can be 10x slower.
- **Per-row overhead:** a virtual call, allocation, exception setup, or string copy per row inside a loop that used to be per vector.
- **Batch size:** smaller vectors → more per-batch overhead; too large → cache misses.
- **Casts:** an implicit cast added by the binder (e.g. INT → BIGINT, or a decimal precision change) runs on every row and can block other optimizations.
- **Common subexpressions:** an expression now evaluated multiple times.
- **Where:** the expression executor, function templates (unary/binary/ternary executors), vector/selection vector classes, the binder's cast insertion, and the specific scalar function implementations.

## 11. Parallelism and pipeline scheduling

- **Symptoms:** wall time up but CPU time flat or lower; one thread busy and the others idle; a query does not get faster with more threads.
- **Check:**
  - How many tasks the scan creates (too few partitions or row groups per task → idle threads).
  - A pipeline breaker (sort, hash build, aggregate) whose final step now runs single-threaded.
  - Locks or atomics added in a hot path (shared counters, a global hash table, the memory allocator).
  - False sharing: per-thread state placed in the same cache line.
  - Exchange/repartition operators being added or removed.
- **Where:** the task scheduler / executor, pipeline construction, each operator's "source/sink/combine/finalize" methods, and the parallel-state classes.

## 12. Memory and spilling

- **Symptoms:** a big slowdown with disk I/O; "spilled bytes" in the profile; memory peak much higher than before.
- **Check:** the memory limit and how it is split between operators; buffer-manager eviction policy; whether an operator now spills too early (thresholds) or keeps too much; the allocator (fragmentation, a switch away from arena/pooled allocation, frequent small allocations or `memset` of large buffers); temporary objects copied instead of moved.
- **Where:** buffer manager / memory tracker, the operator's spill logic (partitioned hash join, external sort, external aggregation), allocator wrappers.

## 13. Strings

Strings are the most common source of hidden cost.

- **Short-string inline storage:** many engines store strings ≤12 bytes inline with a prefix. Comparisons check the prefix first. Losing this makes every compare chase a pointer.
- **Copies:** a string copied (heap allocation) per row where a reference into the existing buffer used to be enough.
- **Dictionary encoding:** operations on dictionary-encoded strings should run once per distinct value, not once per row.
- **Functions:** LIKE with a simple prefix/suffix/contains pattern should use a fast path, not a regex. Case-insensitive collation and UTF-8 handling add cost.
- **Hashing/compare:** string hashing and equality in joins and GROUP BY; a slower hash function shows up here first.
- **Where:** the string type/struct, string heap/arena, string functions (LIKE, contains, regex), collation code, string hashing.

## 14. Sorting and Top-N

- **Symptom:** `ORDER BY ... LIMIT n` now runs a full sort. Check that the TOP_N rewrite still fires (it often needs a known LIMIT and a recognized ORDER BY above it; a projection or a missing cardinality estimate can block it).
- **Sort cost:** key normalization (encode the sort keys into a comparable byte string to use `memcmp` or radix sort) lost; falling back to a comparator-per-row sort; string keys compared slowly; parallel merge steps reduced.
- **Where:** the TOP_N optimizer rule, the order/sort operator, the key normalization / row encoding, the merge sort / radix sort code.

## 15. Symptom → where to look (quick table)

| Symptom in the profile | First place to look |
|---|---|
| Estimated rows ≫ or ≪ actual | Statistics propagation, cardinality estimator |
| Join order puts big intermediate results first | Join-order optimizer, cost model, join-graph construction |
| Large input on the hash-build side | Build/probe side selection |
| Nested-loop or cross product appeared | Join condition detection, decorrelation, casts on keys |
| Scan outputs more rows | Filter pushdown, zone maps, runtime filters |
| FILTER node moved above a join | Pushdown rules, expression determinism flags |
| Same plan, join/aggregate slower | Hash table: sizing, hashing, layout, key fast paths |
| Same plan, projection/filter slower | Expression executor fast paths, casts, string functions |
| Full sort under a LIMIT | Top-N rewrite rule |
| Wall ↑, CPU flat | Parallelism, locks, I/O, spilling |
| Spilled bytes on a query that fits in memory | Memory limits, spill thresholds, bad build side |
| Everything uniformly slower | Build flags, allocator, a per-chunk overhead, environment |

## 16. Engine-specific tips

- Reduce to the smallest query that shows the slowdown. Remove joins and columns until it disappears.
- Most engines have a setting to disable individual optimizer passes. Switch them off one at a time to see which one shapes the plan.
- When the plan looks right, use `perf stat` on the query: more instructions per row means more work; the same instructions with more cache or branch misses points to data layout or hash tables.

Further reading: [DuckDB join performance guide](https://duckdb.org/docs/current/guides/performance/join_operations), [Velox joins and dynamic filters](https://facebookincubator.github.io/velox/develop/joins.html), [How ClickHouse tests performance (noise handling)](https://clickhouse.com/blog/testing-the-performance-of-click-house).
