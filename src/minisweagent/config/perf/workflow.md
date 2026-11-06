# Performance investigation: how to work

These rules apply to any codebase. They tell you how to work. A domain guide, if one is provided, tells you what to look for.

## The order to work in

1. **Measure.** Run the workloads you were given. Record the time for each one.
2. **Decide whether it is real** (see "Code or environment?" below). Do not blame the machine until you have ruled out the code.
3. **Rank the regressions by size and work the worst first.** List the slow workloads by how much slower they are. There may be more than one cause, and a large one can hide a small one. Look at the ones that moved, and at the ones that did not. What do the slow ones do that the fast ones don't?
4. **Profile the worst workload.** Find the part (function, operator, stage) that takes the most time, or does far more work than it should. If a profiling tool is listed under Tools, use it. After fixing one cause, re-measure and go back to step 3 for the next.
5. **Find the code responsible.** Name the file and function that decides or does that work.
6. **State a hypothesis that predicts a number, then test it with an experiment.** For example: "if this is the cause, workload X drops from 500 ms to about 150 ms". Make the smallest code change that would show whether it is right, rebuild, and re-run that workload. Do this within about 10 steps of naming the code; reading more code does not count as a test. Do not try to work out the answer by hand by tracing formulas or simulating the code; the measurement is faster and settles it. If the number does not move as predicted, revert and drop the hypothesis. But a test only rules a hypothesis out if the number would have moved were the hypothesis true: if the system already corrects for what you changed (for example, it reorders conditions at runtime, so reordering them in the query changes nothing), the test tells you nothing. A hypothesis that no measurement has confirmed after about 15 steps is dropped too: go back to the profile.
7. **Fix the cause, then re-measure** the slow workloads *and* some unaffected ones. Keep the fix only if the slow ones improve beyond noise and the others do not get worse. If a comparison tool is listed under Tools, use it for this. Otherwise revert it and go back to step 4.
8. **Report** what you found, what you changed, and the before/after numbers. Claim only what you measured.

## Is there a baseline?

Find out early what you can compare against:

- **An old build, a previous commit, or git history:** compare old vs new directly, and look at the diff in the area the profile points to. `git log` and `git bisect` on that area are often the fastest route.
- **Only reference numbers** (from another run or machine): use them to see *which* workloads are slow, not *how much*. Absolute times differ between machines; the pattern of which workloads are slow does not.
- **Nothing:** judge the profile against what correct behaviour looks like. Does each stage do work proportional to its input? Do estimates match actual sizes? Is work happening that the result does not need? Is a cheaper path available but not taken? The domain guide lists what "correct" looks like.

The absence of history is not evidence that there is no regression. Read the code the profile points to as if it might be wrong.

Code that matches how you remember the upstream project is not cleared by that. Your memory is not evidence, and a regression is often a small difference from what you remember: a removed cache, a missing table entry, a fast path that is gone. Missing code counts: if a hot operation takes a generic path where similar operations have a specialized one, that absence may be the regression. Only a measurement clears a suspect.

The same goes for any other copy of the code. "It is the same in my `before` copy", "the installed Python does it too", "the counts match upstream": none of these clears a suspect. Your `before` copy was taken from the code you are investigating, so it has the same regressions; other installs and versions can have them too. A hot spot that looks the same somewhere else is still a hot spot. Clear it only by measuring that it costs what the work needs, or by trying a cheaper version and finding no gain.

## Code or environment?

| Signal | Points to code | Points to environment |
|---|---|---|
| Which workloads moved | A subset that shares a feature | All of them, by about the same amount |
| Repeat runs | Same slowdown every time | Results move around |
| CPU time vs wall time | CPU time up as well | Wall time up, CPU time flat (waiting) |
| Size | Often large (1.5x or more) on a few workloads | Small and spread across many |

- If some workloads are unchanged and others are several times slower, it is the code. A slower machine slows everything.
- A uniform slowdown can still be code: something every workload runs through (an allocator, logging, a debug check left on).

## Measuring

- Run each measurement several times (5 or more). Drop the first run. Compare medians.
- When comparing two builds, run them on the same machine, interleaved (A B A B), with the same settings.
- If the machine's speed drifts during the session, compare each slow workload with unaffected ones measured in the same pass. Their ratio stays valid when all absolute times move; drift does not explain a workload that is slow relative to its neighbours.
- Small differences are noise. Between two separately compiled builds, expect a few percent of noise in totals and more on single workloads. Do not chase or claim a change smaller than that.
- Keep long outputs short: save them to a file, then pull out the lines you need. Don't read a huge output through `head` and guess at the rest.

## Changes

- Stay in scope. A regression is a workload that is slower than the baseline. Keep a change only if it recovers a regressed workload beyond noise. Behaviour that differs from what you would expect but is not slower is out of scope: report it, don't change it.
- Fix the cause, not the symptom. A change that makes slow code a little faster while the real cause remains is not a fix.
- One change at a time. Measure each one before adding the next.
- Do not change behaviour or results to gain speed. Run the correctness checks after every fix.
- Revert changes that did not measurably help. Leave only the fixes you measured.

## Working tips

- Reduce the problem to the smallest input that still shows the slowdown.
- To test code that makes a choice at runtime (an order, a strategy, a fast or slow path), log the choice it actually makes. Reading the code tells you what it can do, not what it did.
- If the system has switches for its optimizations or stages, turn them off one at a time to see which one changes the behaviour.
- Use a CPU profiler when the high-level profile looks normal but something is still slow. A slow loop inside one stage shows up only in a CPU profile, as the functions that take the time.
- After a fix, re-run the full set of workloads, not only the one you fixed, to check you did not just move the cost.
