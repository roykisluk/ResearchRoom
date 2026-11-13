"""Draws the example project's three figures and writes its table, from made-up numbers.

    uv run --with matplotlib python example/make.py     (or any Python with matplotlib)

The outputs are committed, so the example opens without running this. The names follow the one rule the dashboard
needs: N_name_vK.ext, where N is a two- or three-digit script number and K the version.
"""
import csv, os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
YEARS = list(range(2015, 2025))
GROWTH = [1.8, 2.1, 2.4, 2.2, 2.9, -0.6, 3.4, 3.1, 2.6, 2.8]            # made up
SHARES = [("Services", 0.46), ("Manufacturing", 0.27), ("Trade", 0.17), ("Other", 0.10)]  # made up


def growth(version, markers):
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(YEARS, GROWTH, color="#2f6fd6", marker="o" if markers else None, label="Output growth")
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.set_xticks(YEARS); ax.set_xlabel("Year"); ax.set_ylabel("Percent a year")
    ax.set_title("Output growth, 2015–2024 (made-up numbers)"); ax.legend(frameon=True)
    fig.tight_layout(); fig.savefig(os.path.join(HERE, "figures", f"01_growth_v{version}.pdf")); plt.close(fig)


growth(1, markers=False)   # the first draft: the dashboard flags it "superseded" once v2 exists
growth(2, markers=True)
fig, ax = plt.subplots(figsize=(6, 3.2))
names, values = zip(*sorted(SHARES, key=lambda s: s[1]))
ax.barh(names, values, color="#7b52d9", label="Share of employment")
ax.set_xlabel("Share of employment"); ax.set_title("Employment by sector (made-up numbers)"); ax.legend(frameon=True)
fig.tight_layout(); fig.savefig(os.path.join(HERE, "figures", "02_shares_v1.png"), dpi=150); plt.close(fig)
with open(os.path.join(HERE, "tables", "02_shares_v1.csv"), "w", newline="") as fh:
    csv.writer(fh).writerows([("sector", "share")] + SHARES)
