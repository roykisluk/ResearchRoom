"""Writes the example project: a made-up study of a minimum-wage rise in an invented region, Northvale.

    uv run --with matplotlib python example/make.py     (or any Python with matplotlib)

Every number is invented. The outputs are committed, so the example opens without running this. It draws what three
imaginary sittings at a restricted-data site brought back (docs/runs/readout_*.md), and beside the figures it writes
what a project's own figure scripts would: each caption (<stem>.txt), the plot type (<stem>.ptype), the equation of each
regression figure (<stem>.eq.json, beside <stem>.eq.pdf, the page the equation belongs to), and the option sidecars of
two families (<stem>.variant.json). It writes .room/about.tsv too, since each row carries a fingerprint of the files it
describes. Last, it dates every output by its sitting: outside git the room reads an output's date from its
modification time, so the demo site, built from a copy (tools/site.sh), groups the plots by the three sittings.
"""
import csv, hashlib, json, os, shutil, time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SITTING = {1: "2026-09-08", 2: "2026-10-06", 3: "2026-11-03"}  # the days of the three read-outs in docs/runs/
BLUE, GREY, PURPLE = "#2f6fd6", "#8a8f98", "#7b52d9"
YEARS = np.arange(2015, 2023)
rng = np.random.default_rng(2019)
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": "#e8eaee",
                     "axes.axisbelow": True, "font.size": 9, "axes.titlesize": 10, "legend.frameon": True, "legend.fontsize": 8})
DATED = []  # (path, sitting): every file written, dated at the end


def write(path, text, sitting):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    DATED.append((path, sitting))


def save(fig, name, sitting, ptype, caption, eq=None, variant=None):
    """figures/<name> and the files a figure script writes beside it."""
    path = os.path.join(HERE, "figures", name)
    stem = path.rsplit(".", 1)[0]
    fig.tight_layout()
    fig.savefig(path, dpi=150, **({"metadata": {"CreationDate": None}} if name.endswith(".pdf") else {}))  # same bytes each run
    plt.close(fig)
    DATED.append((path, sitting))
    write(stem + ".txt", caption + " Made-up numbers.", sitting)
    write(stem + ".ptype", ptype, sitting)
    if eq:  # the room shows the plain page and draws the equation from the .json (dashboard.py spec_of)
        shutil.copyfile(path, stem + ".eq.pdf")
        DATED.append((stem + ".eq.pdf", sitting))
        write(stem + ".eq.json", json.dumps(eq, ensure_ascii=False), sitting)
    if variant:
        write(stem + ".variant.json", json.dumps(variant), sitting)


def table(name, sitting, rows):
    path = os.path.join(HERE, "tables", name)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    DATED.append((path, sitting))


def years_axis(ax, years):  # a tick for every year, a label every other year
    ax.set_xticks(years)
    ax.set_xticklabels([str(y) if y % 2 == 0 else "" for y in years])


for d in ("figures", "tables", ".room"):
    os.makedirs(os.path.join(HERE, d), exist_ok=True)

# 01: the wage distribution, before and after the minimum rose from 10.00 to 12.50 in January 2019
w18 = np.maximum(np.round(np.exp(rng.normal(np.log(15.5), 0.32, 20000)), 2), 10.0)
w20 = w18 * 1.04
w20 = np.where((w20 < 12.5) & (rng.random(w20.size) > 0.12), 12.5 + rng.exponential(0.6, w20.size), w20)
s18, s20 = 100 * np.mean(w18 < 12.5), 100 * np.mean(w20 < 12.5)
for v, width in ((1, 0.5), (2, 0.25)):
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    bins = np.arange(9, 30 + width, width)
    ax.hist(w18, bins, histtype="step", color=GREY, linewidth=1.4, label="2018")
    ax.hist(w20, bins, histtype="step", color=BLUE, linewidth=1.4, label="2020")
    if v == 2:
        for x, t, dx, ha in ((10, "old minimum", -4, "right"), (12.5, "new minimum", 4, "left")):
            ax.axvline(x, color="#444", linestyle="--", linewidth=0.8)
            ax.annotate(t, (x, ax.get_ylim()[1] * 0.95), xytext=(dx, 0), textcoords="offset points", fontsize=8, ha=ha)
    ax.set_xlim(5, 30); ax.set_xlabel("Hourly wage"); ax.set_ylabel("Jobs"); ax.legend(title="Year")
    ax.set_title("Hourly wages in Northvale, 2018 and 2020")
    cap = (f"Hourly wages of jobs in Northvale in 2018 and 2020, in bins of {width:.2f}." if v == 1 else
           f"Hourly wages of jobs in Northvale in 2018 and 2020, in bins of {width:.2f}. The share of jobs paid below "
           f"12.50 falls from {s18:.1f} percent to {s20:.1f} percent.")
    save(fig, f"01_wage_distribution_v{v}.pdf", v, "histogram", cap)

# 02: the event study, one family with an outcome switch; v2 adds hours and a trend for each county
TRUE = {"Employment": np.zeros(8), "Wages": np.array([0, 0, 0, 0, 2.1, 3.0, 3.4, 3.2]),
        "Hours": np.array([0, 0, 0, 0, -0.5, -0.7, -0.4, -0.6])}
EQ = r"$y_{ct} = \alpha_c + \lambda_t + \sum_{k \neq -1} \beta_k \, D^k_{ct}%s + \varepsilon_{ct}$"
NOTES = [r"$y_{ct}$: the outcome in county $c$ and year $t$, in logs times 100",
         r"$D^k_{ct}$: one for Northvale's counties $k$ years after the raise of 2019",
         r"$\beta_k$: the gap against the neighbouring counties, relative to 2018",
         "95 percent confidence intervals, standard errors clustered by county"]
ES = {}  # (version, outcome) -> the coefficients after the raise, for the regression table
for v, outs, se in ((1, ["Employment", "Wages"], 0.55), (2, ["Employment", "Wages", "Hours"], 0.4)):
    for rank, out in enumerate(outs):
        b = np.round(TRUE[out] + rng.normal(0, se * 0.6, 8), 1)
        b[3], ses = 0.0, np.where(YEARS == 2018, 0, se)
        ES[(v, out)] = b[4:]
        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        ax.axhline(0, color="#444", linewidth=0.8)
        ax.axvline(2018.5, color="#444", linestyle="--", linewidth=0.8)
        ax.errorbar(YEARS, b, yerr=1.96 * ses, fmt="o", color=BLUE, ecolor=BLUE, capsize=3,
                    label="Northvale minus its neighbours, 95% CI")
        years_axis(ax, YEARS); ax.set_xlabel("Year"); ax.set_ylabel("Percent, relative to 2018")
        ax.set_title(f"{out}: event study around the raise of 2019"); ax.legend(loc="upper left")
        cap = (f"{out} in Northvale's counties against their neighbours', by year, relative to 2018, with 95 percent "
               f"confidence intervals. After the raise the coefficients range from {b[4:].min():.1f} to {b[4:].max():.1f} percent.")
        eq = dict(eq=EQ % (r" + \gamma_c t" if v == 2 else ""), notes=NOTES + ([r"$\gamma_c t$: a linear trend for each county"] if v == 2 else []))
        save(fig, f"02_event_study_v{v}_{out.lower()}.pdf", v + 1, "event study", cap, eq,
             {"family": "Event study around the raise", "variant": [{"axis": "outcome", "value": out, "rank": rank}]})

# 03: does employment fall more where the new minimum bit harder? 400 counties, in 20 bins of 20
bite = rng.uniform(0.05, 0.40, 400)
dy = 0.6 - 0.8 * bite + rng.normal(0, 3, 400)
X = np.column_stack([np.ones(400), bite])
coef, res = np.linalg.lstsq(X, dy, rcond=None)[:2]
slope_se = np.sqrt(res[0] / 398 * np.linalg.inv(X.T @ X)[1, 1])
order = np.argsort(bite)
bx, by = bite[order].reshape(20, 20).mean(1), dy[order].reshape(20, 20).mean(1)
fig, ax = plt.subplots(figsize=(6.4, 3.6))
ax.scatter(bx, by, color=BLUE, s=22, label="Bin of 20 counties")
xs = np.array([0.05, 0.40])
ax.plot(xs, coef[0] + coef[1] * xs, color=PURPLE, linewidth=1.2, label="Fitted line, all 400 counties")
ax.axhline(0, color="#444", linewidth=0.8)
ax.set_xlabel("Bite: share of jobs paid below 12.50 in 2018"); ax.set_ylabel("Employment change, 2018 to 2020, percent")
ax.set_title("Employment change against the bite of the new minimum"); ax.legend()
save(fig, "03_bite_binscatter_v1.pdf", 2, "binscatter",
     f"Each point is the average of 20 counties, binned by the bite of the new minimum. The fitted slope is "
     f"{coef[1]:.1f} (standard error {slope_se:.1f}), so places where the minimum bit harder lost no more jobs.")

# 04: the effect on employment by industry, sorted by size
IND = [("Food service", -1.9, 0.8), ("Retail", -0.7, 0.6), ("Hotels", -0.6, 0.9), ("Health care", 0.1, 0.5),
       ("Manufacturing", 0.2, 0.4), ("Construction", -0.2, 0.5), ("Transport", 0.4, 0.6), ("Finance", 0.3, 0.4)]
IND.sort(key=lambda r: r[1])
fig, ax = plt.subplots(figsize=(6.4, 3.8))
ys = np.arange(len(IND))
ax.errorbar([r[1] for r in IND], ys, xerr=[1.96 * r[2] for r in IND], fmt="o", color=BLUE, capsize=3, label="Effect, 95% CI")
ax.axvline(0, color="#444", linewidth=0.8)
ax.set_yticks(ys, [r[0] for r in IND]); ax.invert_yaxis()
ax.set_xlabel("Effect on employment, percent"); ax.set_title("Effect of the raise on employment, by industry"); ax.legend(loc="lower right")
save(fig, "04_effects_by_industry_v1.pdf", 3, "forest",
     f"Effect of the raise on employment in each industry, percent, with 95 percent confidence intervals. The largest "
     f"fall is in food service ({IND[0][1]:.1f} percent); no other industry's effect differs from zero at 5 percent.",
     dict(eq=r"$y_{ict} = \alpha_{ic} + \lambda_{it} + \beta_i \, \mathrm{Post}_t \times \mathrm{Northvale}_c + \varepsilon_{ict}$",
          notes=[r"$y_{ict}$: employment of industry $i$ in county $c$ and year $t$, in logs times 100",
                 r"$\beta_i$: the effect of the raise in industry $i$", "Standard errors clustered by county"]))

# 05: wage growth by age group and year: the raise reached the young most
AGES = ["16–19", "20–24", "25–34", "35–44", "45–54", "55–64"]
growth = np.round(2.0 + rng.normal(0, 0.4, (len(AGES), len(YEARS))), 1)
growth[:, YEARS == 2019] += np.array([[9.0], [6.0], [2.5], [1.0], [0.3], [0.2]])
fig, ax = plt.subplots(figsize=(6.4, 3.6))
im = ax.imshow(growth, cmap="Blues", aspect="auto")
for i in range(len(AGES)):
    for j in range(len(YEARS)):
        ax.text(j, i, f"{growth[i, j]:.1f}", ha="center", va="center", fontsize=7, color="white" if growth[i, j] > 7 else "#222")
ax.set_xticks(range(len(YEARS)), YEARS); ax.set_yticks(range(len(AGES)), AGES); ax.grid(False)
ax.set_xlabel("Year"); ax.set_ylabel("Age group"); ax.set_title("Growth of the mean hourly wage in Northvale, percent")
fig.colorbar(im, ax=ax, label="Wage growth, percent")
g = growth[:, list(YEARS).index(2019)]
save(fig, "05_wage_growth_heatmap_v1.pdf", 2, "heatmap",
     f"Growth of the mean hourly wage in Northvale, percent, by age group and year. In 2019 it is {g[0]:.1f} percent "
     f"for workers aged 16–19 and {g[4]:.1f} percent for those aged 45–54.")

# 06: employment over time, one family with a sample switch
Y6 = np.arange(2012, 2023)
for rank, (sample, noise) in enumerate((("All workers", 0.4), ("Teens", 1.2))):
    base = 100 + 1.2 * (Y6 - 2018)
    nv = np.round(base + rng.normal(0, noise, Y6.size), 1); nb = np.round(base + rng.normal(0, noise, Y6.size), 1)
    nv[Y6 == 2018] = nb[Y6 == 2018] = 100
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.plot(Y6, nb, color=GREY, marker="o", markersize=3, label="Neighbouring counties")
    ax.plot(Y6, nv, color=BLUE, marker="o", markersize=3, label="Northvale")
    ax.axvline(2018.5, color="#444", linestyle="--", linewidth=0.8)
    years_axis(ax, Y6); ax.set_xlabel("Year"); ax.set_ylabel("Employment, 2018 = 100")
    ax.set_title(f"Employment over time, {sample.lower()}"); ax.legend()
    save(fig, f"06_employment_trends_v1_{sample.split()[0].lower()}.pdf", 1, "lines",
         f"Employment of {sample.lower()} in Northvale and in its neighbouring counties, 2012–2022, as an index with 2018 = 100.",
         variant={"family": "Employment in Northvale and its neighbours", "variant": [{"axis": "sample", "value": sample, "rank": rank}]})

# 07: where the wage gain came from (a PNG, as some scripts write)
PARTS = [("Raises within firms", 2.4), ("Moves to better-paying firms", 0.7), ("A change in who works", 0.3)]
fig, ax = plt.subplots(figsize=(6.4, 2.8))
ax.barh([p for p, _ in PARTS][::-1], [x for _, x in PARTS][::-1], color=PURPLE, label="Percentage points of the wage gain")
for i, (_, x) in enumerate(PARTS[::-1]):
    ax.annotate(f"{x:.1f}", (x, i), xytext=(4, 0), textcoords="offset points", va="center", fontsize=8)
ax.set_xlabel("Percentage points"); ax.set_title(f"The {sum(x for _, x in PARTS):.1f} percent wage gain, decomposed"); ax.legend(loc="lower right")
save(fig, "07_decomposition_v1.png", 3, "bars",
     f"The rise in Northvale's mean hourly wage from 2018 to 2020, {sum(x for _, x in PARTS):.1f} percent, split into "
     f"raises within firms ({PARTS[0][1]:.1f}), moves to better-paying firms ({PARTS[1][1]:.1f}) and a change in who works ({PARTS[2][1]:.1f}).")

# the tables: summary statistics, and the main regressions in two versions
table("08_summary_stats_v1.csv", 1, [("Statistic", "Northvale", "Neighbours"), ("Jobs, thousands", 412, 1130),
      ("Mean hourly wage", round(float(w18.mean()), 2), 16.41), ("Share paid below 12.50, percent", round(s18, 1), 22.8),
      ("Share aged 16 to 24, percent", 14.2, 13.6), ("Firms", 18450, 50210)])
for v in (1, 2):
    rows = [("Outcome", "Effect, percent", "Standard error", "Counties", "County-years")]
    rows += [(out, round(float(ES[(v, out)].mean()), 2), 0.31 if v == 1 else 0.24, 400, 3200)
             for out in ("Employment", "Wages", "Hours") if (v, out) in ES]
    table(f"09_main_regressions_v{v}.csv", v + 1, rows)

# .room/about.tsv: Purpose, How, What it shows under each figure, each row with the fingerprint of the newest files and
# captions it describes (dashboard.py fingerprint): redraw a figure and its row reads "outdated" until rewritten


def fingerprint(paths):
    md5 = lambda p: hashlib.md5(open(os.path.join(HERE, p), "rb").read()).hexdigest()
    files = sorted(set(paths) | {p.rsplit(".", 1)[0] + ".txt" for p in paths})
    return hashlib.md5("".join(md5(p) for p in files).encode()).hexdigest()[:12]


e2, w2 = ES[(2, "Employment")], ES[(2, "Wages")]
ABOUT = [
    ("figures/01_wage_distribution.pdf", ["figures/01_wage_distribution_v2.pdf"],
     "Shows how far into the wage distribution the new minimum reached.",
     "Counts the jobs at each hourly wage in 2018 and in 2020.",
     f"The share of jobs paid below 12.50 falls from {s18:.1f} percent to {s20:.1f} percent."),
    ("fam:Event study around the raise", [f"figures/02_event_study_v2_{o}.pdf" for o in ("employment", "wages", "hours")],
     "Tests whether jobs were lost once the minimum rose, and how much wages and hours moved.",
     "Compares Northvale's counties with their neighbours year by year, relative to the year before the raise.",
     f"After the raise employment moves between {e2.min():.1f} and {e2.max():.1f} percent, while wages rise by "
     f"{w2.min():.1f} to {w2.max():.1f} percent."),
    ("figures/03_bite_binscatter.pdf", ["figures/03_bite_binscatter_v1.pdf"],
     "Asks whether places where the new minimum bit harder lost more jobs.",
     "Bins 400 counties by the share of jobs paid below the new minimum and fits a line through all of them.",
     f"The slope is {coef[1]:.1f}, with a standard error of {slope_se:.1f}: no sign of more job loss where the bite was larger."),
    ("figures/04_effects_by_industry.pdf", ["figures/04_effects_by_industry_v1.pdf"],
     "Looks for the industries where the raise cost jobs.",
     "Estimates the effect of the raise separately in each industry.",
     f"Only food service loses jobs, {IND[0][1]:.1f} percent."),
    ("figures/07_decomposition.png", ["figures/07_decomposition_v1.png"],
     "Splits the wage gain into raises within firms and moves between them.",
     "Decomposes the change in the mean wage into within-firm, between-firm and composition terms.",
     f"Raises within firms make up {PARTS[0][1]:.1f} of the {sum(x for _, x in PARTS):.1f} percent gain."),
]
write(os.path.join(HERE, ".room", "about.tsv"),
      "# plot\tfingerprint\tsources\tapproved\tpurpose\thow\tshows (written by make.py)\n" +
      "".join(f"{pid}\t{fingerprint(fs)}\t\t{SITTING[3]}\t{a}\t{b}\t{c}\n" for pid, fs, a, b, c in ABOUT), 3)

for path, s in DATED:  # noon on the sitting's day
    t = time.mktime(time.strptime(SITTING[s] + " 12:00", "%Y-%m-%d %H:%M"))
    os.utime(path, (t, t))
print(f"wrote {len(DATED)} files")
