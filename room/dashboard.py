#!/usr/bin/env python3
"""Read-only browser for figures/ and tables/: an overview of the curated findings (docs/findings.md) at /,
and every output grouped by the code/local script that writes it at /all.

Run it from the project folder, or name the project with --project DIR (room/project.py):
    python3 room/dashboard.py [--port 8765]   # serves http://127.0.0.1:8765/ (this machine only)
    python3 room/dashboard.py --selftest      # checks name parsing, grouping and flags
    python3 room/dashboard.py --export DIR    # the room as a static site
    python3 room/dashboard.py --publish       # the export, deployed as [hosted] in .room/config.toml says

/room is the control room (plan docs/plans/2026-10-15-control-room.md): a tree of plots, named by their own titles,
beside a canvas of open cards, over /index.json, the index of plots with their versions and options; its page is
room/site/app/.
    uv run --with claude-agent-sdk python room/dashboard.py --claude   # adds the owner's Claude panel (room/agent.py)
With --claude the server prints a URL carrying a per-launch token; the page keeps it as a SameSite=Strict cookie, and
every request must name 127.0.0.1 or localhost as its Host (and, for a POST, as its Origin), so no other web page can
reach the agent. --public HOST (a server with a tunnel) also lets in requests through a Cloudflare tunnel at HOST
that carry the owner's Access login, checked here (access_ok); the server stays on 127.0.0.1.

An output is NN_<what>_vK[_suffix].<ext>; NN names its script, code/local/NN_<function>_vK.py
(code/local/CLAUDE.md). The disk is read on every request; nothing outside figures/ and tables/ is
served, and nothing is written but the owner's clicks in the room, with --claude only: archiving a plot
(.room/archived.tsv, /api/archive), the importance mark (.room/importance.tsv, /api/importance). Standard library, plus poppler's pdftoppm to show a PDF's page 1 as an image.
"""
import argparse, csv, glob, hashlib, html, json, mimetypes, os, queue, re, secrets, shutil, subprocess, sys, tempfile, threading, time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from project import CFG, DATA, NAME, PEOPLE, ROOT, TITLE  # the project and its .room/config.toml (room/project.py)

LOCAL = os.path.join(ROOT, "code", "local")
DIRS = ("figures", "tables")
# the LAST _vK is the version: 30_tables_20_stayer_wages_v1_v1.tex is 30's v1 of a stem that names 20's v1
OUT = re.compile(r"^(?P<stem>(?P<nn>\d{2,3})_.+)_v(?P<k>\d+)(?P<sfx>(?:_+[A-Za-z0-9]+)*)\.(?P<ext>\w+)$")
SCRIPT = re.compile(r"^(\d{2,3})_(.+)_v(\d+)\.py$")
TEXT = {"md", "csv", "tex", "txt", "log"}
FINDINGS = os.path.join(ROOT, "docs", "findings.md")
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "app")  # the UI sits beside the engine
LEGEND = [
    ("superseded", "a higher _vK of the same name (folder, stem, suffix, extension) exists; shown only under "
                   "'older versions'."),
    ("behind", "the newest _vK of this name is below the newest version of its script in code/local: that "
               "version either no longer writes it or has not been run."),
    ("older", "the file is older (modification time) than the script version whose _vK it carries: the script "
              "was edited after this output was written. Meaningless after a fresh clone or a sync that resets "
              "modification times."),
]
LEGEND_D = dict(LEGEND)


def owner(nn, rest, fns):
    """The script function that writes an output: the only one under NN, else the one sharing its first word."""
    c = list(fns.get(nn, {}))
    if len(c) > 1:  # ponytail: NN collisions (14) split by first word only; a script-source scan if that breaks
        c = [f for f in c if f.split("_")[0] == rest.split("_")[0]]
    return c[0] if len(c) == 1 else None


def build(outputs, scripts):
    """outputs [(dir, name, mtime)], scripts [(name, mtime)] -> groups {(nn, fn): {key: [file]}}, unmapped, fns."""
    fns = defaultdict(dict)  # nn -> function -> {K: mtime}
    for name, mt in scripts:
        m = SCRIPT.match(name)
        if m:
            fns[m[1]].setdefault(m[2], {})[int(m[3])] = mt
    groups, unmapped = defaultdict(lambda: defaultdict(list)), []
    for d, name, mt in outputs:
        m = OUT.match(name)
        rest = m["stem"][len(m["nn"]) + 1:] if m else ""
        fn = owner(m["nn"], rest, fns) if m else None
        if m and fn is None and m["nn"] not in fns:
            fn = rest  # no script in code/local/ carries this number: the output is a plot of its own, with no script link
        f = dict(dir=d, name=name, mtime=mt, k=int(m["k"]) if m else 0, flags=[])
        if fn is None:
            unmapped.append(f)
            continue
        groups[(m["nn"], fn)][(d, f"{m['stem']}{m['sfx']}.{m['ext']}")].append(f)
    for (nn, fn), keys in groups.items():
        vers = fns.get(nn, {}).get(fn, {})  # empty for an output no script owns
        for files in keys.values():
            files.sort(key=lambda f: -f["k"])
            for f in files:
                if f["k"] < files[0]["k"]:
                    f["flags"].append("superseded")
                    continue
                if vers and f["k"] < max(vers):
                    f["flags"].append("behind")
                if f["k"] in vers and f["mtime"] < vers[f["k"]]:
                    f["flags"].append("older")
    return groups, unmapped, fns


def ls(*parts):
    """The names in ROOT/parts, [] when the folder is absent: every folder but figures/ or tables/ is optional and
    switches its part of the room on by being there (design 2026-11-13 §2). In the folder's own order, not sorted:
    books() keeps the first of two codebooks with the same version, so sorting changed six definitions (11-13)."""
    try:
        return os.listdir(os.path.join(ROOT, *parts))
    except FileNotFoundError:
        return []


def icon(name):
    """The path of a site icon: the project's own in .room/ when it is there, else the engine's default."""
    own = os.path.join(DATA, name)
    return own if os.path.isfile(own) else os.path.join(os.path.dirname(APP), name)


def findings_text():
    """docs/findings.md, empty when the project has none."""
    if not os.path.isfile(FINDINGS):
        return ""
    with open(FINDINGS, encoding="utf-8") as fh:
        return fh.read()


def scan():
    outs = [(d, n, os.path.getmtime(p)) for d in DIRS for n in ls(d)
            if not n.startswith(".") and not n.endswith((VARIANT, PTYPE_EXT, EQ_EXT, EQ_TXT)) and os.path.isfile(p := os.path.join(ROOT, d, n))]
    return build(outs, [(n, os.path.getmtime(os.path.join(LOCAL, n))) for n in ls("code", "local")])


# The control room (/room, plan docs/plans/2026-10-15-control-room.md) reads one index. A plot is a family of
# files: the versions of one name, or, when figure_rules.save(family=, variant=) wrote a <figure>.variant.json
# beside each file, every option of that family (bins, trim, window), each file one combination.
VARIANT = ".variant.json"


def sidecar(d, name):
    """The option sidecar of figures/<name> or tables/<name> (one per stem: a table's formats share it), or None."""
    p = os.path.join(ROOT, d, name.rsplit(".", 1)[0] + VARIANT)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


TITLES = {}  # (path, mtime) -> title; ponytail: in-memory and unbounded, like PNG


def out_title(path):
    """A reader's title for an output: a figure's caption file's first sentence, else the first line of its page
    that starts with a capital and has four words (the suptitle; a trailing comma continues it); a table's LaTeX
    caption or Markdown heading. '' when none is found."""
    full = os.path.join(ROOT, path)
    key = (path, os.path.getmtime(full))
    if key in TITLES:
        return TITLES[key]
    stem, ext = os.path.splitext(full)
    t = ""
    if os.path.isfile(stem + ".txt") and path.startswith("figures/"):
        with open(stem + ".txt", encoding="utf-8") as fh:
            t = re.split(r"(?<=[a-z0-9%])\.\s", fh.read().strip(), maxsplit=1)[0].rstrip(".")
    elif ext == ".pdf" and path.startswith(("figures/", "out/")) and shutil.which("pdftotext"):
        x = subprocess.run(["pdftotext", "-l", "1", full, "-"], capture_output=True, text=True).stdout
        for para in re.split(r"\n\s*\n", x):
            ls = [l.strip() for l in para.strip().splitlines()]
            if not ls or not ls[0][:1].isupper():
                continue
            t = ls[0]
            for l in ls[1:]:
                if not t.endswith(","):
                    break
                t += " " + l
            if len(re.findall(r"[A-Za-z]{3,}", t)) >= 4:
                break
            t = ""
    else:
        for src in (stem + ".tex", stem + ".md"):
            if os.path.isfile(src):
                with open(src, encoding="utf-8", errors="replace") as fh:
                    m = re.search(r"\\caption\{(.+)\}" if src.endswith(".tex") else r"^#+ (.+)$", fh.read(), re.M)
                if m:
                    t = re.sub(r"\\([a-zA-Z]+)\*?(?:\{([^{}]*)\})?", lambda c: c[2] or "", m[1].replace("\\_", "_"))
                    t = " ".join(t.replace("`", "").split()).strip(" .")
                    break
    TITLES[key] = t
    return t


# The room's plot-type filter: the first rule whose words appear in a figure's path, caption file or first page.
# A figure saved with figure_rules.save(plot_type=) states its type in <stem>.ptype, which wins over the guess.
PTYPES = [("heatmap", r"heatmap"), ("forest", r"forest|confidence interval"),
          ("binscatter", r"\bbins?\b|binned|binscatter"), ("event study", r"event study|year effects|year by year"),
          ("histogram", r"\bhist|distribution"), ("scatter", r"scatter|\bagainst\b|each point"),
          ("bars", r"\bbars?\b|decomposition"), ("lines", r"series|by year|over time|\blines?\b|\d{4}[–-]\d{4}")]
PTYPE = {}  # (path, mtime) -> type
PTYPE_EXT = ".ptype"
# A regression figure saved with figure_rules.save(equation=) has <stem>.eq.pdf beside it: the same page with its
# equation, which the dashboard shows in place of the plain page (10-20); bundles copy the plain one. Beside it,
# <stem>.eq.json holds the equation as text: the room then shows the plain page and draws the equation large in its
# sidebar (11-12; spec_of).
EQ_EXT = ".eq.pdf"
EQ_TXT = ".eq.json"


def plot_type(path):
    full = os.path.join(ROOT, path)
    key = (path, os.path.getmtime(full))
    if key not in PTYPE:
        stem, t = os.path.splitext(full)[0], ""
        if os.path.isfile(stem + PTYPE_EXT):
            with open(stem + PTYPE_EXT, encoding="utf-8") as fh:
                PTYPE[key] = fh.read().strip()
            return PTYPE[key]
        if os.path.isfile(stem + ".txt"):
            with open(stem + ".txt", encoding="utf-8") as fh:
                t = fh.read()
        elif path.endswith(".pdf") and shutil.which("pdftotext"):
            t = subprocess.run(["pdftotext", "-l", "1", full, "-"], capture_output=True, text=True).stdout
        s = (path + " " + t).lower().replace("_", " ")
        PTYPE[key] = next((n for n, r in PTYPES if re.search(r, s)), "other")
    return PTYPE[key]


# a table written in several formats is one plot: Markdown first (drawn as a table), then CSV, LaTeX, PDF, then the rest
# by name; a fixed order, since the disk's listing order differs between machines (a Linux server picked .pdf where the Mac had .tex)
TWIN = {"md": 0, "csv": 1, "tex": 2, "pdf": 3}


def base(key):
    """A table's name without its format: x.md, x.csv, x.tex and x_latex.txt (a CSV's LaTeX twin) are one table."""
    b, e = key.rsplit(".", 1)
    return b[:-6] if e == "txt" and b.endswith("_latex") else b


def index(scanned, ftext, side, titler=lambda path: "", names={}, vars_of=lambda path: [], typer=lambda path: "other",
          read_of=lambda path, mtime: ""):
    """scan() output, docs/findings.md text, sidecar reader, title reader, chosen names {plot id: title}
    (.room/titles.tsv), mini-codebook reader (variables), read-date reader (reads) -> {plots, findings, scripts} for
    /index.json.

    A plot's axes are its sidecar's option axes in their order, plus "version" when it has more than one _vK;
    files are keyed by their values joined with "|", in axis order, version last."""
    groups, _, fns = scanned
    plots, members, twin = {}, defaultdict(list), {}
    for (nn, fn), keys in sorted(groups.items()):
        script = f"code/local/{nn}_{fn}_v{max(fns[nn][fn])}.py" if fn in fns.get(nn, {}) else ""
        best = {}  # tables: base name -> the key shown
        for d, key in keys:
            if d == "tables":
                b, e = base(key), key.rsplit(".", 1)[1]
                if b not in best or (TWIN.get(e, 9), e) < (TWIN.get(best[b].rsplit(".", 1)[1], 9), best[b].rsplit(".", 1)[1]):
                    best[b] = key
        for (d, key), files in sorted(keys.items()):
            if d == "tables" and best[base(key)] != key:
                shown = keys[(d, best[base(key)])]
                for f in files:  # a finding that names the .tex twin finds the shown file of its version, else the newest
                    twin[f"{d}/{f['name']}"] = f"{d}/{next((g for g in shown if g['k'] == f['k']), shown[0])['name']}"
                continue
            if d == "figures" and key.endswith(".txt"):
                continue  # a figure's caption: shown folded under the figure (caption_txt), not a plot of its own
            for f in files:
                sc = side(d, f["name"])
                pid = "fam:" + sc["family"] if sc else f"{d}/{key}"
                p = plots.setdefault(pid, dict(id=pid, title=sc["family"] if sc else key.rsplit(".", 1)[0],
                                               kind="figure" if d == "figures" else "table", script=script, nn=nn,
                                               raw=[]))
                p["raw"].append((f, d, [(v["axis"], v["value"], v["rank"]) for v in sc["variant"]] if sc else []))
                if pid not in members[f"{nn}_{fn}"]:
                    members[f"{nn}_{fn}"].append(pid)
    by_path = {}
    for pid, p in plots.items():
        raw = p.pop("raw")
        ks = sorted({f["k"] for f, _, _ in raw}, reverse=True)
        axes = {}  # ranks from the newest version; a value only older versions have (76 v2's 33 bins) goes last
        for f, _, opts in sorted(raw, key=lambda t: -t[0]["k"]):
            for a, v, r in opts:
                axes.setdefault(a, {}).setdefault(v, r if f["k"] == ks[0] else 1000 + r)
        p["axes"] = [[a, sorted(vals, key=lambda v: (vals[v], v))] for a, vals in axes.items()]
        if len(ks) > 1:
            p["axes"].append(["version", [f"v{k}" for k in ks]])
        p["files"], newest = {}, []
        for f, d, opts in raw:
            vals = dict((a, v) for a, v, _ in opts)
            combo = [vals.get(a, "") for a, _ in p["axes"] if a != "version"] + ([f"v{f['k']}"] if len(ks) > 1 else [])
            path = f"{d}/{f['name']}"
            cap = os.path.join(ROOT, d, f["name"].rsplit(".", 1)[0] + ".txt") if d == "figures" else ""
            p["files"]["|".join(combo)] = dict(path=path, img=img_url(path), k=f["k"], flags=f["flags"],
                                               mtime=int(f["mtime"]), caption_txt=os.path.isfile(cap))
            if (v := vars_of(path)):
                p["files"]["|".join(combo)]["vars"] = v
            by_path[path] = (pid, "|".join(combo))
            if f["k"] == ks[0]:
                newest.append(f)
        p["flags"] = sorted({x for f in newest for x in f["flags"]})
        if not pid.startswith("fam:"):
            p["name"] = pretty(p["title"])
            p["title"] = names.get(pid) or titler(f"{newest[0]['dir']}/{newest[0]['name']}") or p["name"]
        p["updated"] = time.strftime("%Y-%m-%d", time.localtime(max(f["mtime"] for f in newest)))
        p["read"] = max(read_of(f"{f['dir']}/{f['name']}", f["mtime"]) for f in newest)
        p["ptype"] = typer(f"{newest[0]['dir']}/{newest[0]['name']}") if p["kind"] == "figure" else "table"
    n = Counter(p["title"] for p in plots.values())
    for p in plots.values():  # the same title on two plots: each shows its file name too
        p["dup"] = n[p["title"]] > 1
    for path, shown in twin.items():
        if shown in by_path:
            by_path[path] = by_path[shown]
    F = parse_findings(ftext)
    def once(paths):  # one entry per plot, at the first path naming it: a finding may name several files of one family
        seen = {}
        for q in paths:
            if q in by_path and by_path[q][0] not in seen:
                seen[by_path[q][0]] = list(by_path[q])
        return list(seen.values())
    findings = [dict(id=x["id"], title=x["title"], changed=x.get("changed", ""),
                     plots=once(([x["exhibit"]] if x.get("exhibit") else []) + x["also"]),
                     missing=[q for q in ([x["exhibit"]] if x.get("exhibit") else []) + x["also"] if q not in by_path])
                for x in F["findings"]]
    scripts = [dict(id=s, title=s.replace("_", " ", 1), plots=ps) for s, ps in sorted(members.items())]
    return dict(read=time.strftime("%Y-%m-%d %H:%M:%S"), title=TITLE, plots=plots, findings=findings, scripts=scripts)


def pretty(stem):
    """A name without its script number and underscores: 63_isf_1a_rerun -> isf 1a rerun."""
    return re.sub(r"^\d{2,3}_", "", stem).replace("_", " ")


# A plot whose own caption or heading makes a poor title (a file name, a column name, a long sentence) gets a name
# chosen by reading it: .room/titles.tsv, "<plot id>\t<title>", keyed by the plot id, which carries no version, so a
# new version keeps its name (10-15: plain, intelligently chosen names). INTERNAL finds what still needs one.
NAMES = os.path.join(DATA, "titles.tsv")
INTERNAL = re.compile(r"\b\d{2,3}_\w+|\b\w+_v\d+|\.(csv|tex|md|txt|pdf)\b|\b\d{2,3} v\d+|\b\d{2,3}'s\b|\b[a-z]+_[a-z_]+\b|\bv\d+\b")


def names():
    if not os.path.isfile(NAMES):
        return {}
    with open(NAMES, encoding="utf-8") as fh:
        return dict(l.rstrip("\n").split("\t", 1) for l in fh if "\t" in l and not l.startswith("#"))


# Mini codebooks (10-15): a table or figure that shows a variable name says below it what the name means. The
# definitions are the Columns tables of the codebooks, tables/*_csv_codebook_*.md, the newest version of each. A table
# that names the CSV it renders (script 30's captions do) or is that CSV takes that codebook's definitions for every
# column; otherwise only code-like names (snake_case, CAPS_CODES) are defined, each by its most common definition.
CODE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b(?!\.csv)")
BOOK = re.compile(r"^(?:\d{2,3}_)?(.+?)(?:_v\d+)?\.csv$")  # 23_hitech_share_v8.csv and hitech_share.csv are one CSV
BOOKS, VARS = {}, {}  # (name, mtime) -> (csv, {variable: definition}); (path, mtime) -> (defs, undefined)


def books():
    """{csv name: {variable: definition}} from the newest version of each codebook in tables/."""
    newest = {}
    for n in ls("tables"):
        m = OUT.match(n)
        if m and "_csv_codebook_" in n and m["ext"] == "md" and int(m["k"]) > newest.get(m["stem"], (0, ""))[0]:
            newest[m["stem"]] = (int(m["k"]), n)
    out = {}
    for _, n in newest.values():
        full = os.path.join(ROOT, "tables", n)
        key = (n, os.path.getmtime(full))
        if key not in BOOKS:
            with open(full, encoding="utf-8") as fh:
                t = fh.read()
            h = re.search(r"^# Codebook: `([^`]+)`", t, re.M)
            cols = t.split("## Columns", 1)[1].split("\n## ", 1)[0] if "## Columns" in t else ""
            rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in cols.splitlines() if l.startswith("|")]
            BOOKS[key] = (BOOK.match(h[1])[1] if h and BOOK.match(h[1]) else n,
                          {r[0]: r[1] for r in rows[2:] if len(r) > 1 and r[0] and r[1]})  # rows 0-1: header, rule
        out.setdefault(*BOOKS[key])
    return out


# the data owner's own field names (FIELD_A1, VAR_10, CODE) may be defined in a sibling repository's codebooks:
# rows | # | NAME | English label | ...
_sib = CFG.get("siblings", {}).get("codebooks")  # [siblings] codebooks: a folder beside the project; absent: none
SIBLING = os.path.normpath(os.path.join(ROOT, _sib)) if _sib else ""
FIELDS = {}


def fields():
    """{the data owner's field name: its English label} from the sibling codebooks' *_en.md ({} when there are none)."""
    if not FIELDS and SIBLING and os.path.isdir(SIBLING):
        for n in sorted(os.listdir(SIBLING)):
            if n.endswith("_en.md"):
                with open(os.path.join(SIBLING, n), encoding="utf-8") as fh:
                    for l in fh:
                        c = [x.strip() for x in l.strip().strip("|").split("|")]
                        if len(c) > 2 and c[0].isdigit() and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", c[1]) and c[2] not in ("", "—"):
                            FIELDS.setdefault(c[1], c[2])
    return FIELDS


# names no codebook defines, defined by reading where they appear: .room/variables.tsv, "<name>\t<definition>"; a
# definition "-" marks a name that is no variable (a file or list name), shown and reported nowhere (10-15)
GLOSS = os.path.join(DATA, "variables.tsv")


def glossed():
    if not os.path.isfile(GLOSS):
        return {}
    with open(GLOSS, encoding="utf-8") as fh:
        return dict(l.rstrip("\n").split("\t", 1) for l in fh if "\t" in l and not l.startswith("#"))


def define(text, heads, B, own=None, F={}, V={}):
    """The visible text and column headers of an output -> ([[name, definition]], [code-like names left undefined]).
    B: the project's codebooks; V: .room/variables.tsv; F: the data owner's field names, used where no codebook
    defines a name, in that order."""
    src = [B[k] for k in sorted({BOOK.match(c)[1] for c in re.findall(r"[\w.-]+\.csv", text) if BOOK.match(c)}
                                | ({own} if own else set())) if k in B]
    defs, missing, seen = [], [], set()
    caps = [w for w in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", text) if w in F or w in V]  # TURNOVER, CODE: field names with no underscore
    for n in list(heads) + CODE.findall(text) + caps:
        if n in seen:
            continue
        seen.add(n)
        d = next((b[n] for b in src if n in b), None)
        if d is None and (CODE.fullmatch(n) or n in F or n in V):
            pool = Counter(b[n] for b in B.values() if n in b)
            d = pool.most_common(1)[0][0] if pool else V.get(n) or F.get(n)
            if d == "-":  # no variable
                continue
            if d is None:
                missing.append(n)
        if d:
            defs.append([n, d])
    return defs, missing


def seen(path):
    """What a reader sees of an output: (text, column headers). A figure: its caption file and page 1."""
    full, ext = os.path.join(ROOT, path), path.rsplit(".", 1)[-1].lower()
    text, heads = "", []
    if ext in TEXT:
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read(200_000)
        ls = text.splitlines()
        if ext == "md":  # every Markdown table's header row: the line above its |:--- rule
            heads = [c.strip() for i, l in enumerate(ls[1:], 1) if re.match(r"^\|\s*:?-", l)
                     for c in ls[i - 1].strip().strip("|").split("|")]
        elif ext == "csv" and ls:
            heads = [c.strip().strip('"') for c in ls[0].lstrip("\ufeff").split(",")]
        elif ext == "tex":  # the rows between \toprule and \midrule
            top = text.split("\\midrule", 1)[0].rsplit("\\toprule", 1)[-1]
            heads = [re.sub(r"\\[a-zA-Z]+\*?(\{[^{}]*\})?|[{}\\]", " ", c).strip() for c in top.split("&")]
    elif ext == "pdf" and shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", "-l", "2", full, "-"], capture_output=True, text=True).stdout
    cap = full.rsplit(".", 1)[0] + ".txt"
    if path.startswith("figures/") and os.path.isfile(cap):
        with open(cap, encoding="utf-8") as fh:
            text += "\n" + fh.read()
    return text, [h for h in heads if h]


def variables(path, undefined=False):
    """The mini codebook of an output, [[name, definition]] (or the code-like names it leaves undefined); [] for a
    codebook itself or a missing file. Cached by modification time, like TITLES."""
    full = os.path.join(ROOT, path)
    if "_csv_codebook_" in path or not os.path.isfile(full):
        return []
    key = (path, os.path.getmtime(full))
    if key not in VARS:
        own = BOOK.match(os.path.basename(path))
        VARS[key] = define(*seen(path), books(), own[1] if own and path.startswith("tables/") else None, fields(), glossed())
    return VARS[key][1 if undefined else 0]


def minicb(defs):
    """A mini codebook under a table or figure on the dashboard."""
    if not defs:
        return ""
    return ("<details class=vars open><summary>What the names mean</summary><dl>"
            + "".join(f"<dt><code>{html.escape(n)}</code></dt><dd>{prose(d)[3:-4]}</dd>" for n, d in defs) + "</dl></details>")


# Under each figure, three sentences: Purpose, How, What it shows (10-20). Claude writes them into .room/about.tsv
# after each readback (taxcoding § 5; `--about-todo` lists the plots without a current row); the owner approves each in the
# room. A row holds the fingerprint of the figure and caption it was written about, so a redraw puts it back to
# "outdated"; every number in What it shows must be given by the caption or the row's sources (unmatched()).
ABOUT = os.path.join(DATA, "about.tsv")
ABOUT_COLS = ("plot", "print", "sources", "approved", "purpose", "how", "shows")
NUM = re.compile(r"(?<![\w.])[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")  # prose: 12,345 is one number
VAL = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")  # a source's cells: -6.25e-05,0.28 is two
MD5 = {}  # (path, mtime) -> md5, like PNG


def about_rows():
    if not os.path.isfile(ABOUT):
        return {}
    with open(ABOUT, encoding="utf-8") as fh:
        return {r[0]: dict(zip(ABOUT_COLS, r)) for l in fh if l.strip() and not l.startswith("#")
                and len(r := l.rstrip("\n").split("\t")) == len(ABOUT_COLS)}


def md5(path):
    full = os.path.join(ROOT, path)
    key = (path, os.path.getmtime(full))
    if key not in MD5:
        with open(full, "rb") as fh:
            MD5[key] = hashlib.md5(fh.read()).hexdigest()
    return MD5[key]


def about_files(p):
    """A figure plot's newest-version files and their captions: what its three sentences are written about."""
    top = max(f["k"] for f in p["files"].values())
    fs = [f["path"] for f in p["files"].values() if f["k"] == top]
    return sorted(set(fs) | {q.rsplit(".", 1)[0] + ".txt" for q in fs if os.path.isfile(os.path.join(ROOT, q.rsplit(".", 1)[0] + ".txt"))})


def fingerprint(p):
    return hashlib.md5("".join(md5(q) for q in about_files(p)).encode()).hexdigest()[:12]


def nums(text):
    return [s.replace(",", "").replace("−", "-").rstrip(".") for s in NUM.findall(text)]


def source_text(spec):
    """A source: a path under the repository, or path#column=value[&column=value] for the CSV rows that hold those
    values (a fits table holds every panel's rows; one panel's row keeps the check narrow). A trailing *100 reads its
    values times 100: a slope stored in log units, drawn and quoted in log points."""
    spec = spec.strip()
    if spec.endswith("*100"):
        return " ".join(repr(float(x) * 100) for x in VAL.findall(source_text(spec[:-4])))
    path, _, flt = spec.partition("#")
    full = os.path.join(ROOT, path)
    if not path or not os.path.isfile(full) or os.path.relpath(full, ROOT).startswith(".."):
        return ""
    with open(full, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not flt:
        return text
    rows = list(csv.reader(text.splitlines()))
    want = [(rows[0].index(c), v) for c, _, v in (x.partition("=") for x in flt.split("&")) if c in rows[0]]
    return "\n".join(",".join(r) for r in rows[1:] if want and all(i < len(r) and r[i] == v for i, v in want))


def unmatched(sentence, texts, exact=""):
    """The numbers in a sentence that no number in texts gives at the sentence's precision, sign aside (a fall of 0.29
    is a b of -0.29), as a percent of a share only when "pp" or "%" follows (4.7 pp for 0.047); a number written
    exactly so in `exact` (the caption, itself written by code) passes too."""
    have = {abs(float(x)) for t in texts for x in VAL.findall(t)}
    lit = {x.lstrip("-") for x in nums(exact)}
    miss = []
    for m in NUM.finditer(sentence):
        s = m[0].replace(",", "").replace("−", "-").rstrip(".")
        d, v = len(s.split(".")[1]) if "." in s else 0, abs(float(s))
        pct = re.match(r"\s*(pp\b|%|percent)", sentence[m.end():])
        if s.lstrip("-") not in lit and not any(round(h, d) == v or (pct and round(h * 100, d) == v) for h in have):
            miss.append(s)
    return miss


def about_of(p, row):
    """The three sentences for the room: text, status (approved, draft or outdated) and the unmatched numbers."""
    caps = [q for q in about_files(p) if q.endswith(".txt")]
    texts = [source_text(s) for s in row["sources"].split(";")]
    status = "outdated" if row["print"] != fingerprint(p) else "approved" if row["approved"] else "draft"
    return dict(purpose=row["purpose"], how=row["how"], shows=row["shows"], status=status,
                unmatched=unmatched(row["shows"], texts, "\n".join(source_text(q) for q in caps)))


def approve(pid, day=None, path=ABOUT, col=3, cols=len(ABOUT_COLS)):
    """The owner's approval in the room: that plot's row takes today's date in its approved column; no other line changes."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    out, hit = [], False
    for l in lines:
        r = l.rstrip("\n").split("\t")
        if r[0] == pid and len(r) == cols and not l.startswith("#"):
            r[col], hit = day or time.strftime("%Y-%m-%d"), True
            l = "\t".join(r) + "\n"
        out.append(l)
    if hit:
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(out)
    return hit


# The owner's finding aids: .room/tags.tsv, "<plot id>\t<tag>, <tag>", e.g. Levels, Age dummies for the pages a coauthor asked for
# (10-20). Written with the three sentences after each readback; the room colours each tag and filters on a click,
# for collaborators too (everything but Claude; 10-20).
TAGS = os.path.join(DATA, "tags.tsv")


def tags():
    if not os.path.isfile(TAGS):
        return {}
    with open(TAGS, encoding="utf-8") as fh:
        return {r[0]: [t.strip() for t in r[1].split(",") if t.strip()] for l in fh
                if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) == 2}


# Tasks (10-21): the requests a meeting set, each found by the tags of its plots. .room/tasks.tsv,
# "<task name>\t<meeting date>\t<tag>, <tag>"; the room's "by read date" tree splits each sitting by them (a plot goes
# to the first task whose tags it carries; one with none goes to Other). On the hosted copy too.
TASKS = os.path.join(DATA, "tasks.tsv")


def tasks(path=TASKS):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [dict(name=r[0], meeting=r[1], tags=[t.strip() for t in r[2].split(",") if t.strip()]) for l in fh
                if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) == 3]


# The owner's Archive button in the room (10-20): .room/archived.tsv, "<plot id>\t<date>", one line per member of a family,
# so a family is archived only while all its members are and a page drawn into it later brings it back. Archived plots
# hide unless the room's filter shows them, for collaborators as for the owner (everything but Claude; 10-20).
ARCHIVED = os.path.join(DATA, "archived.tsv")


def archived(path=ARCHIVED):
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {l.rstrip("\n").split("\t")[0] for l in fh if l.strip() and not l.startswith("#")}


def archive(ids, on, day=None, path=ARCHIVED):
    """The ids leave the file and, when on, come back with today's date; False for an id that is no plain line."""
    if not ids or not all(isinstance(i, str) and i.strip() and not re.search(r"[\t\r\n]", i) for i in ids):
        return False
    lines = open(path, encoding="utf-8").readlines() if os.path.isfile(path) else []
    out = [l for l in lines if l.startswith("#") or l.rstrip("\n").split("\t")[0] not in ids]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out + ([f"{i}\t{day or time.strftime('%Y-%m-%d')}\n" for i in ids] if on else []))
    return True


# The owner's importance mark in the room (10-21): how key a figure is to its task (.room/tasks.tsv), not overall; Claude
# sets it at each read-back (taxcoding § 5) and --labels-todo lists the figures still without one.
# .room/importance.tsv, "<plot id>\t<!, !! or !!!>\t<date>", one line per member of a family, as archived.tsv; the
# card's button cycles it and the card and the tree show it, collaborators too.
IMPORTANCE = os.path.join(DATA, "importance.tsv")
MARKS = ("!", "!!", "!!!")


MARKS_FROM = str(CFG.get("marks_from", ""))  # Claude marks every figure read since this date (10-21); absent: every read counts


def task_of(p, ts=None):
    """The task a plot sits under in the room (app.js taskSubs): the first whose tags it carries; "" for Other."""
    return next((t["name"] for t in (tasks() if ts is None else ts) if set(p.get("tags", [])) & set(t["tags"])), "")


def marks(path=IMPORTANCE):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r[0]: r[1] for l in fh if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) >= 2 and r[1] in MARKS}


def mark(ids, level, day=None, path=IMPORTANCE):
    """The ids leave the file and, with a level, come back with it and today's date; False for a bad id or level."""
    if level and level not in MARKS or not archive(ids, False, path=path):
        return False
    if level:
        with open(path, "a", encoding="utf-8") as fh:
            fh.writelines(f"{i}\t{level}\t{day or time.strftime('%Y-%m-%d')}\n" for i in ids)
    return True


# "What is this?" (10-20): one sentence above each figure that says at a glance what it shows, for a first-time
# reader. .room/whatis.tsv, "<plot id>\t<date approved>\t<sentence>"; from the 11-06 read (11-08) the
# sentence is the question the plot asks and a fourth column holds its answer, the bottom line. A family reads it
# through its members (the main one first), so a row keyed by a member survives the family's id. Written with the about rows (taxcoding § 5).
WHATIS = os.path.join(DATA, "whatis.tsv")


def whatis_rows(path=WHATIS):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r[0]: dict(text=r[2], answer=r[3] if len(r) == 4 else "", status="approved" if r[1] else "draft") for l in fh
                if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) in (3, 4)}


# Graph D · story (10-21; plan docs/plans/2026-10-21-story-graph-build.md): the research as two trees of plots,
# the question on each arrow and its answer under the plot it led to. .room/story.tsv, one row per arrow: A<n> the
# order it happened, B<n> the paper's story. An answer's numbers must be in the files its source names
# (log YYYY-MM-DD, readout <stem>, takeaways, findings), or the room lists them and offers no Approve.
STORY = os.path.join(DATA, "story.tsv")
STORY_COLS = ("id", "approved", "kind", "from", "from_title", "to", "to_title", "question", "answer", "source")


def story_sources(src):
    out = []
    for m in re.finditer(r"\blog (\d{4}-\d\d-\d\d)|\breadout (\S+?)(?=[\s;,)]|$)|\b(takeaways?)\b|\b(findings)\b", src):
        if m[1]:
            out.append(f"docs/log/{m[1]}.md")
        elif m[2]:
            out += sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "runs", f"readout_{m[2]}*.md")))
        elif m[3]:
            out += sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "runs", "takeaways_*.md")))
        else:
            out.append("docs/findings.md")
    return list(dict.fromkeys(out))


def story_rows(path=STORY):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = [dict(zip(STORY_COLS, r)) for l in fh
                if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) == len(STORY_COLS)]
    for r in rows:
        r.update(status="approved" if r["approved"] else "draft",
                 # the sources are prose, where 2,468 is one number (VAL splits it, as a CSV's cells need)
                 unmatched=unmatched(r["answer"], [re.sub(r"(?<=\d),(?=\d{3}\b)", "", source_text(s))
                                                   for s in story_sources(r["source"])]))
    return rows


PAGE = {}  # (path, mtime) -> page-1 text; ponytail: in-memory and unbounded, like PNG


def page_text(path):
    """A figure's matching text: its caption file, else the first 600 characters of page 1 (title, legend, axes)."""
    full = os.path.join(ROOT, path)
    key = (path, os.path.getmtime(full))
    if key not in PAGE:
        cap = full.rsplit(".", 1)[0] + ".txt"
        if os.path.isfile(cap):
            with open(cap, encoding="utf-8") as fh:
                PAGE[key] = fh.read()
        elif full.endswith(".pdf") and shutil.which("pdftotext"):  # without poppler the page is drawn nowhere: pdf_png says so
            x = subprocess.run(["pdftotext", "-l", "1", full, "-"], capture_output=True, text=True).stdout
            PAGE[key] = " ".join(l.strip() for l in x.splitlines() if l.strip() and not re.fullmatch(r"[-−\d.,%\s]+", l.strip()))[:600]
        else:
            PAGE[key] = ""
    return PAGE[key]


def index_now(bundled=True):
    """The room's index; bundled=False leaves the loose plots as they were (--about-todo's rows are kept per member)."""
    read_of, sittings = reads_now()
    ix = dict(index(scan(), findings_text(), sidecar, out_title, names(), variables, plot_type, read_of), sittings=sittings)
    plots, scripts = enclave(read_of, names())
    ix["plots"].update(plots)
    ix["scripts"] += scripts
    for pid, t in tags().items():
        if pid in ix["plots"]:
            ix["plots"][pid]["tags"] = t
    rows = about_rows()
    for pid, p in ix["plots"].items():
        if p["kind"] == "figure" and pid in rows:
            p["about"] = dict(about_of(p, rows[pid]), key=pid)
    if bundled:
        bundle(ix, names(), options(), lambda path: set(WORD.findall(page_text(path).lower())))
    ix["takeaways"] = takeaways(ix)
    gone, said, marked, helps = archived(), whatis_rows(), marks(), switch_help()
    for pid, p in ix["plots"].items():
        p["help"] = {a: h for a, _ in p["axes"] if (h := helps.get((p["nn"], a)) or helps.get(("*", a)))}
        p["drawn"] = "in the enclave" if p["script"].startswith("code/enclave/") else "locally"
        ids = p.get("members") or [pid]
        p["archived"] = all(i in gone for i in ids)
        p["importance"] = next((marked[i] for i in [pid] + ids if i in marked), "")
        if (k := next((i for i in [pid] + ids if i in said), None)):
            p["whatis"] = dict(said[k], key=k)
        for f in p["files"].values():  # an equation kept as text: the room shows the plain page and draws it beside
            if (s := spec_of(f["path"])):
                f.update(eq=s, img=img_url(f["path"], plain=True))
    ix["tasks"] = tasks()
    ix["story"] = [dict(r, from_ok=r["from"] in ix["plots"], to_ok=r["to"] in ix["plots"]) for r in story_rows()]
    return ix


# A sitting's main takeaways (10-20): docs/runs/takeaways_<read date>.md, written at read-back in findings.md's
# format, one "## N. <plain sentence>" per point, its exhibit and also lines naming the files that support it. The
# room's "by read date" tree opens them as a box on the canvas, for collaborators too; asking Claude about a point is
# The owner's alone (everything but Claude; 10-20).
TAKEAWAYS = re.compile(r"^takeaways_(\d{4}-\d\d-\d\d)\.md$")


def takeaways(ix, texts=None):
    """{read date: [{n, t, body, plots: [[pid, combo]], missing}]}; texts ({date: text}) only for the selftest."""
    if texts is None:
        runs = os.path.join(ROOT, "docs", "runs")
        texts = {m[1]: open(os.path.join(runs, n), encoding="utf-8").read() for n in ls("docs", "runs") if (m := TAKEAWAYS.match(n))}
    by_path = {f["path"]: [pid, c] for pid, p in ix["plots"].items() for c, f in p["files"].items()}
    out = {}
    for d, text in texts.items():
        out[d] = [dict(n=i + 1, t=re.sub(r"^\d+\.\s*", "", x["title"]), body=x["body"],
                       plots=[by_path[q] for q in paths if q in by_path], missing=[q for q in paths if q not in by_path])
                  for i, x in enumerate(parse_findings(text)["findings"])
                  for paths in [([x["exhibit"]] if x.get("exhibit") else []) + x["also"]]]
    return out


# Figures drawn inside the enclave stay where the readback files them, out/<NN_function_vK>/RELEASE/ (10-20): one
# plot per function and file name, its versions on a version axis as in index(); no caption file, so the title is the
# page's own. Only RELEASE/ (the checked set); on the hosted copy too, every version since the floor (10-20).
RUN = re.compile(r"^(?P<nn>\d{2,3})_(?P<fn>.+)_v(?P<k>\d+)$")
ENCLAVE_FIG = re.compile(r"^out/\d{2,3}_[\w-]+_v\d+/RELEASE/[^/]+\.(pdf|png)$")


def enclave(read_of=lambda path, mtime: "", names={}):
    by = defaultdict(list)
    for stem in sorted(ls("out")):
        rel = os.path.join(ROOT, "out", stem, "RELEASE")
        if not (m := RUN.match(stem)) or not os.path.isdir(rel):
            continue
        for n in os.listdir(rel):
            if ENCLAVE_FIG.match(p := f"out/{stem}/RELEASE/{n}"):
                by[(m["nn"], m["fn"], n)].append(dict(k=int(m["k"]), path=p, mtime=os.path.getmtime(os.path.join(ROOT, p))))
    plots, scripts = {}, defaultdict(list)
    for (nn, fn, n), fs in sorted(by.items()):
        fs.sort(key=lambda f: -f["k"])
        top, pid, many = fs[0], f"out/{nn}_{fn}/{n}", len(fs) > 1
        name = pretty(f"{nn}_{n.rsplit('.', 1)[0]}")
        plots[pid] = dict(id=pid, kind="figure", script=f"code/enclave/{nn}_{fn}_v{top['k']}.py", nn=nn, name=name,
                          title=names.get(pid) or out_title(top["path"]) or name, dup=False, flags=[],
                          axes=[["version", [f"v{f['k']}" for f in fs]]] if many else [],
                          files={(f"v{f['k']}" if many else ""): dict(path=f["path"], img=img_url(f["path"]), k=f["k"],
                                 flags=["superseded"] if f is not top else [], mtime=int(f["mtime"]), caption_txt=False)
                                 for f in fs},
                          updated=time.strftime("%Y-%m-%d", time.localtime(top["mtime"])),
                          read=read_of(top["path"], top["mtime"]), ptype=plot_type(top["path"]))
        scripts[f"{nn}_{fn}"].append(pid)
    n = Counter(p["title"] for p in plots.values())
    for p in plots.values():  # a page's title is often an axis label, shared by its siblings: show the file name too
        p["dup"] = n[p["title"]] > 1
    return plots, [dict(id=f"enclave:{s}", title=s.replace("_", " ", 1) + " (enclave)", plots=ps) for s, ps in scripts.items()]


# A figure drawn under several choices that no sidecar names is one plot too (10-20: "automatically, without me
# asking each time"): figure files of one script and folder whose names differ by one token (a word, or a window:
# 2012_2019) and that show the same figure; the tokens that differ are its option axes. The same figure: the same
# title head (up to the first , ; : or (), or, when neither title is a chosen name or a caption (an enclave page
# without a suptitle yields a panel label), page-1 words at least 70% the same; or, whatever the titles, the two tokens
# they differ by sit on one axis in this script's rows of .room/options.tsv (10-20: imputed and measured labor
# cost are one plot), and tokens declared on different axes never share one. A loose page one token from a file of
# a sidecar family joins that family with that file's choices. .room/options.tsv names the chips; a sidecar wins over
# all of this, so one also undoes a wrong merge (figures/ only: enclave pages carry none).
YEAR = re.compile(r"(?:19|20)\d\d")
WORD = re.compile(r"[a-z][a-z'-]{2,}")
OPTIONS = os.path.join(DATA, "options.tsv")


def tokens(name):
    """A file name less its script number -> its tokens; two years in a row are one window (2012_2019 -> 2012–2019)."""
    out = []
    for t in re.sub(r"^\d{2,3}_", "", name).split("_"):
        if out and YEAR.fullmatch(t) and YEAR.fullmatch(out[-1]):
            out[-1] += "–" + t
        else:
            out.append(t)
    return tuple(out)


def apart(a, b):
    """The tokens two token tuples differ by when they differ in one (one swapped, or one more: "-" for the one a
    tuple lacks), else None."""
    if len(a) == len(b):
        d = [(x, y) for x, y in zip(a, b) if x != y]
        return d[0] if len(d) == 1 else None
    s, l = sorted((a, b), key=len)
    return next(((l[i], "-") for i in range(len(l)) if l[:i] + l[i + 1:] == s), None) if len(l) == len(s) + 1 else None


def one_apart(a, b):
    return apart(a, b) is not None


def head(title):
    """What two drawings of one figure share in their titles: up to the first , ; : or (, else the title less digits."""
    h = re.split(r"[,;:(]", title, maxsplit=1)[0].strip().lower()
    return h if len(h.split()) >= 3 else re.sub(r"\d", "", title.lower())


def glued(s, u, t):
    out = []
    for x in s:
        if x == t and out and out[-1] == u:
            out[-1] = f"{u} {t}"
        else:
            out.append(x)
    return tuple(out)


def option_axes(seqs, axis_of=lambda t: None):
    """{node: tokens} -> one {node: token, "" for none} per axis: the tokens some names have and others lack, a token
    that only ever follows one such token glued to it (links_any is one choice), grouped so that no two tokens of an
    axis meet in one name and no two declared on different axes (axis_of: .room/options.tsv) share one."""
    apart_axes = lambda t, u: None not in (axis_of(t), axis_of(u)) and axis_of(t) != axis_of(u)
    while True:
        has = defaultdict(set)
        for n, s in seqs.items():
            for t in s:
                has[t].add(n)
        opt = [t for t in dict.fromkeys(t for s in seqs.values() for t in s) if len(has[t]) < len(seqs)]
        pre = {t: {s[i - 1] if i else "" for s in seqs.values() for i, x in enumerate(s) if x == t} for t in opt}
        pair = next(((u, t) for t in opt if len(pre[t]) == 1 and (u := next(iter(pre[t]))) in opt and u != t
                     and not apart_axes(u, t)), None)
        if not pair:
            break
        seqs = {n: glued(s, *pair) for n, s in seqs.items()}
    groups = []
    for t in opt:
        g = next((g for g in groups if not any(has[t] & has[x] or apart_axes(t, x) for x in g)), None)
        if g is None:
            groups.append(g := [])
        g.append(t)
    return [{n: next((t for t in s if t in g), "") for n, s in seqs.items()} for g in groups]


SWITCHES = os.path.join(DATA, "switches.tsv")


def switch_help():
    """.room/switches.tsv, the help behind the room's ⓘ beside each switch (11-12): {(NN or *, axis): text}."""
    if not os.path.isfile(SWITCHES):
        return {}
    with open(SWITCHES, encoding="utf-8") as fh:
        return {(r[0], r[1]): r[2].replace("\\n", "\n") for l in fh
                if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) == 3}


def options():
    """.room/options.tsv: [(NN or *, token, axis, chip)] in the file's order."""
    if not os.path.isfile(OPTIONS):
        return []
    with open(OPTIONS, encoding="utf-8") as fh:
        return [tuple(r) for l in fh if not l.startswith("#") and len(r := l.rstrip("\n").split("\t")) == 4]


def natural(s):
    return [(0, float(x), "") if re.fullmatch(r"\d+(?:\.\d+)?", x) else (1, 0, x) for x in re.split(r"(\d+(?:\.\d+)?)", s) if x]


def chips(nn, vals, rows):
    """One axis's tokens ("" for the names without any) -> (axis name, {token: chip}, chips in order, tokens shown
    raw). From .room/options.tsv rows, this script's before "*" ones; the token "-" names the "" value of an axis. Else
    "window" or "year" for years, "option" otherwise, the token itself and "base"; listed values first, in file order."""
    def row(t, axis=None):
        return next(((i, r) for s in (nn, "*") for i, r in enumerate(rows) if r[0] == s and r[1] == t
                     and axis in (None, r[2])), None)
    hit = {t: row(t) for t in vals if t}
    name = next((h[1][2] for h in hit.values() if h), None) or (
        "window" if all(re.fullmatch(r"\d{4}–\d{4}", t) for t in hit) else "year" if all(YEAR.fullmatch(t) for t in hit)
        else "option")
    if "" in vals:
        hit[""] = row("-", name)
    label = {t: hit[t][1][3] if hit[t] else t or "base" for t in vals}
    if len(set(label.values())) < len(label):  # two tokens, one chip: the tokens, so no file is lost
        label = {t: t or "base" for t in vals}
    order = sorted(vals, key=lambda t: (0, hit[t][0], False, []) if hit[t] else (1, 0, t != "", natural(t)))
    raw = [t for t in vals if t and not hit[t] and name not in ("window", "year")] + (["-"] if "" in vals and not hit[""] else [])
    return name, label, [label[t] for t in order], raw


def stem_title(ts):
    """The words all a family's titles begin with, cut at a comma or a word; '' when that leaves under 3 words."""
    t = os.path.commonprefix(ts)
    if len(set(ts)) > 1:
        t = re.sub(r"(?:[,;:]\s|\s\(|\s)[^,;:(]*$", "", t) if re.search(r"[,;:]\s|\s\(", t) else t.rsplit(" ", 1)[0]
    t = t.strip(" ,;:–-")
    return t if len(t.split()) >= 3 else ""


def main_key(p):
    """The key of the file the room opens a plot on: the newest version, then each axis's first value."""
    vals = [v for a, v in p["axes"] if a != "version"]
    rank = lambda k: [vals[i].index(x) if x in vals[i] else 999 for i, x in enumerate(k.split("|")[:len(vals)])]
    return min(p["files"], key=lambda k: (-p["files"][k].get("k", 0), rank(k)))


def main_file(p):
    return p["files"][main_key(p)]


def bundle(ix, names={}, rows=(), words=lambda path: set()):
    """Merge the index's loose figure plots into option families (the comment above), in place. A family lists its
    members, the plots it replaces (their rows in .room/about.tsv, tags.tsv, archived.tsv and whatis.tsv are read
    through them); a member's three sentences go to its files; findings and script groups point at the family."""
    plots, nodes = ix["plots"], defaultdict(list)  # (folder, NN) -> nodes
    for pid, p in plots.items():
        if p["kind"] != "figure":
            continue
        if pid.startswith("fam:"):
            ex, seen = sum(a != "version" for a, _ in p["axes"]), {}
            for c, f in sorted(p["files"].items(), key=lambda kv: -kv[1]["k"]):
                m = OUT.match(os.path.basename(f["path"]))
                if m and (m["stem"] + m["sfx"], os.path.dirname(f["path"])) not in seen:
                    seen[(m["stem"] + m["sfx"], os.path.dirname(f["path"]))] = dict(
                        fam=pid, name=m["stem"] + m["sfx"], vals=c.split("|")[:ex], title=p["title"], sure=True, path=f["path"], k=f["k"])
            for (name, d), n in seen.items():
                nodes[(d, name.split("_")[0])].append(n)
        else:
            top = max(p["files"].values(), key=lambda f: f["k"])
            nodes[(pid.rsplit("/", 1)[0], p["nn"])].append(dict(
                fam=None, pid=pid, name=pid.rsplit("/", 1)[1].rsplit(".", 1)[0], title=p["title"],
                sure=pid in names or top["caption_txt"], path=top["path"], k=top["k"]))

    def declared(nn, t):  # the axes this script's rows of .room/options.tsv put a token on ("*" rows only name chips)
        return {r[2] for r in rows if r[0] == nn and r[1] == t}

    def same(a, b, nn):
        d = apart(tokens(a["name"]), tokens(b["name"]))
        if d is None:
            return False
        if head(a["title"]) == head(b["title"]) or declared(nn, d[0]) & declared(nn, d[1]):
            return True  # the same title, or two choices Claude named on one axis (imputed and measured labor cost)
        A, B = (set() if a["sure"] or b["sure"] else words(n["path"]) for n in (a, b))
        return len(A & B) >= 0.7 * len(A | B) > 0

    def parts(ns, nn):
        top = list(range(len(ns)))
        find = lambda i: i if top[i] == i else find(top[i])
        for i in range(len(ns)):
            for j in range(i):
                if same(ns[i], ns[j], nn):
                    top[find(i)] = find(j)
        out = defaultdict(list)
        for i, n in enumerate(ns):
            out[find(i)].append(n)
        return list(out.values())

    remap = {}  # (old pid, old key) -> [plot, key]

    def merge(c, fam, folder, nn):
        """Merge one component -> the loose pages it left out, or None when it merged nothing. A loose page joins a
        sidecar family only beside a sidecar file with its choices drawn by the same script version: what a name means
        can change between versions (23's year effects were weighted by the mean denominator before v8)."""
        ex, left = [n for n in c if n["fam"]], []
        sets = [set(tokens(n["name"])) for n in ex]
        explained = set.union(*sets) - set.intersection(*sets) if ex else set()  # what the sidecar's axes already say
        for n in c:
            if not n["fam"]:
                a = next((e for e in ex if set(tokens(e["name"])) & explained == set(tokens(n["name"])) & explained), None)
                if ex and (a is None or a["k"] != n["k"]):
                    left.append(n)
                n["vals"] = a["vals"] if a else []
        c = [n for n in c if not any(n is x for x in left)]
        if len(c) < 2 or all(n["fam"] for n in c):
            return left
        axes = option_axes({i: tuple(t for t in tokens(n["name"]) if t not in explained) for i, n in enumerate(c)},
                           lambda t: next(iter(declared(nn, t)), None))
        named, used, raw = [], Counter(["version"] + [a for a, _ in plots[fam]["axes"]] if fam else ["version"]), []
        for ax in axes:
            name, label, order, r = chips(nn, list(dict.fromkeys(ax.values())), rows)
            used[name] += 1
            named.append((name if used[name] == 1 else f"{name} {used[name]}", label, order, ax))
            raw += r
        at = {(n["name"], os.path.dirname(n["path"])): i for i, n in enumerate(c) if n["fam"]}
        entries = []  # (values, file, newest, member)
        if fam:
            P0, top = plots[fam], max(f["k"] for f in plots[fam]["files"].values())
            for k0, f in P0["files"].items():
                m = OUT.match(os.path.basename(f["path"]))
                i = at.get((m["stem"] + m["sfx"], os.path.dirname(f["path"]))) if m else None
                vals = k0.split("|")[:len(ex[0]["vals"])] + [lab[ax[i]] if i is not None else lab.get("") for _, lab, _, ax in named]
                entries.append((vals, f, f["k"] == top and "superseded" not in f["flags"], None, (fam, k0)))
        for i, n in enumerate(c):
            if not n["fam"]:
                for k0, f in plots[n["pid"]]["files"].items():
                    vals = n["vals"] + [lab[ax[i]] for _, lab, _, ax in named]
                    entries.append((vals, f, "superseded" not in f["flags"], n["pid"], (n["pid"], k0)))
        ks = sorted({f["k"] for _, f, _, _, _ in entries}, reverse=True)
        keys = ["|".join(v + ([f"v{f['k']}"] if len(ks) > 1 else [])) for v, f, _, _, _ in entries]
        names_at = defaultdict(set)  # choice -> the pages drawn at it, whatever their version
        for v, f, _, member, _ in entries:
            names_at[tuple(v)].add(member or (lambda m: m["stem"] + m["sfx"] if m else f["path"])(OUT.match(os.path.basename(f["path"]))))
        if any(None in v for v, _, _, _, _ in entries) or len(set(keys)) < len(keys) or any(len(x) > 1 for x in names_at.values()):
            return None  # a sidecar file with no "base" on a new axis, or two pages at one choice: left as they are
        old = [plots[n["pid"]] for n in c if not n["fam"]]
        if fam:
            pid, p = fam, P0
            eaxes = [x for x in P0["axes"] if x[0] != "version"]
        else:
            pre = "_".join(os.path.commonprefix([n["name"].split("_") for n in c])) or c[0]["name"]
            pid = f"auto:{folder}/{pre}"
            while pid in plots:
                pid += "+"
            p, eaxes = dict(id=pid, kind="figure", script=old[0]["script"], nn=nn, name=pretty(pre)), []
        p["axes"] = eaxes + [[name, order] for name, _, order, _ in named] + ([["version", [f"v{k}" for k in ks]]] if len(ks) > 1 else [])
        p["files"] = {}
        for key, (_, f, newest, member, was) in zip(keys, entries):
            a = plots[member].get("about") if member else None
            p["files"][key] = dict(f, newest=newest, **({"member": member} if member else {}), **({"about": a} if a else {}))
            remap[was] = [pid, key]
        main = main_file(p).get("member") or fam
        p["members"] = ([fam] if fam else []) + sorted((n["pid"] for n in c if not n["fam"]), key=lambda m: m != main)
        if not fam:
            p["title"] = names.get(pid) or stem_title([o["title"] for o in old]) or plots[main]["title"]
            p["ptype"] = plots[main]["ptype"]
        if raw:
            p["unnamed"] = raw
        every = old + ([P0] if fam else [])
        if (t := list(dict.fromkeys(t for x in every for t in x.get("tags", [])))):
            p["tags"] = t
        p["flags"] = sorted({x for f in p["files"].values() if f["newest"] for x in f["flags"]})
        p["updated"] = max(x["updated"] for x in every)
        p["read"] = max(x["read"] for x in every)
        for n in c:
            if not n["fam"]:
                del plots[n["pid"]]
        plots[pid] = p
        done.add(fam)
        return left

    done = set()  # sidecar families merged once already: a second merge would re-key them (a family spans scripts)
    for (folder, nn), ns in sorted(nodes.items()):
        todo = parts(ns, nn)
        while todo:
            c = todo.pop()
            loose, fams = [n for n in c if not n["fam"]], {n["fam"] for n in c if n["fam"]}
            if not loose or len(c) < 2:
                continue
            left = merge(c, next(iter(fams)), folder, nn) if len(fams) == 1 and not fams & done else \
                merge(c, None, folder, nn) if not fams else None
            if left is None and fams:
                left = loose  # it cannot join the sidecar family: the loose pages alone
            todo += parts(left or [], nn)
    for x in ix["findings"]:  # one entry per plot, as index() keeps them: the first file named of a family
        once = {}
        for pk in x["plots"]:
            once.setdefault((pk := remap.get(tuple(pk), pk))[0], pk)
        x["plots"] = list(once.values())
    fam_of = {m: pid for pid, p in plots.items() for m in p.get("members", [])}
    for s in ix["scripts"]:
        s["plots"] = list(dict.fromkeys(fam_of.get(q, q) for q in s["plots"]))
    n = Counter(p["title"] for p in plots.values())
    for p in plots.values():
        p["dup"] = n[p["title"]] > 1


def about_todo():
    """The figure plots with no row in .room/about.tsv or one written about an older drawing, as JSON lines with what a
    writer reads: the plot's files, its caption, its script and the tables that script writes."""
    ix, rows = index_now(bundled=False), about_rows()
    for pid, p in sorted(ix["plots"].items()):
        a = p.get("about")
        if p["kind"] != "figure" or (a and a["status"] != "outdated"):
            continue
        print(json.dumps(dict(plot=pid, title=p["title"], read=p["read"], why="outdated" if a else "missing",
                              print=fingerprint(p), files=about_files(p), script=p["script"],
                              tables=sorted(f"{d}/{n}" for d in ([os.path.dirname(about_files(p)[0])] if pid.startswith("out/")
                                                                 else ["tables"])
                                            for n in ls(d)
                                            if n.endswith(".csv") and (pid.startswith("out/") or n.startswith(p["nn"] + "_"))),
                              old=rows.get(pid)), ensure_ascii=False))


def labels_todo():
    """The room's words still to write, as JSON lines: each figure without a What is this? line (plot: the id to write
    it under, a family's main member), each without a tag (.room/tags.tsv; every figure carries one, 10-20) and
    each family whose chips show a file-name token (.room/options.tsv), and each figure read since the 23rd sitting
    without an importance mark to its task (.room/importance.tsv; Claude sets it at read-back, 10-21)."""
    for pid, p in sorted(index_now()["plots"].items()):
        if p["kind"] != "figure" or p["archived"] or all("superseded" in f["flags"] for f in p["files"].values()):
            continue  # what the room hides by default: archived, and older drawings only
        if not p.get("whatis"):
            print(json.dumps(dict(plot=(p.get("members") or [pid])[0], why="what is this", title=p["title"],
                                  file=main_file(p)["path"], script=p["script"]), ensure_ascii=False))
        if not p.get("tags"):
            print(json.dumps(dict(plot=(p.get("members") or [pid])[0], why="tag", title=p["title"], read=p["read"],
                                  script=p["script"]), ensure_ascii=False))
        if not p.get("importance") and p["read"] >= MARKS_FROM and (t := task_of(p)):
            print(json.dumps(dict(plot=pid, why="importance", task=t, title=p["title"], read=p["read"]), ensure_ascii=False))
        if p.get("unnamed"):
            print(json.dumps(dict(plot=pid, why="chips", nn=p["nn"], tokens=p["unnamed"], axes=p["axes"][:3],
                                  files=sorted(f["path"] for f in p["files"].values() if f["newest"])[:6]), ensure_ascii=False))


# house rule 6's floor of 10 units dates from 2026-09-14; outputs written before it stay on the Mac (10-15). An
# output's date is the earlier of its modification time and the commit that first added it: a pull or a clone (the server
# deploys on a timer) gives old files a new modification time, never a new first commit.
FLOOR = time.mktime(time.strptime(str(CFG["floor"]), "%Y-%m-%d")) if CFG.get("floor") else 0  # absent: every file is hosted


def added():
    """{path: unix time of the commit that first added it} for figures/ and tables/; {} outside git."""
    r = subprocess.run(["git", "-C", ROOT, "log", "--relative", "--diff-filter=A", "--format=%ct", "--name-only", "--", *DIRS, "out"],
                       capture_output=True, text=True)
    out, t = {}, 0
    for l in r.stdout.splitlines():  # newest commit first, so the last time kept for a path is its first addition
        if l.isdigit():
            t = int(l)
        elif l:
            out[l] = t
    return out


# Every readback files docs/runs/readout_<date>_<label>.md (taxcoding skill); its label is the sitting ("23rd") or, in
# older ones, the scripts read ("61_64"). The room groups plots by the read their outputs come from (10-20).
READOUT = re.compile(r"^readout_(\d{4}-\d\d-\d\d)_(.+)\.md$")
SITTING = re.compile(r"^\d+(st|nd|rd|th|[a-z])(-\d+(st|nd|rd|th|[a-z]))?$")  # 38b: a re-ship's letter (ship.sh)


def reads(log, readouts):
    """git log text ("@%ct" per commit, --name-status, newest first, over docs/runs, figures and tables) and the file
    names in docs/runs -> (read_of(path, mtime), {read date: sitting}). An output's read date is that of the newest
    readback commit (one adding a readout) that touched a file of its stem (the figure, its caption); failing that,
    the newest read on or before the commit that first added it, or before its modification time while uncommitted;
    "" before the first read."""
    rb, born, commits = {}, {}, []
    for l in log.splitlines():
        if l.startswith("@"):
            commits.append((int(l[1:]), files := []))
        elif l:
            st, *ps = l.split("\t")
            files.append((st[0], ps[-1]))
    for t, files in commits:  # newest first: the first readback seen wins, the last addition seen is the first
        days = [m[1] for s, p in files if s == "A" and (m := READOUT.match(os.path.basename(p)))]
        for s, p in files:
            if p.startswith(DIRS + ("out/",)):
                stem = p.rsplit(".", 1)[0]
                if days:
                    rb.setdefault(stem, max(days))
                if s == "A":
                    born[stem] = t
    sit = defaultdict(list)
    for n in readouts:
        if (m := READOUT.match(n)):
            sit[m[1]] += [m[2]] if SITTING.match(m[2]) else []
    dates = sorted(sit)

    def read_of(path, mtime):
        stem = path.rsplit(".", 1)[0]
        if stem in rb:
            return rb[stem]
        day = time.strftime("%Y-%m-%d", time.localtime(born.get(stem, mtime)))
        return max((d for d in dates if d <= day), default="")
    return read_of, {d: " + ".join(sorted(s)) + " sitting" for d, s in sit.items() if s}


def reads_now():
    r = subprocess.run(["git", "-C", ROOT, "log", "--relative", "--format=@%ct", "--name-status", "--", "docs/runs", "out", *DIRS],
                       capture_output=True, text=True)
    return reads(r.stdout, ls("docs", "runs"))


def hosted(ix, since=FLOOR, born={}):
    """The index for the hosted room (plan docs/plans/2026-10-15-hosted-room.md): every file written since the
    disclosure floor, older versions and archived plots included, since collaborators see everything but the Claude
    features (10-20); no script numbers or file names. Option values left with no file are dropped; a finding or a
    takeaway naming a dropped file points at its plot's main file. The "older" flag (modification times) is left out:
    the hosted copy is built from a clone, where those times mean nothing."""
    ix = json.loads(json.dumps(ix))
    for pid, p in list(ix["plots"].items()):
        files = {c: dict(path=f["path"], img=f["img"], k=f["k"], mtime=f["mtime"], caption_txt=f["caption_txt"],
                         flags=[x for x in f["flags"] if x != "older"], **({"vars": f["vars"]} if f.get("vars") else {}),
                         **({"about": f["about"]} if f.get("about") else {}), **({"eq": f["eq"]} if f.get("eq") else {}))
                 for c, f in p["files"].items() if min(f["mtime"], born.get(f["path"], f["mtime"])) >= since}
        if not files:
            del ix["plots"][pid]
            continue
        axes = [[a, [v for v in vals if any(k.split("|")[i] == v for k in files)]] for i, (a, vals) in enumerate(p["axes"])]
        for k in ("members", "unnamed"):
            p.pop(k, None)
        p.update(files=files, axes=axes, script="", nn="", name="", flags=[x for x in p["flags"] if x != "older"], dup=False)
    at = lambda pid, c: [pid, c if c in ix["plots"][pid]["files"] else main_key(ix["plots"][pid])]
    for x in ix["findings"]:
        x.update(plots=[at(pid, c) for pid, c in x["plots"] if pid in ix["plots"]], missing=[])
    for points in ix.get("takeaways", {}).values():
        for x in points:
            x.update(plots=[at(pid, c) for pid, c in x["plots"] if pid in ix["plots"]], missing=[])
    for r in ix.get("story", []):  # Graph D: a plot dropped here keeps its stored title, without a link; no sources
        r.update(from_ok=r["from"] in ix["plots"], to_ok=r["to"] in ix["plots"], source="")
    return dict(ix, scripts=[], hosted=True)


# the site icon: files at the site root (the project's in .room/ with their source icon.svg, else the engine's site/), which Access and _middleware.js leave public;
# Safari's bookmark icon fetch carries no login, and a data-URI icon did not show either (docs/lessons.md 10-14)
ICON = '<link rel=icon href=favicon.ico sizes=any><link rel=apple-touch-icon href=apple-touch-icon.png>'
STYLE = """<meta name=viewport content="width=device-width,initial-scale=1">
{ICON}
<link rel=preconnect href=https://fonts.gstatic.com crossorigin><link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400..700;1,6..72,400&family=Public+Sans:wght@400;500;600&display=swap">
<style>
:root{--paper:#F3F5F7;--surface:#fff;--ink:#16212E;--muted:#5B6877;--rule:#D8DEE4;--accent:#1F5FA8;--low:#B23B3B;
 --med:#B7791F;--high:#2E7D4F;--contested:#B23B3B;--serif:"Newsreader",Georgia,serif;
 --sans:"Public Sans",system-ui,sans-serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#0F141A;--surface:#161D25;--ink:#E3E8ED;--muted:#93A0AE;--rule:#2A3440;
 --accent:#86B4EA;--low:#E07070;--med:#D9A04A;--high:#5DB282;--contested:#E07070;color-scheme:dark}}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 var(--sans)}
body.plain{max-width:1100px;margin:0 auto;padding:32px 24px}
a{color:var(--accent);text-underline-offset:2px} :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font-size:.88em;background:color-mix(in srgb,var(--rule) 50%,transparent);padding:0 .3em;border-radius:3px}
h1,h2{font-family:var(--serif);font-weight:600;letter-spacing:-.01em} h2{font-size:22px;margin:40px 0 10px}
.dim{color:var(--muted);font-weight:normal;font-size:13px} summary{cursor:pointer}
button,input,select,textarea{font:inherit;font-size:13px;color:var(--ink);background:var(--surface);border:1px solid var(--rule);border-radius:6px}
button{padding:5px 12px;cursor:pointer} button:hover{border-color:var(--muted)} input,select,textarea{padding:5px 8px}
.fl{font-size:11px;font-weight:600;padding:1px 7px;border-radius:999px;color:#fff;white-space:nowrap}
.superseded{background:#8A94A0} .behind{background:#B7791F} .older{background:#B23B3B} .fl.new{background:var(--accent);color:var(--surface)}
.warn{color:var(--contested);font-weight:600} .err{color:var(--contested);font-size:12px;margin-left:8px}
/* overview: a sticky rail of findings beside the reading column */
.layout{display:grid;grid-template-columns:300px minmax(0,1fr);max-width:1320px;margin:0 auto}
.rail{position:sticky;top:0;height:100vh;box-sizing:border-box;overflow:auto;padding:32px 20px;border-right:1px solid var(--rule)}
.rail h1{font-size:27px;line-height:1.1;margin:0} .rail .sub{font:italic 19px var(--serif);color:var(--muted);margin:2px 0 14px}
.rail ol{list-style:none;padding:0;margin:22px 0}
.rail li a{display:block;padding:8px 10px;border-radius:6px;
 color:var(--ink);text-decoration:none;font-size:13.5px;line-height:1.35}
.go{display:inline-block;font-size:13px;padding:5px 12px;border:1px solid var(--rule);border-radius:6px;background:var(--surface);color:var(--ink);text-decoration:none} .go:hover{border-color:var(--muted)}
.rail li a:hover{background:var(--surface)} .rail li a.on{background:var(--surface);box-shadow:inset 3px 0 var(--accent)}
.key{display:flex;flex-direction:column;align-items:flex-start;gap:2px;font-size:12px;color:var(--muted);margin:10px 0 0}
.filters{gap:6px} .filters label{display:flex;gap:8px;align-items:center} .filters select{font-size:12px;padding:2px 4px}
main{padding:32px 44px 80px;min-width:0} main>*{max-width:880px} main p{max-width:72ch}
.lede p{font:18px/1.55 var(--serif);margin-top:0} .rail .fl.new{background:none;color:var(--accent);padding:0 2px}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:24px 28px;margin:0 0 28px;scroll-margin-top:16px}
.kick{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;font-size:12.5px;color:var(--muted)}
.card h2{font-size:24px;line-height:1.25;margin:8px 0 18px}
.ex{margin:0 0 6px} .ex img{display:block;max-width:100%;max-height:75vh;margin:0 auto;padding:8px;box-sizing:border-box;background:#fff;border-radius:6px}
.vars{font-size:12.5px;margin:6px 0} .vars dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 12px;margin:6px 0} .vars dd{margin:0;color:var(--muted)}
.cap{font-size:13px;color:var(--muted)} .cap p{margin:6px 0 0} ul.pws{padding-left:20px} ul.pws li{margin:4px 0}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:24px}
.thumbs a{display:block;background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:6px;color:var(--muted);text-decoration:none;font-size:12px}
.thumbs a:hover{border-color:var(--accent)} .thumbs a[hidden]{display:none}
.thumbs img{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#fff;border-radius:4px;margin-bottom:6px} .card details{margin:12px 0;font-size:13px}
.pv iframe,.pv img{width:100%;height:70vh;border:1px solid var(--rule);border-radius:6px;background:#fff;resize:vertical;margin-top:6px}
.pv img{height:auto;max-height:70vh;object-fit:contain}
.cm{border-top:1px solid var(--rule);margin-top:20px;padding-top:12px;font-size:13px} .cm textarea{width:100%;box-sizing:border-box;margin:4px 0}
.add{background:var(--accent);color:var(--surface);border:0;border-radius:50%;width:34px;height:34px;padding:0;font-size:24px;line-height:1;vertical-align:middle;margin-left:6px}
.send{background:var(--accent);color:var(--surface);border:0;border-radius:50%;width:32px;height:32px;padding:0;font-size:18px;font-weight:600}
.cm ul,.board ul{list-style:none;padding:0;margin:4px 0}
.nt{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:8px 10px;margin:6px 0;font-size:13px}
.nt.done{opacity:.6} .ctl{margin:6px 0;font-size:12px} .rp{margin:3px 0 3px 12px} .nt textarea{width:100%;box-sizing:border-box}
.board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px} .board>div{background:color-mix(in srgb,var(--rule) 35%,transparent);border-radius:8px;padding:8px}
.cm.top{border-top:0;margin-top:0;padding-top:0} .composer{display:flex;gap:8px;align-items:flex-end;margin:6px 0} .composer textarea{flex:1;margin:0;min-width:0} .composer .pri{height:32px} .prl{display:flex;flex-direction:column;gap:2px;font-size:11px;color:var(--ink)}
.rq-bar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:14px 0 8px}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:6px;overflow:hidden;margin:4px 0 10px}
.seg button{border:0;border-radius:0;border-right:1px solid var(--rule)} .seg button:last-child{border-right:0}
.seg button[aria-pressed=true]{background:var(--accent);color:var(--surface)}
.nt-h{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;font-size:12px;color:var(--muted)} .nt-h b.who{color:var(--ink)}
.nt-t{margin:6px 0;font-size:14px;white-space:pre-wrap} .nt{padding:6px 10px}
.nt>details>summary{display:flex;gap:8px;align-items:center;list-style:none} .nt>details>summary::-webkit-details-marker{display:none}
.nt>details>summary::before{content:'▸';color:var(--muted)} .nt>details[open]>summary::before{content:'▾'}
.nt-s{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13.5px}
.nt>details>summary .dim{font-size:12px;white-space:nowrap} .nt>details[open]>summary .nt-s{font-weight:600;white-space:pre-wrap} .ctl label{display:inline-flex;gap:4px;align-items:center;margin:0 10px 4px 0}
.board h3 .dim{font-size:12px} .empty{color:var(--muted);font-size:12px;padding:4px 6px}
hr.sep{border:0;border-top:1px solid var(--rule);margin:28px 0 0}
.board h3{margin:2px 4px;font-size:13px;font-weight:600}
ol.next li p{margin:0 0 8px}
/* all outputs */
.g h2{font:600 15px var(--sans);letter-spacing:0;border-bottom:1px solid var(--rule);padding-bottom:4px;margin:28px 0 6px}
.row{margin:2px 0 2px 8px} .old{margin-left:18px;color:var(--muted)} #q{width:320px}
@media (max-width:900px){.layout{display:block} .rail{position:static;height:auto;border-right:0;border-bottom:1px solid var(--rule);padding:20px 16px}
 main{padding:20px 16px 60px} .card{padding:16px} .card h2{font-size:20px} .lede p{font-size:17px} .board{grid-template-columns:1fr}
 body.plain{padding:16px} #q{width:100%}}
</style>""".replace("{ICON}", ICON)
# preview loads when a <details data-src> opens, so ~280 PDFs are not fetched up front; a PDF shows as png/<name>.png (page 1)
PREVIEW = """<script>
document.addEventListener('toggle',e=>{const d=e.target,p=d.querySelector&&d.querySelector(':scope>.pv');
 if(!d.open||!p||p.firstChild)return; const x=d.dataset.ext;
 p.innerHTML=['png','svg','jpg'].includes(x)?`<img src="${d.dataset.src}">`:x=='pdf'?`<img src="${d.dataset.src.replace(/(^|\\/)f\\//,'$1png/')}.png">`:
  ['md','csv','tex','txt','log'].includes(x)?
  `<iframe src="${d.dataset.src}"></iframe>`:'<i>no preview; use "open"</i>';},true);
</script>"""


def parse_findings(text):
    """docs/findings.md -> {updated, picture, next, findings: [{title, importance, exhibit, also, tags, evidence, body}]}."""
    m = re.search(r"^updated:\s*(\S+)", text, re.M)
    out = dict(updated=m[1] if m else "", picture="", next="", findings=[])
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, rest = block.partition("\n")
        meta, lines = {}, rest.strip().splitlines()
        while lines and (km := re.match(r"(importance|exhibit|also|evidence|changed|added|type|tags|purpose|does|shows):\s*(.*)", lines[0])):
            meta[km[1]] = km[2].strip()
            lines.pop(0)
        body, head = "\n".join(lines).strip(), head.strip()
        if head in ("Picture", "Next"):
            out[head.lower()] = body
        else:
            for k in ("also", "tags"):
                meta[k] = [x.strip() for x in meta.get(k, "").split(",") if x.strip()]
            fid = re.match(r"^(F\d+)\.", head)
            out["findings"].append(dict(id=fid[1] if fid else head, title=re.sub(r"^F\d+\.\s*", "", head), body=body, **meta))
    return out


def warning(path, ix):
    """Why an exhibit cannot be trusted as current, or '' if it can. ix: 'dir/name' -> (file, newest of its name)."""
    if path not in ix:
        return f"missing: {path}"
    f, top = ix[path]
    if "superseded" in f["flags"]:
        return f"superseded by {top['dir']}/{top['name']}"
    return "; ".join(f"{x}: {LEGEND_D[x]}" for x in f["flags"])


def prose(text):
    """Paragraphs of escaped text; `code`, **bold** and *italic* only."""
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", re.sub(r"`(.+?)`", r"<code>\1</code>", t))
    t = re.sub(r"\*(\S.*?)\*", r"<i>\1</i>", t)
    return "".join(f"<p>{p}</p>" for p in re.split(r"\n\s*\n", t) if p.strip())


PNG = {}  # ponytail: in-memory, unbounded; fine for a few hundred figures, an LRU if it ever is not
# on disk too, so each publish (a new process) renders only new or changed PDFs (10-29)
# ponytail: never pruned; a stale PNG is never read (its key holds the mtime), clear the folder if it grows too big
PNG_DIR = os.path.expanduser(f"~/.cache/{re.sub(r'[^a-z0-9]+', '-', NAME.lower())}-png")


def eq_of(path):
    """The page the dashboard and the room show for a figure: its copy with the equation, <stem>.eq.pdf beside it, or
    for a page drawn inside the enclave figures/eq/<run>/<stem>.eq.pdf (out/<run>/RELEASE/ holds only what left the enclave;
    each run folder its own, so a new version never inherits the old one's equation); else the figure itself."""
    m = re.match(r"out/([^/]+)/RELEASE/(.+)\.pdf$", path)
    for eq in ([path[:-4] + EQ_EXT] if path.endswith(".pdf") else []) + ([f"figures/eq/{m[1]}/{m[2]}{EQ_EXT}"] if m else []):
        if os.path.isfile(os.path.join(ROOT, eq)):
            return eq
    return path


def spec_of(path):
    """A figure's equation and notation as text ({eq, notes}, figure_rules.write_eq) beside its equation page, or None."""
    eq = eq_of(path)
    p = os.path.join(ROOT, eq.removesuffix(EQ_EXT) + EQ_TXT)
    if eq == path or not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def pdf_png(p, plain=False):
    """Page 1 of a PDF as PNG bytes, the equation page unless `plain`; rendered in a temporary folder, so nothing is
    written to the repo."""
    p = os.path.join(ROOT, os.path.relpath(p, ROOT) if plain else eq_of(os.path.relpath(p, ROOT)))
    key = (p, os.path.getmtime(p))
    if key not in PNG:
        disk = os.path.join(PNG_DIR, hashlib.md5(repr(key).encode()).hexdigest() + ".png")
        if not os.path.isfile(disk):
            if not shutil.which("pdftoppm"):  # printed, not passed to sys.exit: a server thread's SystemExit is silent
                print("poppler's pdftoppm is needed to show a PDF figure (apt install poppler-utils / brew install poppler)", file=sys.stderr)
                raise SystemExit(1)
            os.makedirs(PNG_DIR, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=PNG_DIR) as t:  # same disk, so the move is atomic
                subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile", p, os.path.join(t, "x")], check=True)
                os.replace(os.path.join(t, "x.png"), disk)
        with open(disk, "rb") as fh:
            PNG[key] = fh.read()
    return PNG[key]


def img_url(path, plain=False):
    """An exhibit's image URL (page 1 as PNG for a PDF: the equation page, or with `plain` the page itself, at
    <name>.plain.png), or '' if it has none; relative, so it works served and exported."""
    d, _, name = path.partition("/")
    ext = name.rsplit(".", 1)[-1].lower()
    # the shown file's time in the URL: Cloudflare keeps an image 4 hours under its URL whatever the server says
    # (the web room, 10-20), so a redrawn page, or one that gained its equation, gets a new URL
    src = os.path.join(ROOT, path if plain else eq_of(path))
    v = f"?v={int(os.path.getmtime(src))}" if os.path.isfile(src) else ""
    png = ".plain.png" if plain else ".png"
    return f"png/{d}/{quote(name)}{png}{v}" if ext == "pdf" else f"f/{d}/{quote(name)}{v}" if ext in ("png", "svg", "jpg") else ""


def exhibit(path, ix, main=False):
    """A finding's main exhibit is shown as an image; the others fold."""
    d, _, name = path.partition("/")
    kind = "figure" if d == "figures" else "table"
    url, ext = f"f/{d}/{quote(name)}", name.rsplit(".", 1)[-1].lower()  # relative: works served and exported
    w = warning(path, ix)
    warn = f' <span class=warn>{html.escape(w)}</span>' if w else ""
    img = img_url(path)
    if main and img:
        side = os.path.join(ROOT, d, name.rsplit(".", 1)[0] + ".txt")  # figure_rules.save(caption_file=)
        cap = ""
        if os.path.isfile(side):
            with open(side, encoding="utf-8") as fh:
                cap = f'<details class=cap><summary>Caption</summary><p>{html.escape(fh.read().strip())}</p></details>'
        return (f'<p class=ex><img src="{img}" alt="{kind}"></p>{cap}{minicb(variables(path))}'
                f'<p class=dim><a href="{url}" target=_blank>full {ext.upper()}</a>{warn}</p>')
    return (f'<details class=it data-src="{url}" data-ext="{ext}"{" open" if main else ""}><summary>{kind} '
            f'<a href="{url}" target=_blank>open {kind}</a>{warn}</summary><div class=pv></div>{minicb(variables(path))}</details>')


# Requests (stored as notes, keyed by finding) exist only on a hosted copy that serves api/comments (a project's own function); locally the API
# 404s and the boxes and board stay hidden. Claude answers with the project's notes script, which marks a note done.
# the rail marks the finding being read
SPY = """<script>
const L=document.querySelectorAll('.rail a[href^="#f"]'),io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)
 L.forEach(a=>a.classList.toggle('on',a.hash=='#'+e.target.id));}),{rootMargin:'-15% 0px -75% 0px'});
document.querySelectorAll('.card').forEach(c=>io.observe(c));
</script>"""
# newest first by `changed:` (file order breaks ties); the selects keep only findings with that added: date, type: or one of
# their tags:, in the rail too. Importance is hidden for now (10-14); `importance:` lines stay in docs/findings.md.
ARRANGE = """<script>
const pass=c=>['added','type','tag'].every(k=>{const v=document.getElementById('f-'+k).value;
 return !v||(k=='tag'?c.dataset.tags.split('|').includes(v):c.dataset[k]==v);});
function arrange(){const k=c=>c.dataset.date;
 for(const box of [document.getElementById('cards'),document.getElementById('thumbs'),document.querySelector('.rail ol')])
  [...box.children].sort((a,b)=>k(b).localeCompare(k(a))||a.dataset.ord-b.dataset.ord)
   .forEach(c=>{c.hidden=!pass(c);box.append(c);});}
document.addEventListener('change',e=>{if(e.target.closest('.filters'))arrange();});
arrange();
</script>"""
COMMENTS = """<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
const PRI={P1:'#c22',P2:'#c80',P3:'#888'},API='api/comments';let all={},me='',admin=false,view='all',PEOPLE={};const OWN=__OWN__;
const opt=(v,cur)=>v.map(o=>`<option${o==cur?' selected':''}>${o}</option>`).join('');
const nm=w=>esc(PEOPLE[w]||w);
const openNt=new Set();  // requests the viewer expanded, kept open across re-renders
function item(k,i,x){const st=x.status||(x.done?'done':'open'),pr=x.pri||'P2',rs=x.replies||[],id=k+':'+i;
 return `<li class="nt ${st}" data-k="${k}" data-i="${i}"><details data-nt="${id}"${openNt.has(id)?' open':''}>`+
  `<summary><b class=fl style="background:${PRI[pr]}">${pr}</b><span class=nt-s>${esc(x.text)}</span>`+
  `<span class=dim>${k=='note'?'General':'Finding '+k.slice(1)} · ${nm(x.who)}${rs.length?' · '+rs.length+' ↩':''}</span></summary>`+
  `<div class=nt-h>${k=='note'?'':`<a href="#f${k.slice(1)}">Go to finding ${k.slice(1)}</a>`}<span>${esc(x.at).slice(0,10)}</span></div>`+

  `<div class=ctl><label>Status <select data-set=status>${opt(['open','doing','done','archived'],st)}</select></label>`+
  `<label>Priority <select data-set=pri>${opt(['P1','P2','P3'],pr)}</select></label>`+
  (admin?`<label>Owner <select data-set=owner>${OWN.map(o=>`<option value="${o}"${o==(x.owner||'')?' selected':''}>${o||'unassigned'}</option>`).join('')}</select></label>`+
   '<button data-act=del>Delete</button>':'')+`</div>`+
  `<details${rs.length?' open':''}><summary>${rs.length?rs.length+' repl'+(rs.length==1?'y':'ies'):'Reply'}</summary>`+
  rs.map(r=>`<div class=rp><b>${nm(r.who)}</b> <span class=dim>${esc(r.at).slice(0,10)}</span>: ${esc(r.text)}</div>`).join('')+
  `<div class=composer><textarea rows=1 placeholder="Write a reply"></textarea><button class=send data-act=reply title=Reply aria-label=Reply>→</button></div></details></details></li>`;}
document.addEventListener('toggle',e=>{const id=e.target.dataset&&e.target.dataset.nt;if(id)e.target.open?openNt.add(id):openNt.delete(id);},true);
function render(){
 document.querySelectorAll('.cm').forEach(c=>{const k=c.dataset.f;c.hidden=false;
  const u=c.querySelector('ul');if(u)u.innerHTML=(all[k]||[]).map((x,i)=>['archived','deleted'].includes(x.status)?'':item(k,i,x)).join('');});
 const stOf=r=>r[2].status||(r[2].done?'done':'open'),cnt={};
 const rows=[];for(const k in all)all[k].forEach((x,i)=>rows.push([k,i,x]));
 const keep=([k,i,x])=>view=='all'||(view=='mine'&&me&&(x.owner||'')==me)||(view=='P1'&&(x.pri||'P2')=='P1');
 const by=(a,b)=>(a[2].pri||'P2').localeCompare(b[2].pri||'P2')||b[2].at.localeCompare(a[2].at);
 for(const st of ['open','doing','done']){const rs=rows.filter(r=>stOf(r)==st&&keep(r)).sort(by);cnt[st]=rows.filter(r=>stOf(r)==st).length;
  document.getElementById('col-'+st).innerHTML=rs.map(r=>item(...r)).join('')||'<li class=empty>None</li>';
  document.getElementById('n-'+st).textContent=rs.length;}
 document.getElementById('tcount').textContent=`${cnt.open} open · ${cnt.doing} doing · ${cnt.done} done`;
 document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.view==view));
 document.querySelectorAll('.add').forEach(b=>b.hidden=false);document.getElementById('requests').hidden=false;}
// a failed save says why next to the control that sent it; an expired Access session shows up as a failed fetch
async function post(body,el){let e=el.nextElementSibling;if(e&&e.classList.contains('err'))e.remove();
 const say=m=>{el.insertAdjacentHTML('afterend','<span class=err role=alert></span>');el.nextElementSibling.textContent=m;};
 let r;try{r=await fetch(API,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});}
 catch(x){return say('Not saved: no answer from the server. If the page has been open a while, reload it to sign in again.');}
 if(!r.ok)return say(r.status==401?'Not saved: you are signed out. Reload the page to sign in, then try again.':
  `Not saved (${r.status} ${(await r.text()).slice(0,80)}). Try again.`);
 all[body.f]=await r.json();render();return true;}
document.addEventListener('change',e=>{const s=e.target.dataset.set,li=e.target.closest('.nt');if(!s||!li)return;
 post({f:li.dataset.k,i:+li.dataset.i,set:{[s]:e.target.value}},e.target);});
document.addEventListener('click',e=>{const b=e.target;
 if(b.dataset.act=='add'){const n=document.getElementById(b.dataset.t);n.hidden=!n.hidden;if(!n.hidden)n.querySelector('textarea').focus();}
 if(b.dataset.act=='del'){const li=b.closest('.nt');if(b.textContent!='Sure?'){b.textContent='Sure?';return;}post({f:li.dataset.k,i:+li.dataset.i,del:true},b);}
 if(b.dataset.act=='reply'){const li=b.closest('.nt'),t=li.querySelector('textarea');if(t.value.trim())post({f:li.dataset.k,i:+li.dataset.i,reply:t.value},b);}
 if(b.id=='rq-toggle'){const d=document.getElementById('rq-board');d.hidden=!d.hidden;b.setAttribute('aria-expanded',!d.hidden);
  b.textContent=d.hidden?'Show all requests':'Hide requests';}
 if(b.dataset.act=='post'){const c=b.closest('.cm'),t=c.querySelector('.composer textarea');if(t.value.trim())post({f:c.dataset.f,text:t.value,pri:c.querySelector('.pri').value},b).then(ok=>{if(ok)t.value='';});}
 if(b.dataset.view){view=b.dataset.view;render();}});
(async()=>{let r;try{r=await fetch(API+'?all=1')}catch(e){return}if(!r.ok)return;const d=await r.json();all=d.notes;me=d.name;admin=d.admin;PEOPLE=d.people||{};render();})();
</script>""".replace("__OWN__", json.dumps([""] + PEOPLE))


def bullets(x):
    """Purpose, what it does, what it shows: one bullet each, from the finding's purpose:, does: and shows: lines."""
    li = "".join(f"<li><b>{k}:</b> {prose(x[f])[3:-4]}</li>" for k, f in
                 (("Purpose", "purpose"), ("What it does", "does"), ("What it shows", "shows")) if x.get(f))
    return f"<ul class=pws>{li}</ul>" if li else ""


def note_box(key, hint, listed=True):
    """A request box: the requests already sent about `key` (unless listed=False) and a box to send a new one."""
    return (f'<div class="cm{"" if listed else " top"}" data-f="{html.escape(key)}" hidden>' + ('<b>Requests</b><ul></ul>' if listed else "")
            + f'<div class=composer><textarea rows=2 placeholder="{html.escape(hint)}"></textarea>'
            '<label class=prl>Priority<select class=pri><option>P1</option><option selected>P2</option><option>P3</option></select></label>'
            '<button class=send data-act=post title=Send aria-label=Send>→</button></div></div>')


def overview(static=False):
    if not os.path.exists(FINDINGS):
        return (f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(NAME)}</title>{STYLE}</head><body class=plain>"
                '<p>No docs/findings.md yet. <a href="/all">All outputs</a></p></body></html>')
    with open(FINDINGS, encoding="utf-8") as fh:
        F = parse_findings(fh.read())
    groups, unmapped, _ = scan()
    ix = {f"{f['dir']}/{f['name']}": (f, files[0]) for keys in groups.values() for files in keys.values() for f in files}
    ix.update({f"{f['dir']}/{f['name']}": (f, f) for f in unmapped})
    try:
        since = time.mktime(time.strptime(F["updated"], "%Y-%m-%d")) + 86400
        n_new = sum(f["mtime"] > since for f, _ in ix.values())
        fresh = f"{n_new} outputs were written after that date." if n_new else "No output is newer than that."
    except ValueError:
        fresh = "The file has no valid updated: date."
    if static:
        fresh = ""  # the hosted copy has no outputs folder to compare with
    cards, chips, thumbs = [], [], []
    for i, x in enumerate(F["findings"], 1):
        ch = x.get("changed", "")
        n = x["id"][1:] if re.fullmatch(r"F\d+", x["id"]) else str(i)  # anchor by F-number: notes and links survive re-sorting
        data = (f'data-date="{html.escape(ch)}" data-ord={i} '
                f'data-added="{html.escape(x.get("added", ""))}" data-type="{html.escape(x.get("type", ""))}" '
                f'data-tags="{html.escape("|".join(x["tags"]))}"')
        try:
            new = time.time() - time.mktime(time.strptime(ch, "%Y-%m-%d")) < 14 * 86400
        except ValueError:
            new = False
        badge = ' <b class="fl new">new</b>' if new else ""
        chips.append(f'<li {data}><a href="#f{n}"><span>{n}. {html.escape(x["title"])}{badge}</span></a></li>')
        if (im := img_url(x.get("exhibit", ""))):
            thumbs.append(f'<a class=th href="#f{n}" {data} title="{html.escape(x["title"])}"><img src="{im}" alt="" loading=lazy>'
                          f'<span>Finding {n}</span></a>')
        also = "".join(exhibit(p, ix) for p in x["also"])
        cards.append(
            f'<article class=card id=f{n} {data}><div class=kick><span>Finding {n}</span>'
            + (f'<span>changed {html.escape(ch)}</span>' if ch else "") + f'{badge}</div><h2>{html.escape(x["title"])} <button class=add data-act=add data-t=nb-{x["id"]} hidden title="New request" '
            f'aria-label="New request">+</button></h2>'
            + f'<div id=nb-{x["id"]} hidden>' + note_box(x["id"], "Send a request about this finding") + '</div>'
            + (exhibit(x["exhibit"], ix, True) if x.get("exhibit") else "")
            + bullets(x)
            + prose(x["body"]) + (f'<details><summary>{len(x["also"])} more exhibit(s)</summary>{also}</details>' if also else "")
            + (f'<p class=dim>Evidence: {html.escape(x["evidence"])}</p>' if x.get("evidence") else "") + "</article>")
    nxt = "".join("<li>" + prose(re.sub(r"^\d+\.\s*", "", l)) + "</li>" for l in F["next"].splitlines() if l.strip())
    def pick(k, label, vals):  # a filter on the finding's added:, type: or tags: line
        return (f'<label>{label} <select id=f-{k}><option value="">All</option>'
                + "".join(f"<option>{html.escape(v)}</option>" for v in vals) + "</select></label>")
    fs = F["findings"]
    filters = (pick("added", "Added", sorted({x.get("added", "") for x in fs} - {""}, reverse=True))
               + pick("type", "Type", sorted({x.get("type", "") for x in fs} - {""}))
               + pick("tag", "Tag", sorted({t for x in fs for t in x["tags"]})))
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{html.escape(NAME)}</title>{STYLE}</head><body>
<div class=layout><aside class=rail><h1>{html.escape(TITLE)}</h1><p class=sub>Findings so far</p>
<p><a class=go href="/room">Control room</a></p><p><a class=go href="/room?group=read">Figures by read date</a></p>
<p class=dim>Last updated {html.escape(F["updated"])}. {fresh}</p>
<div class="key filters">{filters}</div><nav><ol>{"".join(chips)}</ol></nav>{"" if static else '<p><a href="/all">All outputs</a></p>'}</aside>
<main>
<section id=requests hidden><h2>Requests</h2>
{note_box("note", "Write a request", listed=False)}
<div class=rq-bar><button id=rq-toggle aria-expanded=false aria-controls=rq-board>Show all requests</button><span id=tcount class=dim></span></div>
<div id=rq-board hidden><div class=seg role=group aria-label="Show"><button data-view=all>All</button><button data-view=mine>Mine</button><button data-view=P1>P1 only</button></div>
<div class=board><div><h3>Open <span id=n-open class=dim></span></h3><ul id=col-open></ul></div><div><h3>Doing <span id=n-doing class=dim></span></h3><ul id=col-doing></ul></div>
<div><h3>Done <span id=n-done class=dim></span></h3><ul id=col-done></ul></div></div></div><hr class=sep></section>
<div class=thumbs id=thumbs>{"".join(thumbs)}</div>
<h2>Findings</h2><div id=cards>{"".join(cards)}</div>
<h2>Where next</h2><ol class=next>{nxt}</ol></main></div>{PREVIEW}{COMMENTS}{SPY}{ARRANGE}</body></html>"""


def room_page():
    """The room's page, its app.css and app.js links carrying each file's hash: a tab opened before a change cannot
    keep the old stylesheet (seen once, 10-15), nor Cloudflare's 4-hour copy of the old script on the web room,
    which hid the Takeaways button there (10-20). Served by do_GET and written by export()."""
    with open(os.path.join(APP, "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    for f in ("app.css", "app.js"):
        with open(os.path.join(APP, f), "rb") as fh:
            page = page.replace(f'"app/{f}"', f'"app/{f}?v={hashlib.md5(fh.read()).hexdigest()[:8]}"')
    return page.replace("__NAME__", html.escape(NAME))


def export(out):
    """The room as static files under out/: index.html and room.html (the same page), app/, index.json (hosted()),
    f/<dir>/<file> for each exhibit and each plot's newest files and captions, png/<dir>/<file>.png."""
    F = parse_findings(findings_text())
    paths = {p for x in F["findings"] for p in ([x["exhibit"]] if x.get("exhibit") else []) + x["also"]}
    full = index_now()
    ix = hosted(full, born=added())
    for p in ix["plots"].values():
        for f in p["files"].values():
            paths.add(f["path"])
            if f["caption_txt"]:
                paths.add(f["path"].rsplit(".", 1)[0] + ".txt")
    os.makedirs(os.path.join(out, "app"), exist_ok=True)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:  # the room is the front page; the overview is
        fh.write(room_page())  # retired on the hosted copy (11-08); room.html stays so /room links keep working
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(ix, fh, ensure_ascii=False)
    # room.html, not room/index.html: Pages serves it at /room, where its relative app/ paths resolve to /app/
    with open(os.path.join(out, "room.html"), "w", encoding="utf-8") as fh:
        fh.write(room_page())
    for f in ("app.js", "app.css"):
        shutil.copy2(os.path.join(APP, f), os.path.join(out, "app"))
    for f in ("favicon.ico", "apple-touch-icon.png"):
        shutil.copy2(icon(f), out)
    for path in sorted(paths):
        src = os.path.join(ROOT, path)
        if not os.path.isfile(src):
            print(f"missing, not exported: {path}", file=sys.stderr)
            continue
        os.makedirs(os.path.join(out, "f", os.path.dirname(path)), exist_ok=True)
        shutil.copy2(src, os.path.join(out, "f", path))
        if path.endswith(".pdf"):  # the page the index's img names: plain when its equation is text (spec_of)
            os.makedirs(os.path.join(out, "png", os.path.dirname(path)), exist_ok=True)
            plain = spec_of(path) is not None
            with open(os.path.join(out, "png", path + (".plain.png" if plain else ".png")), "wb") as fh:
                fh.write(pdf_png(src, plain))
    rough = sorted(pid for pid, p in ix["plots"].items()  # a file or column name, or a title that is only the file name
                   if INTERNAL.search(p["title"]) or p["title"] == full["plots"][pid].get("name"))
    undef = sorted({n for p in ix["plots"].values() for f in p["files"].values() for n in variables(f["path"], undefined=True)})
    if undef:  # each wants a row in the codebook of the CSV it comes from, or in the sibling codebooks
        print(f"{len(undef)} variable names are shown without a definition: " + ", ".join(undef), file=sys.stderr)
    nohelp = sorted({f"{p['nn']} {a}" for p in full["plots"].values() for a, _ in p["axes"] if a not in p["help"]})
    if nohelp:  # each wants a row in .room/switches.tsv (the room's ⓘ beside the switch)
        print(f"{len(nohelp)} switches have no help: " + ", ".join(nohelp), file=sys.stderr)
    inner = sorted({a["key"] for p in ix["plots"].values() for a in [p.get("about")] + [f.get("about") for f in p["files"].values()]
                    if a and INTERNAL.search(" ".join(a[k] for k in ("purpose", "how", "shows")))})
    if inner:  # the hosted sentences name things by meaning (memory: internal names)
        print(f"{len(inner)} approved Purpose / How / What it shows rows show a file or code name (.room/about.tsv):\n  "
              + "\n  ".join(inner), file=sys.stderr)
    said = sorted(p["whatis"]["key"] for p in ix["plots"].values() if p.get("whatis") and INTERNAL.search(p["whatis"]["text"] + " " + p["whatis"]["answer"]))
    if said:
        print(f"{len(said)} What is this? lines show a file or code name (.room/whatis.tsv):\n  " + "\n  ".join(said), file=sys.stderr)
    lost = sorted({r[k] for r in ix["story"] for k in ("from", "to") if not r[k + "_ok"] and not r[k].startswith("(none")})
    if lost:
        print(f"{len(lost)} Graph D plots are not on the hosted copy (drawn by their stored title):\n  " + "\n  ".join(lost), file=sys.stderr)
    told = [r["id"] for r in ix["story"] if INTERNAL.search(" ".join(r[k] for k in ("question", "answer", "from_title", "to_title")))]
    if told:
        print(f"{len(told)} Graph D rows show a file or code name (.room/story.tsv): {', '.join(told)}", file=sys.stderr)
    tk = sorted(f"{d} point {x['n']}" for d, xs in ix.get("takeaways", {}).items() for x in xs if INTERNAL.search(x["t"] + " " + x["body"]))
    if tk:  # collaborators read them now (10-20)
        print(f"{len(tk)} takeaways show a script, file or version name (docs/runs/takeaways_*.md):\n  " + "\n  ".join(tk), file=sys.stderr)
    raw = sorted(pid for pid in ix["plots"] if full["plots"][pid].get("unnamed"))
    if raw:  # a chip that shows a file-name token: a row for it in .room/options.tsv (--labels-todo lists them)
        print(f"{len(raw)} plots show a chip named by a file-name token (.room/options.tsv):\n  " + "\n  ".join(raw), file=sys.stderr)
    if rough:  # a name for each goes into .room/titles.tsv
        print(f"{len(rough)} titles still show a file or column name (add them to .room/titles.tsv):\n  " + "\n  ".join(rough), file=sys.stderr)
    # every regression figure shows its equation (10-20): the newest sitting's forests and event studies without one
    last = max((p["read"] for p in full["plots"].values()), default="")
    noeq = sorted(f["path"] for p in full["plots"].values() if p["read"] == last and p.get("ptype") in ("forest", "event study")
                  for f in p["files"].values() if f["path"].endswith(".pdf") and "superseded" not in f["flags"] and eq_of(f["path"]) == f["path"])
    if noeq:  # figure_rules.save(equation=), or for a page drawn in the enclave a row in code/local/85_enclave_equations_v1.py
        print(f"{len(noeq)} regression figures read {last} show no equation:\n  " + "\n  ".join(noeq), file=sys.stderr)
    if lost := scan()[1]:  # never silent: these are listed at /all only (review 11-13, F8)
        names_ = ", ".join(sorted(f"{f['dir']}/{f['name']}" for f in lost)[:8]) + (", …" if len(lost) > 8 else "")
        print(f"{len(lost)} output{' is' if len(lost) == 1 else 's are'} not in the room: the name is not N_name_vK.ext, or several "
              f"scripts carry the number and none matches its first word ({names_})", file=sys.stderr)
    print(f"exported {len(paths)} files ({len(ix['plots'])} plots) to {out}")


def row(f, label=None):
    e, url = html.escape, f"/f/{f['dir']}/{quote(f['name'])}"
    flags = "".join(f'<b class="fl {x}">{x}</b>' for x in f["flags"])
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"]))
    return (f'<details class=it data-src="{url}" data-ext="{f["name"].rsplit(".", 1)[-1].lower()}"><summary>'
            f'<code>{f["dir"]}/{e(label or f["name"])}</code> {flags} <span class=dim>{when}</span> '
            f'<a href="{url}" target=_blank>open</a></summary><div class=pv></div></details>')


def page():
    groups, unmapped, fns = scan()
    n_latest = n_flag = 0
    counts = defaultdict(int)
    body = []
    for (nn, fn) in sorted(groups):
        vers = fns.get(nn, {}).get(fn)  # None: no script owns these outputs
        src = f"newest script code/local/{nn}_{html.escape(fn)}_v{max(vers)}.py" if vers else "no script"
        rows = []
        for (d, key), files in sorted(groups[(nn, fn)].items()):
            latest = [f for f in files if "superseded" not in f["flags"]]
            old = [f for f in files if "superseded" in f["flags"]]
            n_latest += len(latest)
            for f in files:
                for x in f["flags"]:
                    counts[x] += 1
            n_flag += any(f["flags"] for f in latest)
            olds = (f'<details class=old><summary>{len(old)} older version(s)</summary>'
                    + "".join(row(f) for f in old) + "</details>") if old else ""
            q = " ".join(f["name"] + " " + " ".join(f["flags"]) for f in files)
            fl = " ".join(x for f in latest for x in f["flags"])
            rows.append(f'<div class=row data-f="{fl}" data-q="{html.escape(q.lower())}">'
                        + "".join(row(f) for f in latest) + olds + "</div>")
        body.append(f'<section class=g><h2>{nn} {html.escape(fn)} <span class=dim>{src}; {len(rows)} names</span></h2>'
                    f'{"".join(rows)}</section>')
    if unmapped:
        body.append('<section class=g><h2>unmapped <span class=dim>no NN_..._vK name, or no single code/local '
                    'script with that NN; not guessed</span></h2>' + "".join(
                        f'<div class=row data-q="{html.escape(f["name"].lower())}">{row(f)}</div>'
                        for f in sorted(unmapped, key=lambda f: f["name"])) + "</section>")
    summary = (f"{len(groups)} script groups, {n_latest} latest outputs, {n_flag} names with a flag on the latest "
               f"version (behind {counts['behind']}, older {counts['older']}), {counts['superseded']} superseded "
               f"files, {len(unmapped)} unmapped. Read {time.strftime('%Y-%m-%d %H:%M:%S')}.")
    legend = "".join(f'<li><b class="fl {k}">{k}</b> {html.escape(v)}</li>' for k, v in LEGEND)
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{html.escape(NAME)} outputs</title>{STYLE}</head><body class=plain>
<p><a href="/">Overview of the findings</a></p><h1>{html.escape(NAME)} outputs</h1><p>{summary}</p><ul>{legend}</ul>
<p><input id=q placeholder="filter by name or flag" autofocus> <label><input type=checkbox id=fo> flagged only</label>
<span class=dim>Click a name to preview it; "open" shows the file alone.</span></p>{"".join(body)}
<script>
const q=document.getElementById('q'),fo=document.getElementById('fo');
function filt(){{const s=q.value.toLowerCase();
 document.querySelectorAll('.row').forEach(r=>{{const t=r.dataset.q;
  r.hidden=!(t.includes(s)&&(!fo.checked||r.dataset.f));}});
 document.querySelectorAll('.g').forEach(g=>g.hidden=!g.querySelector('.row:not([hidden])'));}}
q.oninput=fo.onchange=filt;
</script>{PREVIEW}</body></html>"""


HUB, TOKEN, PORT = None, "", 8765  # set by --claude: the agent hub (room/agent.py) and the launch token
WRITE = threading.Lock()  # The owner's clicks rewrite a whole TSV; the server answers requests on threads
# --public HOST (plan docs/plans/2026-10-20-claude-tunnel.md): the room through a Cloudflare tunnel on a server, where
# Access's signed login stands in for the launch token; AUD is that Access app's tag (env ACCESS_AUD)
PUBLIC, AUD, KEYS = "", "", None
HOSTED = CFG.get("hosted", {})  # [hosted] emails: who may use the web room; access_team: whose keys sign the login
ROOM_EMAILS = {str(e).lower() for e in HOSTED.get("emails", [])}
CERTS = f"https://{HOSTED.get('access_team', '')}.cloudflareaccess.com/cdn-cgi/access/certs"


def guard_verdict(code, redirect, preview, team=""):
    """Is this answer from a host safe to deploy behind (design 2026-11-13 §4)? A listed host must redirect (302) to
    the Access login of `team` ("" = any team). A preview host may also answer 404: where the Access app does not
    cover <anything>.<project>.pages.dev, check.<host> is an alias no deployment has, and Pages answers its own 404.
    That 404 shows only that nothing is served there; whether the middleware closes a real deployment's host is
    checked after the deploy (deployed_verdict; review 11-13, F14)."""
    host = urlsplit(redirect).hostname or ""
    login = host == f"{team}.cloudflareaccess.com" if team else host.endswith(".cloudflareaccess.com")
    return (code == 302 and login) or (preview and code == 404)


def curl_probe(host, body=False):
    """(status code, redirect URL) of https://host/, without following the redirect, and the page's text too when
    `body`; code 0 when curl fails."""
    r = subprocess.run(["curl", "-s", "-m", "20", *([] if body else ["-o", "/dev/null"]), "-w", "\n%{http_code} %{redirect_url}",
                        f"https://{host}/"], capture_output=True, text=True)
    text, _, last = r.stdout.rpartition("\n")
    code, _, to = last.partition(" ")
    out = (int(code) if code.isdigit() else 0), to.strip()
    return (*out, text) if body else out


def deployed_verdict(code, redirect, body, team=""):
    """Is a deployment's own host (<hash>.<project>.pages.dev) closed? Either Access covers it (the 302 to the login) or
    the middleware refuses it: a 404 whose whole text is "not found". Pages' own 404 page ("Deployment Not Found",
    served until a deployment is live, and for any alias no deployment has) says nothing about the middleware."""
    return guard_verdict(code, redirect, False, team) or (code == 404 and body.strip() == "not found")


def deploy_host(text):
    """The deployment's own host in wrangler's output ("… Take a peek over at https://<hash>.<project>.pages.dev"), else ""."""
    m = re.search(r"https://([0-9a-z-]+\.[0-9a-z-]+\.pages\.dev)", text)
    return m[1] if m else ""


def access_guard(hosts, team="", probe=curl_probe):
    """One refusal line per host that Access no longer guards, [] when a deploy is safe. Each host is probed as listed;
    a *.pages.dev host is also probed at check.<host>, standing for the preview hosts every deploy creates."""
    bad = []
    for h in hosts:
        for host, preview in [(h, False)] + [("check." + h, True)] * h.endswith(".pages.dev"):
            code, to = probe(host)
            if not guard_verdict(code, to, preview, team):
                bad.append(f"refusing to deploy: https://{host}/ answers '{f'{code} {to}'.strip()}', not the Access login"
                           + (" or the middleware's 404" if preview else ""))
    return bad


def site_files(hosted):
    """{path: text} of the two files a deploy needs beside dist/, both written from [hosted]: wrangler.toml (the Pages
    project) and functions/_middleware.js (the hosts it serves), so the two places that must agree cannot disagree
    (docs/lessons.md, 10-13). No KV binding and no other function: api/comments is not deployed (11-13)."""
    with open(os.path.join(os.path.dirname(APP), "_middleware.js"), encoding="utf-8") as fh:
        middleware = fh.read().replace("__HOSTS__", json.dumps(hosted["hosts"]))
    return {"wrangler.toml": f'name = "{hosted["project"]}"\npages_build_output_dir = "dist"\ncompatibility_date = "2026-10-01"\n',
            "functions/_middleware.js": middleware}


def publish():
    """Deploy the hosted copy to Cloudflare Pages (design 2026-11-13 §4): refuse without [hosted], or unless Access
    still guards every host; then write .room/site/ afresh (site_files, and the export as dist/) and run wrangler
    there, through [hosted] env_file when that file exists (a token and account, for a server with no wrangler login). Returns the exit code."""
    h = CFG.get("hosted") or {}
    if not (h.get("project") and h.get("hosts")):
        print("no [hosted] section with project and hosts in .room/config.toml: nothing to publish to", file=sys.stderr)
        return 1
    if bad := access_guard(h["hosts"], h.get("access_team", "")):
        print(*bad, sep="\n", file=sys.stderr)
        return 1
    site = os.path.join(DATA, "site")
    shutil.rmtree(site, ignore_errors=True)
    for rel, text in site_files(h).items():
        os.makedirs(os.path.dirname(os.path.join(site, rel)), exist_ok=True)
        with open(os.path.join(site, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    export(os.path.join(site, "dist"))
    cmd = ["npx", "--yes", "wrangler", "pages", "deploy", "dist", "--project-name", h["project"], "--branch", "main", "--commit-dirty=true"]
    env = os.path.expanduser(h.get("env_file", ""))
    if env and os.path.exists(env):  # bash reads it (exports, comments)
        cmd = ["bash", "-c", '. "$0" && exec "$@"', env, *cmd]
    r = subprocess.run(cmd, cwd=site, stdout=subprocess.PIPE, text=True)
    print(r.stdout, end="", flush=True)
    if r.returncode:
        return r.returncode
    # every deployment also gets its own host, which only the middleware (or a wildcard in the Access app) closes
    if not (host := deploy_host(r.stdout)):
        print("deployed, but wrangler named no deployment host to check", file=sys.stderr)
        return 1
    for _ in range(12):  # Pages answers its own 404 page until the deployment is live
        code, to, body = curl_probe(host, body=True)
        if code != 404 or body.strip() == "not found":
            break
        time.sleep(5)
    if not deployed_verdict(code, to, body, h.get("access_team", "")):
        print(f"DEPLOYED BUT NOT CLOSED: https://{host}/ answers '{f"{code} {to}".strip()}', neither the Access login nor the "
              "middleware's refusal. Roll the deployment back in Cloudflare Pages.", file=sys.stderr)
        return 1
    return 0


def access_ok(tok, key=None):
    """A Cf-Access-Jwt-Assertion -> True when the team's keys signed it for this app, it has not expired, and it is
    the owner's (pyjwt; the key client caches the keys and fetches them again on an unknown key id)."""
    import jwt
    global KEYS
    try:
        if key is None:
            KEYS = KEYS or jwt.PyJWKClient(CERTS)
            key = KEYS.get_signing_key_from_jwt(tok).key
        c = jwt.decode(tok, key, algorithms=["RS256"], audience=AUD, options={"require": ["exp", "aud"]})
    except Exception:
        return False
    return bool(AUD) and str(c.get("email", "")).lower() in ROOM_EMAILS


def allowed(h, post=False, key=None):
    """Under --claude: may a request with headers h use the room? This machine's own page: its Host, its Origin on a
    POST, the token cookie. Through the tunnel (--public): the tunnel's Host, its Origin on a POST, the owner's Access login."""
    host, origin, ok = h.get("Host"), h.get("Origin", ""), {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    if host in ok:
        if post and origin.split("://")[-1] not in ok:
            return False
        jar = dict(c.strip().split("=", 1) for c in h.get("Cookie", "").split(";") if "=" in c)
        return secrets.compare_digest(jar.get("cr", ""), TOKEN)
    return bool(PUBLIC) and host == PUBLIC and (not post or origin == f"https://{PUBLIC}") \
        and access_ok(h.get("Cf-Access-Jwt-Assertion", ""), key)


class Handler(BaseHTTPRequestHandler):  # GET, plus POST for the Claude panel under --claude
    def send(self, code, body, ctype, cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if ctype.startswith(("text/html", "text/css", "text/javascript", "application/javascript", "application/json")):
            self.send_header("Cache-Control", "no-cache")  # an edited room (app.js) shows on the next load
        self.end_headers()
        self.wfile.write(body)

    def local(self, post=False):
        return allowed(self.headers, post)

    def api_get(self, path):
        if path == "/api/hello":
            return self.send(200, b'{"ok":true}', "application/json")
        if path == "/api/threads":
            return self.send(200, json.dumps(dict(cards=HUB.cards, status=HUB.status)).encode(), "application/json")
        if path == "/api/events":  # server-sent events: every card change and status change, until the page goes away
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sub = HUB.subscribe()
            try:
                while True:
                    try:
                        ev = sub.get(timeout=15)
                        self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                HUB.unsubscribe(sub)
            return
        self.send(404, b"not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        if HUB is None or not path.startswith("/api/") or not self.local(post=True):
            return self.send(403, b"forbidden", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            return self.send(413, b"too large", "text/plain")
        d = json.loads(self.rfile.read(n) or b"{}")
        if path == "/api/ask" and d.get("plot") and d.get("text"):
            out = dict(id=HUB.ask(d["plot"], d.get("parent") or "plot", d["text"], bool(d.get("now")), d.get("ctx") or {}))
        elif path == "/api/approve" and d.get("id"):
            out = dict(ok=HUB.answer(d["id"], d.get("choice")))
        elif path == "/api/archive" and isinstance(d.get("ids"), list):
            with WRITE:
                out = dict(ok=archive(d["ids"], bool(d.get("on"))))
        elif path == "/api/importance" and isinstance(d.get("ids"), list):
            with WRITE:
                out = dict(ok=mark(d["ids"], d.get("level") or ""))
        elif path == "/api/share" and d.get("plot") and d.get("id"):
            out = dict(ok=HUB.share(d["plot"], d["id"], d.get("on")))
        else:
            return self.send(400, b"bad request", "text/plain")
        self.send(200, json.dumps(out).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        # the two icons answer anyone, as Access's Bypass app lets them through with no login: a bookmark's icon fetch
        # carries none (docs/lessons.md, 10-14), so on the web room they come before the login check
        if path in ("/favicon.ico", "/apple-touch-icon.png"):
            with open(icon(path[1:]), "rb") as fh:
                return self.send(200, fh.read(), mimetypes.guess_type(path)[0] or "image/x-icon")
        if HUB is not None:
            if path == "/room" and secrets.compare_digest(parse_qs(urlsplit(self.path).query).get("token", [""])[0], TOKEN):
                # the launch URL: hand the token over as a cookie
                return self.send(200, room_page().encode(), "text/html", cookie=f"cr={TOKEN}; HttpOnly; SameSite=Strict; Path=/")
            if path.startswith("/api/"):
                return self.api_get(path) if self.local() else self.send(403, b"forbidden", "text/plain")
            if self.headers.get("Host") not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"} and not self.local():
                return self.send(403, b"forbidden", "text/plain")
        if path in ("/", "/all"):
            return self.send(200, (overview() if path == "/" else page()).encode(), "text/html; charset=utf-8")
        if path == "/index.json":
            return self.send(200, json.dumps(index_now(), ensure_ascii=False).encode(), "application/json")
        if path == "/room":
            return self.send(200, room_page().encode(), "text/html")
        if path.startswith("/app/"):
            name = unquote(path[5:])
            if name in os.listdir(APP):  # only files listed in room/site/app/, so no path tricks
                with open(os.path.join(APP, name), "rb") as fh:
                    return self.send(200, fh.read(), mimetypes.guess_type(name)[0] or "application/octet-stream")
        plain = path.startswith("/png/") and path.endswith(".pdf.plain.png")  # img_url(plain=True): no equation
        png = (".plain.png" if plain else ".png") if path.startswith("/png/") else ""
        rest = unquote(path.split("/", 2)[2]) if path.startswith(("/f/out/", "/png/out/")) else ""
        if rest and ENCLAVE_FIG.match(rest.removesuffix(png)):
            p = os.path.join(ROOT, rest.removesuffix(png))
            if os.path.isfile(p):  # an enclave page (enclave()): the pattern allows one run folder and one file name
                return self.send(200, pdf_png(p, plain), "image/png") if png and p.endswith(".pdf") else \
                    self.send(200, open(p, "rb").read(), mimetypes.guess_type(p)[0] or "application/octet-stream")
        parts = path.split("/")  # /f|png/<dir>/<name>: dir must be one of DIRS, name a listed file in it
        if len(parts) == 4 and parts[1] in ("f", "png") and parts[2] in DIRS:
            d, name = parts[2], unquote(parts[3])
            if parts[1] == "png" and name.endswith(".pdf" + png):
                name = name.removesuffix(png)
                p = os.path.join(ROOT, d, name)
                if name in ls(d) and os.path.isfile(p):
                    return self.send(200, pdf_png(p, plain), "image/png")
            p = os.path.join(ROOT, d, name)
            if not name.startswith(".") and name in ls(d) and os.path.isfile(p):
                if parts[1] == "png":  # png/<dir>/<x.pdf>.png
                    return self.send(404, b"not found", "text/plain")
                ext = name.rsplit(".", 1)[-1].lower()
                ctype = "text/plain; charset=utf-8" if ext in TEXT else (
                    mimetypes.guess_type(name)[0] or "application/octet-stream")
                with open(p, "rb") as fh:
                    return self.send(200, fh.read(), ctype)
        self.send(404, b"not found", "text/plain")


def selftest():
    assert OUT.match("101_reserve_v1.pdf")["nn"] == "101" and RUN.match("100_feasibility_probes_v1")["nn"] == "100"
    assert pretty("101_reserve") == "reserve" and not OUT.match("2017_x_v1.pdf") and INTERNAL.search("100_feasibility")
    assert SITTING.match("38b") and SITTING.match("36th") and not SITTING.match("feasibility")
    G3, U3, _ = build([("figures", "101_reserve_x_v1.pdf", 5), ("figures", "101_exit_y_v1.pdf", 5), ("figures", "101_other_z_v1.pdf", 5),
                       ("figures", "77_alone_v1.png", 5)], [("101_reserve_v1.py", 5), ("101_exit_referral_v1.py", 5)])
    # 3 digits split by first word; no script carries 77, so it is a plot of its own; under 101 an output no script owns is unmapped
    assert set(G3) == {("101", "reserve"), ("101", "exit_referral"), ("77", "alone")} and [f["name"] for f in U3] == ["101_other_z_v1.pdf"], (set(G3), U3)
    G0, U0, _ = build([("figures", "77_alone_v1.png", 5), ("figures", "77_alone_v2.png", 5), ("figures", "notes.png", 5)], [])  # a project with no scripts
    assert [f["flags"] for f in G0[("77", "alone")][("figures", "77_alone.png")]] == [[], ["superseded"]] and len(U0) == 1, (G0, U0)
    L = "https://acme.cloudflareaccess.com/cdn-cgi/access/login/x"  # the guard, with the answers the six hosts gave on 11-13
    assert guard_verdict(302, L, False) and guard_verdict(302, L, True) and guard_verdict(404, "", True)
    assert not guard_verdict(404, "", False) and not guard_verdict(200, "", True) and not guard_verdict(302, "https://example.org/", False)
    assert guard_verdict(302, L, False, team="acme") and not guard_verdict(302, L, False, team="other")
    assert not guard_verdict(302, "https://example.org/?to=x.cloudflareaccess.com/", False) and not guard_verdict(302, L, False, team="me")
    seen = {"a.example.org": (302, L), "p.pages.dev": (200, ""), "check.p.pages.dev": (404, "")}
    bad = access_guard(["a.example.org", "p.pages.dev"], probe=seen.__getitem__)
    assert len(bad) == 1 and "https://p.pages.dev/" in bad[0], bad
    # a fresh deployment's own host: closed by Access or by the middleware's "not found"; Pages' own 404 page is neither
    assert deployed_verdict(404, "", "not found\n") and deployed_verdict(302, L, "")
    assert not deployed_verdict(404, "", "<title>Deployment Not Found</title>") and not deployed_verdict(200, "", "<html>")
    assert deploy_host("Take a peek over at https://116792eb.my-site-t8p.pages.dev\n") == "116792eb.my-site-t8p.pages.dev" and deploy_host("no address") == ""
    SF = site_files({"project": "p", "hosts": ["a.example.org", "p.pages.dev"]})  # the publish folder, from [hosted] alone
    assert 'new Set(["a.example.org", "p.pages.dev"])' in SF["functions/_middleware.js"] and "__HOSTS__" not in SF["functions/_middleware.js"]
    assert SF["wrangler.toml"].splitlines()[0] == 'name = "p"' and "kv_namespaces" not in SF["wrangler.toml"]
    assert [next((n for n, r in PTYPES if re.search(r, x)), "other") for x in
            ("each point is a bin of 10 firms", "forest: point estimates", "employment growth against the payroll share",
             "year effects, 2012-2024", "amendment 73")] == ["binscatter", "forest", "scatter", "event study", "other"]
    S = [("30_table_pdfs_v1.py", 5), ("02_exposure_payroll_v4.py", 5), ("02_exposure_payroll_v5.py", 5),
         ("14_cbs_active_businesses_v1.py", 5), ("14_rd_brief_tables_v1.py", 5),
         ("24_sector_employment_scatter_v9.py", 5), ("CLAUDE.md", 5)]
    O = [("tables", "30_tables_20_stayer_wages_v1_v1.tex", 9), ("tables", "02_exposure_payroll_v5_d4.tex", 9),
         ("tables", "02_exposure_payroll_v4_d4.tex", 9), ("tables", "02_exposure_payroll_v3_d4.tex", 9),
         ("tables", "24_scatter_forks_v6_latex.txt", 9), ("tables", "14_cbs_active_businesses_v1.csv", 1),
         ("tables", "14_rd_files_v1.md", 9), ("tables", "requested_table.xlsx", 9), ("tables", "14_zz_v1.csv", 9)]
    g, un, _ = build(O, S)
    assert sorted(f["name"] for f in un) == ["14_zz_v1.csv", "requested_table.xlsx"], un
    t30 = g[("30", "table_pdfs")][("tables", "30_tables_20_stayer_wages_v1.tex")]
    assert [(f["k"], f["flags"]) for f in t30] == [(1, [])], t30
    d4 = g[("02", "exposure_payroll")][("tables", "02_exposure_payroll_d4.tex")]
    assert [(f["k"], f["flags"]) for f in d4] == [(5, []), (4, ["superseded"]), (3, ["superseded"])], d4
    fk = g[("24", "sector_employment_scatter")][("tables", "24_scatter_forks_latex.txt")]
    assert [(f["k"], f["flags"]) for f in fk] == [(6, ["behind"])], fk
    assert g[("14", "cbs_active_businesses")][("tables", "14_cbs_active_businesses.csv")][0]["flags"] == ["older"]
    assert ("tables", "14_rd_files.md") in g[("14", "rd_brief_tables")]
    F = parse_findings("# x\nupdated: 2026-10-13\n\n## Picture\nP.\n\n## F1. A claim\nimportance: high\n"
                       "exhibit: tables/02_exposure_payroll_v4_d4.tex\nalso: tables/02_exposure_payroll_v5_d4.tex, figures/x.pdf\n"
                       "evidence: docs/a.md\nchanged: 2026-10-13\ntags: payroll share, wages\npurpose: Why.\nBody.\n\n## Next\n1. Q: settle.\n")
    x = F["findings"][0]
    assert (F["updated"], F["picture"], x["id"], x["title"], x["importance"], x["changed"], x["body"], len(x["also"])) == (
        "2026-10-13", "P.", "F1", "A claim", "high", "2026-10-13", "Body.", 2), F
    assert x["tags"] == ["payroll share", "wages"], x
    assert bullets(x) == "<ul class=pws><li><b>Purpose:</b> Why.</li></ul>", bullets(x)
    ix = {f"{f['dir']}/{f['name']}": (f, d4[0]) for f in d4}
    assert warning(x["exhibit"], ix) == "superseded by tables/02_exposure_payroll_v5_d4.tex", warning(x["exhibit"], ix)
    assert warning(x["also"][0], ix) == "" and warning(x["also"][1], ix).startswith("missing"), ix
    # the control room's index: a sidecar family with two options and two versions, a plain versioned name,
    # a finding whose exhibit is in the family and whose second path is missing
    S2 = S + [("76_sector_bin_options_v2.py", 5)]
    O2 = O + [("figures", "76_bins_points_v2.pdf", 9), ("figures", "76_bins_count10_v2.pdf", 9),
              ("figures", "76_bins_count10_v1.pdf", 9), ("figures", "76_bins_count33_v1.pdf", 9)]
    fam = {"76_bins_points_v2.pdf": ("every sector", 0), "76_bins_count10_v2.pdf": ("10 eq", 1),
           "76_bins_count10_v1.pdf": ("10 eq", 1), "76_bins_count33_v1.pdf": ("33 eq", 1)}  # 33 only in v1, same rank
    side = lambda d, n: ({"family": "Sector scatter", "variant": [{"axis": "bins", "value": fam[n][0], "rank": fam[n][1]}]}
                         if n in fam else None)
    I = index(build(O2, S2), "## F4. A claim\nexhibit: figures/76_bins_count10_v2.pdf\nalso: figures/gone_v1.pdf\n", side)
    sc = I["plots"]["fam:Sector scatter"]
    assert sc["axes"] == [["bins", ["every sector", "10 eq", "33 eq"]], ["version", ["v2", "v1"]]], sc["axes"]
    assert set(sc["files"]) == {"every sector|v2", "10 eq|v2", "10 eq|v1", "33 eq|v1"}, sc["files"]
    assert sc["files"]["10 eq|v1"]["flags"] == ["superseded"] and sc["files"]["10 eq|v2"]["flags"] == [], sc["files"]
    assert sc["files"]["10 eq|v2"]["img"] == "png/figures/76_bins_count10_v2.pdf.png", sc["files"]
    d4i = I["plots"]["tables/02_exposure_payroll_d4.tex"]
    assert d4i["axes"] == [["version", ["v5", "v4", "v3"]]] and d4i["flags"] == [], d4i
    assert I["findings"] == [dict(id="F4", title="A claim", changed="", plots=[["fam:Sector scatter", "10 eq|v2"]],
                                  missing=["figures/gone_v1.pdf"])], I["findings"]
    assert any(s["id"] == "76_sector_bin_options" and s["plots"] == ["fam:Sector scatter"] for s in I["scripts"]), I["scripts"]
    stem = lambda d, n: side(d, n.rsplit(".", 1)[0] + ".pdf")  # the real sidecar() matches by stem, a caption included
    I = index(build(O2 + [("figures", "76_bins_points_v2.txt", 9)], S2),
              "## F4. A claim\nexhibit: figures/76_bins_count10_v2.pdf\nalso: figures/76_bins_points_v2.pdf\n", stem)
    assert I["findings"][0]["plots"] == [["fam:Sector scatter", "10 eq|v2"]], I["findings"]
    assert not any(pid.startswith("figures/") and pid.endswith(".txt") for pid in I["plots"]), [p for p in I["plots"] if p.endswith(".txt")]
    assert "every sector|v2" in I["plots"]["fam:Sector scatter"]["files"] and \
        I["plots"]["fam:Sector scatter"]["files"]["every sector|v2"]["path"].endswith(".pdf"), I["plots"]["fam:Sector scatter"]
    # the hosted index: every version since the floor (collaborators see everything but Claude; 10-20), no script
    # numbers in what is shown (ids and paths are keys and URLs), no "older" flag; the finding keeps its file
    H = hosted(I, since=0)
    hs, hd = H["plots"]["fam:Sector scatter"], H["plots"]["tables/02_exposure_payroll_d4.tex"]
    assert hs["axes"] == sc["axes"] and set(hs["files"]) == set(sc["files"]) and hs["files"]["10 eq|v1"]["flags"] == ["superseded"], hs
    assert hd["axes"] == [["version", ["v5", "v4", "v3"]]] and hd["files"]["v5"]["path"].endswith("_v5_d4.tex"), hd
    assert not any("older" in f["flags"] for p in H["plots"].values() for f in p["files"].values()), "older needs true mtimes"
    assert H["findings"][0]["plots"] == [["fam:Sector scatter", "10 eq|v2"]] and H["findings"][0]["missing"] == [], H["findings"]
    assert H["hosted"] and H["scripts"] == [] and all(p["script"] == p["nn"] == p["name"] == "" for p in H["plots"].values())
    shown = json.dumps([{k: v for k, v in p.items() if k not in ("files", "id")} for p in H["plots"].values()] + H["findings"])
    assert not re.search(r"\d\d_\w+_v\d", shown) and "fam:Sector scatter" in I["plots"] and I["scripts"], shown
    # before the floor: the table written at mtime 1 goes, the rest (mtime 9) stays
    assert set(I["plots"]) - set(hosted(I, since=5)["plots"]) == {"tables/14_cbs_active_businesses.csv"}, hosted(I, since=5)["plots"]
    # Graph D on the hosted copy: an arrow to a plot dropped there keeps its stored title but no link, and no source line
    kept = next(p for p in I["plots"] if p != "tables/14_cbs_active_businesses.csv")
    sr = dict(id="A1", **{"from": kept}, to="tables/14_cbs_active_businesses.csv", source="log 2026-09-22")
    hs = hosted(dict(I, story=[sr]), since=5)["story"][0]
    assert (hs["from_ok"], hs["to_ok"], hs["source"]) == (True, False, ""), hs
    # a file a pull touched (new mtime) but first committed before the floor stays out
    assert "tables/14_rd_files.md" not in hosted(I, since=5, born={"tables/14_rd_files_v1.md": 2})["plots"], "first commit counts"
    # a table in .md and .tex is one plot, the .md; a finding naming the .tex finds it; titles come from the titler,
    # and two plots with one title are marked
    O3 = O2 + [("tables", "14_rd_files_v1.tex", 9), ("figures", "14_rd_a_v1.pdf", 9), ("figures", "14_rd_b_v1.pdf", 9)]
    tl = {"tables/14_rd_files_v1.md": "R&D files", "figures/14_rd_a_v1.pdf": "Same", "figures/14_rd_b_v1.pdf": "Same"}
    I = index(build(O3, S2), "## F5. B\nexhibit: tables/14_rd_files_v1.tex\n", side, lambda path: tl.get(path, ""))
    assert "tables/14_rd_files.tex" not in I["plots"] and I["plots"]["tables/14_rd_files.md"]["title"] == "R&D files", I["plots"]
    assert I["findings"][0]["plots"] == [["tables/14_rd_files.md", ""]], I["findings"]
    assert I["plots"]["figures/14_rd_a.pdf"]["dup"] and not I["plots"]["tables/14_rd_files.md"]["dup"], I["plots"]
    assert I["plots"]["tables/02_exposure_payroll_d4.tex"]["title"] == "exposure payroll d4", I["plots"]
    # a table in .tex and .pdf is the .tex plot whatever order the disk lists them in (Linux and the Mac differ)
    tp = [("tables", "30_tables_33_cross_term_v1.pdf", 9), ("tables", "30_tables_33_cross_term_v1.tex", 9)]
    for O6 in (O2 + tp, O2 + tp[::-1]):
        assert "tables/30_tables_33_cross_term.tex" in index(build(O6, S2), "", side)["plots"], "twin order"
    # a chosen name (.room/titles.tsv) wins over the caption; a plot without one keeps its own title
    I5 = index(build(O3, S2), "", side, lambda path: tl.get(path, ""), {"tables/14_rd_files.md": "Files used in the brief"})
    assert I5["plots"]["tables/14_rd_files.md"]["title"] == "Files used in the brief", I5["plots"]["tables/14_rd_files.md"]
    assert I5["plots"]["figures/14_rd_a.pdf"]["title"] == "Same" and INTERNAL.search("Codebook: zeros_by_block.csv"), I5["plots"]
    assert not INTERNAL.search("Files used in the brief, 2010–2024")
    # a mini codebook: a table naming its CSV takes that codebook's definitions for every column, plain words included;
    # without a named CSV only code-like names are defined (the most common definition); unknown ones are reported
    B = {"hitech_share": {"share_pr": "Payroll over sales.", "year": "Tax year."}, "other": {"share_pr": "Elsewhere.", "n_firms": "Firms."},
         "third": {"n_firms": "Firms."}}
    assert define("Payroll share by year. 23_hitech_share_v8.csv, 15 rows", ["year", "share_pr", "label"], B) == (
        [["year", "Tax year."], ["share_pr", "Payroll over sales."]], []), define("23_hitech_share_v8.csv", ["year", "share_pr"], B)
    assert define("CODE by year", [], B, F={"CODE": "A made-up field label"}) == (
        [["CODE", "A made-up field label"]], []), "a field of the data owner takes its label"
    assert define("s_jt by year; see floor11_all_years.", [], B, V={"s_jt": "Sales weight.", "floor11_all_years": "-"}) == (
        [["s_jt", "Sales weight."]], []), ".room/variables.tsv defines, and '-' drops a name that is no variable"
    assert define("Counts; see `zz_top` and n_firms.", ["year", "n_firms"], B) == ([["n_firms", "Firms."]], ["zz_top"]), \
        define("Counts; see `zz_top` and n_firms.", ["year", "n_firms"], B)
    # a table family (10-15): two stems, each in .md and .tex, are one plot with a "sample" axis, main value first,
    # shown as the .md; a finding naming a .tex finds it; a _latex.txt beside its .csv is the same table, not a plot
    tst = {"27_split_model_v5": ("business", 0), "27_split_model_exht_v5": ("no high-tech", 1)}
    side4 = lambda d, n: ({"family": "Split", "variant": [{"axis": "sample", "value": tst[n.rsplit(".", 1)[0]][0],
                                                            "rank": tst[n.rsplit(".", 1)[0]][1]}]}
                          if d == "tables" and n.rsplit(".", 1)[0] in tst else side(d, n))
    O4 = O2 + [("tables", n, 9) for n in ("27_split_model_exht_v5.tex", "27_split_model_exht_v5.md", "27_split_model_v5.tex",
                                         "27_split_model_v5.md", "23_hitech_share_v9_latex.txt", "23_hitech_share_v9.csv")]
    S4 = S2 + [("27_split_model_v5.py", 5), ("23_hitech_share_v9.py", 5)]
    I4 = index(build(O4, S4), "## F6. C\nexhibit: tables/27_split_model_v5.tex\nalso: tables/23_hitech_share_v9_latex.txt\n", side4)
    sp = I4["plots"]["fam:Split"]
    assert sp["axes"] == [["sample", ["business", "no high-tech"]]] and set(sp["files"]) == {"business", "no high-tech"}, sp
    assert all(f["path"].endswith(".md") for f in sp["files"].values()), sp["files"]
    assert not any(p.startswith("tables/27_") for p in I4["plots"]) and "tables/23_hitech_share_latex.txt" not in I4["plots"], I4["plots"]
    assert I4["findings"][0]["plots"] == [["fam:Split", "business"], ["tables/23_hitech_share.csv", ""]], I4["findings"]
    # the hosted copy keeps a plot made only of superseded files (a family's older versions): the room hides it behind
    # "Show older drawings only", for collaborators too
    J = json.loads(json.dumps(I))
    for f in J["plots"]["tables/02_exposure_payroll_d4.tex"]["files"].values():
        f["flags"] = ["superseded"]
    assert "tables/02_exposure_payroll_d4.tex" in hosted(J, since=0)["plots"], hosted(J, since=0)["plots"].keys()
    # read dates: a readback commit's outputs (a caption counts for its figure) take its read; others snap their first
    # commit, or their mtime while uncommitted, to the newest read on or before it; a later restyle moves nothing
    day = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d")) + 3600
    log = (f"@{day('2026-10-21'):.0f}\nM\tfigures/63_a_v1.pdf\n"
           f"@{day('2026-10-20'):.0f}\nA\tdocs/runs/readout_2026-10-20_23rd.md\nM\tfigures/82_s_v1.txt\nA\tfigures/82_f_v1.pdf\n"
           f"@{day('2026-10-19'):.0f}\nA\tfigures/82_s_v1.pdf\nA\tfigures/82_s_v1.txt\n"
           f"@{day('2026-10-12'):.0f}\nA\tfigures/63_a_v1.pdf\n")
    rd, sit = reads(log, ["readout_2026-10-20_23rd.md", "readout_2026-10-14_19th.md", "readout_2026-10-14_20th.md",
                          "readout_2026-10-11_61_64.md", "82_x_v1_2026-10-20.txt"])
    assert [rd(p, day("2026-10-22")) for p in ("figures/82_s_v1.pdf", "figures/82_f_v1.pdf", "figures/63_a_v1.pdf",
                                               "figures/99_new_v1.pdf", "figures/01_old_v1.pdf")] == \
        ["2026-10-20", "2026-10-20", "2026-10-11", "2026-10-20", "2026-10-20"]
    assert rd("figures/01_old_v1.pdf", day("2026-09-01")) == ""
    assert sit == {"2026-10-20": "23rd sitting", "2026-10-14": "19th + 20th sitting"}, sit
    # the number check: a rounded value passes, sign aside; a neighbour off in the last digit fails; a share counts as
    # a percent only before "pp" or "%"; a caption number passes only as written
    src = ["panel,row,quantity,value\n2b,dlw,b,-0.4117\n2b,dlw,se,0.219\n2b,share,b,0.0472\n"]
    assert unmatched("Wages fall by 0.41 (SE 0.22) per point.", src) == []
    assert unmatched("Wages rise by 0.42 (SE 0.22).", src) == ["0.42"]
    assert unmatched("The share rises 4.7 pp; 4.7 firms.", src) == ["4.7"]
    assert unmatched("On 212 units, 2011–2016.", src, "212 units, 2011–2016") == []
    assert unmatched("On 213 units.", src, "212 units") == ["213"]
    # a tiny value in scientific notation is one cell, not a run into the next; *100 reads log units as log points
    assert unmatched("A slope of 0.28.", ["b,se\n-6.25e-05,0.2834\n"]) == []
    with tempfile.TemporaryDirectory() as tmp:  # under a made-up project root: the self-test reads no project file
        with open(os.path.join(tmp, "fit.csv"), "w") as fh:
            fh.write("outcome,weight,b\nln_firms,jobs,1.5\nln_firms,none,4.389\n")
        with open(os.path.join(tmp, "fits.csv"), "w") as fh:
            fh.write("panel,row,quantity,value\n2b,dlw,b,1\n2b,dlw,se,2\n2b,dlw,n,3\n2b,dlw,p,0.5\n2b,share,b,4\n1a,dlw,b,5\n")
        g0 = globals(); r0, g0["ROOT"] = ROOT, tmp
        try:
            assert source_text("fit.csv#outcome=ln_firms&weight=none*100").startswith("438.9")
            assert len(source_text("fits.csv#panel=2b&row=dlw").splitlines()) == 4  # one panel's row alone: its four quantities
        finally:
            g0["ROOT"] = r0
    assert unmatched("0.31 (SE 0.14) log points", ["0.00305,0.0014"]) == ["0.31", "0.14"]
    with tempfile.TemporaryDirectory() as tmp:  # a source narrowed to one panel's row; approving one row changes only it
        open(os.path.join(tmp, "t.csv"), "w").write(src[0])
        os.makedirs(os.path.join(tmp, "project")); r0, g0["ROOT"] = ROOT, os.path.join(tmp, "project")
        try:
            assert source_text("../t.csv") == "", "nothing outside the repository"
        finally:
            g0["ROOT"] = r0
        tsv = os.path.join(tmp, "about.tsv")
        rows = ["# header\n", "figures/a.pdf\tx\ts\t\tP.\tH.\tS.\n", "figures/b.pdf\tx\ts\t\tP.\tH.\tS.\n"]
        open(tsv, "w").writelines(rows)
        assert approve("figures/b.pdf", "2026-10-20", tsv) and not approve("figures/zz.pdf", "2026-10-20", tsv)
        assert open(tsv).readlines() == rows[:2] + ["figures/b.pdf\tx\ts\t2026-10-20\tP.\tH.\tS.\n"]
    # bundling (bundle): pages one token apart with one title head are one plot, a window and a glued pair one chip
    # each; a different head stays apart; a loose page beside a sidecar family joins it with its neighbour's choices
    assert tokens("63_isf_2b_firm_links_any_start") == ("isf", "2b", "firm", "links", "any", "start")
    assert tokens("fig_firm_forest_e2012_2012_2019") == ("fig", "firm", "forest", "e2012", "2012–2019")
    assert one_apart(("a", "x"), ("a",)) and one_apart(("a", "x"), ("a", "y")) and not one_apart(("a", "x", "y"), ("a", "y", "x"))
    assert [sorted(set(ax.values())) for ax in option_axes({1: ("f",), 2: ("f", "links"), 3: ("f", "links", "any"), 4: ("f", "rd"),
                                                             5: ("f", "rd", "surv"), 6: ("f", "surv")})] == [["", "links", "links any", "rd"], ["", "surv"]]
    assert stem_title(["Firm Outcomes and Exposure, 2012–2019, in 2012", "Firm Outcomes and Exposure, 2014–2019, in 2012"]) == "Firm Outcomes and Exposure"
    O7 = [("figures", f"91_ff_{e}_{w}_v1.pdf", 9) for e in ("e2012", "estart") for w in ("2012_2019", "2014_2019")] + \
         [("figures", "91_lv_distribution_v1.pdf", 9), ("figures", "91_lv_forest_v1.pdf", 9), ("figures", "91_lv_forest_v1.txt", 9),
          ("figures", "92_f_start_v1.pdf", 9), ("figures", "92_f_v1.pdf", 9), ("figures", "92_f_age10_start_v1.pdf", 9),
          ("figures", "92_f_start_survivors_v1.pdf", 9), ("figures", "92_f_age10_start_survivors_v1.pdf", 9)]
    S7 = [("91_ff_v1.py", 5), ("92_f_v1.py", 5)]
    tl7 = lambda path: ("Firm outcomes and exposure, by type" if "92_" in path else "Firm outcomes and exposure, " + path[-17:] if "_ff_" in path
                        else "Sector levels, distribution" if "distribution" in path else "Firm levels and exposure")
    fam7 = {"92_f_start_v1.pdf": ("2014", 0), "92_f_v1.pdf": ("2012", 1)}
    side7 = lambda d, n: {"family": "Firm outcomes and exposure, 2014–2019", "variant": [
        {"axis": "exposure yr", "value": fam7[n][0], "rank": fam7[n][1]}]} if n in fam7 else None
    I7 = index(build(O7, S7), "## F7. D\nexhibit: figures/91_ff_estart_2014_2019_v1.pdf\nalso: figures/91_ff_e2012_2014_2019_v1.pdf\n", side7, tl7)
    bundle(I7, rows=[("91", "estart", "exposure yr", "window start"), ("91", "e2012", "exposure yr", "2012"), ("92", "-", "split", "none"), ("92", "age10", "split", "age 10")])
    ff = I7["plots"]["auto:figures/91_ff"]
    assert ff["axes"] == [["exposure yr", ["window start", "2012"]], ["window", ["2012–2019", "2014–2019"]]] and len(ff["files"]) == 4, ff["axes"]
    assert ff["title"] == "Firm outcomes and exposure" and ff["members"][0] == "figures/91_ff_estart_2012_2019.pdf" and "unnamed" not in ff, ff
    assert I7["findings"][0]["plots"] == [["auto:figures/91_ff", "window start|2014–2019"]], I7["findings"]
    assert "figures/91_lv_distribution.pdf" in I7["plots"] and "figures/91_lv_forest.pdf" in I7["plots"], "another head stays apart"
    fe = I7["plots"]["fam:Firm outcomes and exposure, 2014–2019"]
    assert fe["axes"] == [["exposure yr", ["2014", "2012"]], ["split", ["none", "age 10"]], ["option", ["base", "survivors"]]], fe["axes"]
    assert fe["files"]["2014|age 10|survivors"]["path"] == "figures/92_f_age10_start_survivors_v1.pdf" and fe["unnamed"] == ["survivors", "-"], fe
    assert fe["members"] == ["fam:Firm outcomes and exposure, 2014–2019", "figures/92_f_age10_start.pdf", "figures/92_f_age10_start_survivors.pdf",
                             "figures/92_f_start_survivors.pdf"] and not any(p.startswith("figures/92_") for p in I7["plots"]), fe["members"]
    assert set(hosted(I7, since=0)["plots"]["auto:figures/91_ff"]["files"]) == set(ff["files"]), "a bundled family keeps every file"
    I7["plots"]["auto:figures/91_ff"]["archived"] = True
    H7 = hosted(I7, since=0)
    assert H7["plots"]["auto:figures/91_ff"]["archived"] and H7["findings"][0]["plots"], "an archived plot stays, hidden behind the filter"
    # review 10-20: a sidecar family is merged once (here it spans scripts 95 and 96), a page drawn by another script
    # version than its sidecar neighbour stays out, a new axis never takes the family's own axis name, and two pages
    # at one choice stay apart even at different versions
    fam10 = {"95_g_v1.pdf": "own", "95_g_common_v1.pdf": "common", "96_g_v1.pdf": "other"}
    side10 = lambda d, n: {"family": "G outcomes and exposure", "variant": [{"axis": "scale", "value": fam10[n], "rank": 0}]} if n in fam10 else None
    O10 = [("figures", n, 9) for n in ("95_g_v1.pdf", "95_g_common_v1.pdf", "95_g_wide_v1.pdf", "95_g_tall_v2.pdf", "96_g_v1.pdf", "96_g_wide_v1.pdf")]
    I10 = index(build(O10, [("95_g_v2.py", 5), ("96_g_v1.py", 5)]), "", side10, lambda path: "G outcomes and exposure")
    bundle(I10, rows=[("95", "wide", "scale", "wide")])
    g = I10["plots"]["fam:G outcomes and exposure"]
    assert [a for a, _ in g["axes"]] == ["scale", "scale 2"] and all(len(k.split("|")) == 2 for k in g["files"]), g["axes"]
    assert {"figures/96_g_wide.pdf", "figures/95_g_tall.pdf"} <= set(I10["plots"]), I10["plots"].keys()
    # 10-20: two drawings whose titles differ (imputed against measured labor cost) are one plot when this script's
    # rows put the tokens they differ by on one axis, and that axis stays apart from the family's other new axis
    O12 = [("figures", f"97_f_{n}v1.pdf", 9) for n in ("", "age10_", "lc-imp_", "lc-obs_", "lc-imp_start_")]
    I12 = index(build(O12, [("97_f_v1.py", 5)]), "", lambda d, n: None, lambda path: path[13:])
    bundle(I12, rows=[("97", "-", "split", "none"), ("97", "age10", "split", "age 10"), ("97", "-", "pay", "wages"),
                      ("97", "lc-imp", "pay", "imputed"), ("97", "lc-obs", "pay", "measured"), ("97", "-", "yr", "2012"), ("97", "start", "yr", "2014")])
    assert list(I12["plots"]) == ["auto:figures/97_f"], I12["plots"].keys()
    assert [a for a, _ in I12["plots"]["auto:figures/97_f"]["axes"]] == ["split", "pay", "yr"], I12["plots"]["auto:figures/97_f"]["axes"]
    I12 = index(build(O12, [("97_f_v1.py", 5)]), "", lambda d, n: None, lambda path: path[13:])
    bundle(I12, rows=[("*", "lc-imp", "pay", "imputed"), ("*", "lc-obs", "pay", "measured")])
    assert len(I12["plots"]) == 5, "a * row names a chip and merges nothing"
    O11 = [("figures", n, 9) for n in ("93_a_v1.pdf", "93_a_x_v1.pdf", "93_a_x_y_v1.pdf", "93_a_y_x_v2.pdf")]
    I11 = index(build(O11, [("93_a_v2.py", 5)]), "", lambda d, n: None, lambda path: "Same title of four words")
    bundle(I11)
    assert len(I11["plots"]) == 4, I11["plots"].keys()
    # two pages whose token sets cross (a_x_y, a_y_x) would share a choice: left as they were; page words join pages
    # whose titles are panel labels (no caption, no chosen name), and only those
    O8 = [("figures", f"93_{n}_v1.pdf", 9) for n in ("a", "a_x", "a_x_y", "a_y_x")]
    I8 = index(build(O8, [("93_a_v1.py", 5)]), "", lambda d, n: None, lambda path: "Same title of four words")
    bundle(I8)
    assert sorted(I8["plots"]) == ["figures/93_a.pdf", "figures/93_a_x.pdf", "figures/93_a_x_y.pdf", "figures/93_a_y_x.pdf"], I8["plots"]
    O9 = [("figures", f"94_s_{w}_v1.pdf", 9) for w in ("2012_2019", "2014_2019")]
    for wd, n in (({"x", "y", "z"}, 1), ({"x"}, 2)):
        I9 = index(build(O9, [("94_s_v1.py", 5)]), "", lambda d, n: None, lambda path: "Low exposure" if "_2012_2019" in path else "Medium exposure")
        bundle(I9, words=lambda path: wd if "2012_2019" in path else {"x", "y", "z"})
        assert len(I9["plots"]) == n, (wd, I9["plots"].keys())
    with tempfile.TemporaryDirectory() as tmp:  # archiving writes and clears one line per member; approving a What is this? line
        tsv = os.path.join(tmp, "archived.tsv")
        open(tsv, "w").write("# header\nfigures/b.pdf\t2026-10-01\n")
        assert archive(["figures/a.pdf", "fam:X"], True, "2026-10-20", tsv) and archived(tsv) == {"figures/a.pdf", "fam:X", "figures/b.pdf"}
        assert archive(["fam:X"], False, path=tsv) and archived(tsv) == {"figures/a.pdf", "figures/b.pdf"} and not archive(["a\tb"], True, path=tsv)
        ts = [dict(name="A", tags=["x"]), dict(name="B", tags=["y", "x"])]
        assert task_of(dict(tags=["y", "x"]), ts) == "A" and task_of(dict(tags=["y"]), ts) == "B" and task_of({}, ts) == ""
        imp = os.path.join(tmp, "importance.tsv")  # The owner's mark: set, changed, cleared; a bad level or id writes nothing
        assert mark(["fam:X", "figures/a.pdf"], "!!", "2026-10-21", imp) and marks(imp) == {"fam:X": "!!", "figures/a.pdf": "!!"}
        assert mark(["fam:X"], "!!!", path=imp) and mark(["figures/a.pdf"], "", path=imp) and marks(imp) == {"fam:X": "!!!"}
        assert not mark(["fam:X"], "high", path=imp) and not mark(["a\tb"], "!", path=imp) and marks(imp) == {"fam:X": "!!!"}
        wt = os.path.join(tmp, "whatis.tsv")
        open(wt, "w").write("fam:X\t\tOne line.\n")
        assert approve("fam:X", "2026-10-20", wt, col=1, cols=3) and open(wt).read() == "fam:X\t2026-10-20\tOne line.\n"
        open(wt, "a").write("figures/q.pdf\t\tWhy?\tBecause.\n")  # from the 11-06 read: question and answer (11-08)
        assert whatis_rows(wt) == {"fam:X": dict(text="One line.", answer="", status="approved"),
                                   "figures/q.pdf": dict(text="Why?", answer="Because.", status="draft")}
        st = os.path.join(tmp, "story.tsv")  # Graph D: an answer's numbers are checked against the files its source names
        open(st, "w").write("# header\nA1\t\tmain\tfigures/a.pdf\tA\tfigures/b.pdf\tB\tWhy?\tThe trend is 0.0041 a year.\tfindings F6\n"
                            "A2\t\tdead\tfigures/b.pdf\tB\t(none: c)\tc\tAnd?\tIt is 9.99.\tlog 2026-09-22 § Addendum 2\n"
                            "A3\t\tmain\tfigures/b.pdf\tB\tfigures/c.pdf\tC\tWho?\tAbout 2,400 workers.\tlog 2026-09-22\n")
        for rel, text in (("docs/log/2026-09-22.md", "About 2,400 workers moved.\n"), ("docs/findings.md", "The trend is 0.0041 a year.\n"),
                          ("docs/runs/readout_2026-09-30_08_11.md", "Item 15.\n")):  # the sources, under a made-up project root
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w") as fh:
                fh.write(text)
        g0 = globals(); r0, g0["ROOT"] = ROOT, tmp
        try:
            assert story_sources("log 2026-09-22 § Addendum 2; findings F6") == ["docs/log/2026-09-22.md", "docs/findings.md"]
            assert story_sources("readout 2026-09-30_08_11 § Item 15") == ["docs/runs/readout_2026-09-30_08_11.md"]
            assert [r["unmatched"] for r in story_rows(st)] == [[], ["9.99"], []], story_rows(st)
        finally:
            g0["ROOT"] = r0
        assert approve("A1", "2026-10-21", st, col=1, cols=len(STORY_COLS)) and story_rows(st)[0]["status"] == "approved"
        assert story_rows(st)[1]["status"] == "draft" and story_rows(st)[1]["to"] == "(none: c)"
        tt = os.path.join(tmp, "tasks.tsv")  # the room's tasks: a name, the meeting that set it, the tags that find its plots
        open(tt, "w").write("# header\nFirm age dummy\t2026-10-19\tAge dummies\nBad row\nCost check B\t2026-10-19\tRelated costs, Labor cost\n")
        assert tasks(tt) == [dict(name="Firm age dummy", meeting="2026-10-19", tags=["Age dummies"]),
                             dict(name="Cost check B", meeting="2026-10-19", tags=["Related costs", "Labor cost"])], tasks(tt)
    # the room's two ways in: this machine's page with the token cookie, and the tunnel with the owner's Access login only
    global PUBLIC, AUD, TOKEN
    PUBLIC, AUD, TOKEN = "room.example.com", "aud1", "t0k"
    loc = {"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}", "Cookie": "cr=t0k"}
    assert allowed(loc, post=True) and not allowed(dict(loc, Cookie="cr=x")) and not allowed(dict(loc, Origin="https://evil"), post=True)
    try:
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("access check not tested: run under uv run --with 'pyjwt[crypto]'")
    else:
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        g = globals(); emails, g["ROOM_EMAILS"] = ROOM_EMAILS, {"owner@example.org"}  # not the project's own list
        tok = lambda key=k, **c: jwt.encode(dict(dict(aud="aud1", email="Owner@example.org", exp=time.time() + 60), **c), key, "RS256")
        pub = {"Host": "room.example.com", "Origin": "https://room.example.com", "Cf-Access-Jwt-Assertion": tok()}
        assert allowed(pub, post=True, key=k.public_key()) and allowed(dict(pub, Origin=""), key=k.public_key())
        bad = [dict(pub, **{"Cf-Access-Jwt-Assertion": t}) for t in (tok(aud="aud2"), tok(email="other@example.org"), tok(exp=time.time() - 60),
                                                                       tok(rsa.generate_private_key(public_exponent=65537, key_size=2048)), "")]
        bad += [dict(pub, Origin="https://evil"), dict(pub, Host="other.example.com")]
        assert not any(allowed(h, post=True, key=k.public_key()) for h in bad)
        AUD = ""
        assert not allowed(pub, key=k.public_key())
        g["ROOM_EMAILS"] = emails
    PUBLIC, AUD, TOKEN = "", "", ""
    assert not allowed({"Host": "room.example.com"})
    # a sitting's takeaways: points numbered in order, files mapped to their plot and option, unknown files listed
    tk = takeaways(I, {"2026-10-20": "# T\n\n## 1. Wages rise.\nexhibit: figures/76_bins_count10_v2.pdf\nalso: figures/gone_v1.pdf\n"
                                     "Slope 0.41.\n\n## 2. Nothing to draw.\nA count only.\n"})["2026-10-20"]
    assert [(x["n"], x["t"], x["body"]) for x in tk] == [(1, "Wages rise.", "Slope 0.41."), (2, "Nothing to draw.", "A count only.")], tk
    assert tk[0]["plots"] == [[pid, c] for pid, p in I["plots"].items() for c, f in p["files"].items()
                              if f["path"] == "figures/76_bins_count10_v2.pdf"] and tk[0]["missing"] == ["figures/gone_v1.pdf"], tk[0]
    Ht = hosted(dict(I, takeaways={"d": tk}), since=0)["takeaways"]["d"]  # on the hosted copy too, files dropped there pointing at their plot
    assert Ht[0]["plots"] == tk[0]["plots"] and Ht[0]["missing"] == [] and Ht[1]["plots"] == [], Ht
    # the page shown: the equation copy beside a figure, or under figures/eq/<run>/ for an enclave page of that run only
    root, g = ROOT, globals()  # selftest reads ROOT above, so it is swapped through globals()
    with tempfile.TemporaryDirectory() as d:
        for q in ("figures/a_v1.eq.pdf", "figures/eq/70_x_v5/p.eq.pdf"):
            os.makedirs(os.path.dirname(os.path.join(d, q)), exist_ok=True)
            open(os.path.join(d, q), "w").close()
        with open(os.path.join(d, "figures/eq/70_x_v5/p.eq.json"), "w") as fh:  # the equation as text: enclave page only
            json.dump({"eq": "$y$", "notes": []}, fh)
        with open(sw := os.path.join(d, "switches.tsv"), "w") as fh:  # the ⓘ help: a literal \n is a new line
            fh.write("# comment\n*\tversion\tWhich drawing.\n88\tTrim\tFirms only.\\nNo trim: sectors.\nbad row\n")
        g["ROOT"], swap = d, g["SWITCHES"]
        g["SWITCHES"] = sw
        try:
            helps = switch_help()
            got = [eq_of(q) for q in ("figures/a_v1.pdf", "figures/b_v1.pdf", "out/70_x_v5/RELEASE/p.pdf", "out/70_x_v6/RELEASE/p.pdf")]
            specs = [spec_of(q) for q in ("figures/a_v1.pdf", "out/70_x_v5/RELEASE/p.pdf")]
            urls = [img_url("figures/a_v1.pdf"), img_url("figures/a_v1.pdf", plain=True)]
        finally:
            g["ROOT"], g["SWITCHES"] = root, swap
    assert helps == {("*", "version"): "Which drawing.", ("88", "Trim"): "Firms only.\nNo trim: sectors."}, helps
    assert got == ["figures/a_v1.eq.pdf", "figures/b_v1.pdf", "figures/eq/70_x_v5/p.eq.pdf", "out/70_x_v6/RELEASE/p.pdf"], got
    assert specs == [None, {"eq": "$y$", "notes": []}], specs
    assert [u.split("?")[0] for u in urls] == ["png/figures/a_v1.pdf.png", "png/figures/a_v1.pdf.plain.png"], urls
    # A foreign project (design 2026-11-13 §2). Each case runs this file again: the paths derived from ROOT at import
    # (LOCAL, FINDINGS, the TSVs) do not follow the globals() swap above.
    run = lambda *args, **kw: subprocess.run([sys.executable, os.path.abspath(__file__), *args], capture_output=True, text=True, **kw)
    with tempfile.TemporaryDirectory() as d:
        r = run("--project", d, "--export", os.path.join(d, "x"))  # neither figures/ nor tables/: one line, no traceback
        assert r.returncode == 1 and "no figures/ or tables/" in r.stderr and "Traceback" not in r.stderr, r.stderr
    with tempfile.TemporaryDirectory() as d:  # a project in a subfolder of its repository keeps its commit dates: the floor rests on them
        os.makedirs(os.path.join(d, "analysis", "figures")); os.makedirs(os.path.join(d, "analysis", "docs", "runs"))
        for rel in ("figures/10_a_v1.png", "docs/runs/readout_2026-01-02_1st.md"):
            open(os.path.join(d, "analysis", rel), "w").close()
        for a in (["init", "-q"], ["add", "-A"], ["-c", "user.name=x", "-c", "user.email=x@example.org", "commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", d, *a], capture_output=True)
        g["ROOT"] = os.path.join(d, "analysis")
        try:
            born, (read_of, _) = added(), reads_now()
        finally:
            g["ROOT"] = root
        assert list(born) == ["figures/10_a_v1.png"] and read_of("figures/10_a_v1.png", 0), (born, read_of("figures/10_a_v1.png", 0))
    with tempfile.TemporaryDirectory() as d:  # only figures/, and no git history: the room is served and the export finishes
        os.makedirs(os.path.join(d, "figures")); out = os.path.join(d, "x")
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001"
                            "e221bc330000000049454e44ae426082")  # 1 x 1
        for n in ("10_a_v1.png", "101_b_v2.png"):
            with open(os.path.join(d, "figures", n), "wb") as fh:
                fh.write(png)
        r = run("--project", d, "--export", out); assert r.returncode == 0, r.stderr
        with open(os.path.join(out, "index.json")) as fh:
            ix = json.load(fh)
        assert len(ix["plots"]) == 2, ix["plots"].keys()   # 10 and 101: two plots, both exported
        assert os.path.isfile(os.path.join(out, "f", "figures", "101_b_v2.png")) and os.path.isfile(os.path.join(out, "room.html"))
        g["ROOT"] = d
        try:
            listing = page()  # /all, where an output with no script is listed under its own name
        finally:
            g["ROOT"] = root
        assert "101_b_v2.png" in listing and "code/local/" not in listing, listing[-600:]
        # a PDF figure without poppler: one line naming what to install (PATH holds git alone)
        for n in ("11_c_v1.pdf", "11_d_v1.pdf"):  # two under one number: bundling reads their text before any page is drawn
            with open(os.path.join(d, "figures", n), "wb") as fh:
                fh.write(b"%PDF-1.4")
        os.makedirs(bin_ := os.path.join(d, "bin")); os.symlink(shutil.which("git"), os.path.join(bin_, "git"))
        r = run("--project", d, "--export", out, env=dict(os.environ, PATH=bin_))
        assert r.returncode == 1 and "pdftoppm is needed" in r.stderr and "Traceback" not in r.stderr, r.stderr
        for n in ("11_c_v1.pdf", "11_d_v1.pdf"):
            os.remove(os.path.join(d, "figures", n))
        # one script in code/local/ takes no other plot out of the room (review 11-13, F8); a file the room cannot place is named
        os.makedirs(os.path.join(d, "code", "local"))
        for rel in ("code/local/10_a_v1.py", "figures/sketch.png"):
            open(os.path.join(d, rel), "w").close()
        r = run("--project", d, "--export", out)
        with open(os.path.join(out, "index.json")) as fh:
            assert r.returncode == 0 and len(json.load(fh)["plots"]) == 2, r.stderr
        lost = [l for l in r.stderr.splitlines() if "not in the room" in l]
        assert len(lost) == 1 and lost[0].startswith("1 output is") and "sketch.png" in lost[0] and "101_b" not in lost[0], r.stderr
        shutil.rmtree(os.path.join(d, "code")); os.remove(os.path.join(d, "figures", "sketch.png"))
        # .room/config.toml (design §3): none means the defaults; a misspelt key stops the engine and is named
        def title():
            with open(os.path.join(out, "index.json")) as fh:
                return json.load(fh)["title"]

        def conf(text):
            os.makedirs(os.path.join(d, ".room"), exist_ok=True)
            with open(os.path.join(d, ".room", "config.toml"), "w") as fh:
                fh.write(text)
        assert run("--project", d, "--export", out).returncode == 0 and title() == os.path.basename(d)
        for text, key in (('titel = "x"\n', "titel"), ('[hosted]\nhots = ["a"]\n', "hosted.hots")):
            conf(text)
            r = run("--project", d, "--export", out)
            assert r.returncode == 1 and f"unknown key '{key}'" in r.stderr and "Traceback" not in r.stderr, r.stderr
        conf('name = "Demo"\ntitle = "A demo"\n')
        assert run("--project", d, "--export", out).returncode == 0 and title() == "A demo"
        r = run("--project", d, "--claude", "--fake", "--public", "room.example.org", env=dict(os.environ, ACCESS_AUD="x"), timeout=30)
        assert r.returncode != 0 and "[hosted] emails and access_team" in r.stderr, r.stderr  # no allow-list: the web room refuses to start
        r = run("--project", d, "--publish")  # no [hosted]: refuses, and writes no publish folder
        assert r.returncode == 1 and "no [hosted] section" in r.stderr and not os.path.exists(os.path.join(d, ".room", "site")), r.stderr
    print("selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", metavar="DIR", default=".", help="the project folder, holding figures/ or tables/ (default: the current folder)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--export", metavar="DIR", help="write the room as a static site to DIR")
    ap.add_argument("--claude", action="store_true", help="add the owner's Claude panel to /room (needs claude-agent-sdk)")
    ap.add_argument("--about-todo", action="store_true", help="list the figures whose Purpose / How / What it shows is missing or outdated")
    ap.add_argument("--labels-todo", action="store_true", help="list the figures without a What is this? line or a tag, and chips named by a file-name token")
    ap.add_argument("--public", metavar="HOST", help="with --claude: also serve the room to the owner's Access login at HOST (a tunnel; env ACCESS_AUD)")
    ap.add_argument("--publish", action="store_true", help="deploy the export to Cloudflare Pages as [hosted] in .room/config.toml says; refuses unless Access guards the hosts")
    ap.add_argument("--fake", action="store_true", help="with --claude: scripted runs, no model call (tests the panel)")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not any(os.path.isdir(os.path.join(ROOT, d)) for d in DIRS):
        sys.exit(f"no figures/ or tables/ in {ROOT}: run from the project folder or pass --project")
    if a.export:
        sys.exit(export(a.export))
    if a.publish:
        sys.exit(publish())
    if a.about_todo:
        sys.exit(about_todo())
    if a.labels_todo:
        sys.exit(labels_todo())
    PORT = a.port
    if a.claude:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import agent
        if a.public and not (ROOM_EMAILS and HOSTED.get("access_team")):
            sys.exit("--public needs [hosted] emails and access_team in .room/config.toml: the web room's allow-list")
        HUB, TOKEN = agent.Hub(fake=a.fake), secrets.token_urlsafe(18)
        if a.public:
            PUBLIC, AUD = a.public, os.environ.get("ACCESS_AUD", "")
            if not AUD:
                ap.error("--public needs ACCESS_AUD, the Access app's audience tag")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True  # open event streams do not keep Ctrl-C waiting
    threading.Thread(target=index_now, daemon=True).start()  # fills the title and type caches: a cold index takes ~10 s
    print(f"http://127.0.0.1:{a.port}/  (Ctrl-C to stop)", flush=True)
    if a.claude:
        print(f"control room with Claude: http://127.0.0.1:{a.port}/room?token={TOKEN}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
