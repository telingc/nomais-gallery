"""Thumbnail for the `gd-charged-core` entry: field and potential of a charged shell.

Run:  python figures/gd-charged-core.py
Writes figures/gd-charged-core.png (2:1 aspect, matches the Ship Log card image area).
"""

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BG, PANEL, ORANGE, BRIGHT, DIM, TEXT = "#0a0c10", "#13161c", "#f0a050", "#ffb35c", "#6b5a48", "#e8e0d0"

R = 1.0
r = np.linspace(0.01, 3.0, 600)
E = np.where(r < R, 0.0, 1.0 / r**2)
V = np.where(r < R, 1.0 / R, 1.0 / r)

fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=100)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

ax.axvspan(0, R, color=PANEL, zorder=0)
ax.plot(r, E, color=BRIGHT, lw=2.2, label="E(r)")
ax.plot(r, V, color=ORANGE, lw=2.2, ls="--", label="V(r)")
ax.axvline(R, color=DIM, lw=1, ls=":")

ax.text(R / 2, 0.55, "E = 0", color=TEXT, ha="center", va="center", fontsize=13)
ax.text(2.05, 0.62, r"$\propto 1/r$", color=ORANGE, fontsize=11)
ax.text(2.35, 0.05, r"$\propto 1/r^2$", color=BRIGHT, fontsize=11)

ax.set_xlim(0, 3)
ax.set_ylim(0, 1.15)
for s in ax.spines.values():
    s.set_color(DIM)
ax.tick_params(colors=DIM, labelsize=8)
ax.set_xticks([0, 1, 2, 3])
ax.set_xticklabels(["0", "R", "2R", "3R"])
ax.set_yticks([])
ax.set_xlabel("r", color=DIM, fontsize=9)
ax.legend(frameon=False, labelcolor=TEXT, fontsize=9, loc="upper right")

fig.tight_layout(pad=0.6)
out = Path(__file__).with_suffix(".png")
fig.savefig(out, facecolor=BG)
print(f"wrote {out}")
