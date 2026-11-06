"""Bugs fixed per run, plain mini-swe-agent vs performance-agent, one chart per target. Data: graded final patches (see README)."""
import matplotlib.pyplot as plt

PLAIN, TOOLS, INK, MUTED, GRID, BG = "#e5622f", "#2a78d6", "#1f1f1f", "#6b6b6b", "#e6e6e6", "#fcfcfb"
CHARTS = [
    ("bugs-fixed-duckdb.png", "DuckDB: performance-agent fixed all 3 planted bugs in every run", 3,
     {"plain": [2, 0, 2], "performance-agent": [3, 3, 3]},
     "deepseek-v4.1-flash, $3 / 200-step cap. One dot per run, graded by rebuilding and timing its final patch.\n"
     "Isolated harness, Oct 21-23, no domain guide."),
    ("bugs-fixed-cpython.png", "CPython: performance-agent fixed up to 3 of 5 planted bugs", 5,
     {"plain": [2], "performance-agent": [2, 3, 3]},
     "deepseek-v4.1-flash, $3 / 200-step cap. One dot per run, graded by rebuilding and timing its final patch.\n"
     "Oct 24; only one plain run."),
]

for out, title, total, arms, note in CHARTS:
    fig, ax = plt.subplots(figsize=(7, 4.2), facecolor=BG)
    ax.set_facecolor(BG)
    for x, (arm, vals) in enumerate(arms.items()):
        seen = {}
        for v in vals:  # equal values side by side so every run is visible
            k = seen.get(v, 0); seen[v] = k + 1
            ax.scatter(x + (k - (vals.count(v) - 1) / 2) * 0.12, v, s=110, color=PLAIN if arm == "plain" else TOOLS,
                       edgecolor=BG, linewidth=2, zorder=3)
    n = {a: len(v) for a, v in arms.items()}
    ax.set_xticks([0, 1], [f"plain mini-swe-agent\n({n['plain']} run{'s' if n['plain'] > 1 else ''})",
                           f"performance-agent\n({n['performance-agent']} runs)"], color=INK, fontsize=10)
    ax.set_xlim(-0.6, 1.6); ax.set_ylim(-0.4, total + 0.4)
    ax.set_yticks(range(total + 1)); ax.tick_params(axis="y", colors=MUTED, length=0); ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    for s in ax.spines.values(): s.set_visible(False)
    ax.set_ylabel(f"Bugs fixed (of {total})", color=MUTED, fontsize=10)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=12)
    fig.text(0.02, 0.01, note, color=MUTED, fontsize=8, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(out, dpi=150, facecolor=BG)
