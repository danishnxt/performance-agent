"""Bugs fixed per run, plain mini-swe-agent vs performance-agent. Data: graded final patches (see README)."""
import matplotlib.pyplot as plt

PLAIN, TOOLS, INK, MUTED, GRID, BG = "#e5622f", "#2a78d6", "#1f1f1f", "#6b6b6b", "#e6e6e6", "#fcfcfb"
# (target, total bugs, {arm: [bugs fixed per run]})
DATA = [
    ("DuckDB: 3 planted bugs", 3, {"plain": [2, 0, 2], "performance-agent": [3, 3, 3]}),
    ("CPython: 5 planted bugs", 5, {"plain": [2], "performance-agent": [2, 3, 3]}),
]

fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), facecolor=BG)
for ax, (title, total, arms) in zip(axes, DATA):
    ax.set_facecolor(BG)
    for x, (arm, vals) in enumerate(arms.items()):
        color = PLAIN if arm == "plain" else TOOLS
        # stack equal values side by side so every run is visible
        seen = {}
        for v in vals:
            k = seen.get(v, 0); seen[v] = k + 1
            same = vals.count(v)
            ax.scatter(x + (k - (same - 1) / 2) * 0.12, v, s=110, color=color, edgecolor=BG, linewidth=2, zorder=3)
    ax.set_xticks([0, 1], [f"plain mini-swe-agent\n({len(arms['plain'])} run{'s' if len(arms['plain'])>1 else ''})",
                           f"performance-agent\n({len(arms['performance-agent'])} runs)"], color=INK, fontsize=10)
    ax.set_xlim(-0.6, 1.6); ax.set_ylim(-0.4, total + 0.4)
    ax.set_yticks(range(total + 1)); ax.tick_params(axis="y", colors=MUTED, length=0); ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    for s in ax.spines.values(): s.set_visible(False)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
axes[0].set_ylabel("Bugs fixed in the final patch", color=MUTED, fontsize=10)
fig.suptitle("Same cheap model (deepseek-v4.1-flash): performance-agent fixed all 3 DuckDB bugs\nin every run; on CPython it fixed 3 of 5 at best",
             x=0.02, ha="left", color=INK, fontsize=12)
fig.text(0.02, 0.01, "One dot per run, graded by rebuilding and timing its final patch. $3 / 200-step cap per run.\n"
         "DuckDB: isolated harness, Oct 21-23, no domain guide. CPython: 5-bug target, Oct 24; one plain run only.",
         color=MUTED, fontsize=8, ha="left")
fig.tight_layout(rect=(0, 0.07, 1, 0.91))
fig.savefig("bugs-fixed.png", dpi=150, facecolor=BG)
