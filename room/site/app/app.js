/* The control room (plan docs/plans/2026-10-15-control-room.md; rehaul 2026-11-12): the plot tree on the left, the
   open cards in the middle, and on the right the details of the card in focus, in folds: Specification, Purpose · How ·
   Results, Switches (open), the names' meanings, Claude, Caption. Both side panels fold to a rail and open over the
   cards while the mouse is on them. Over index.json, the one index room/dashboard.py builds from figures/ and tables/.
   Plain JS, no build step. A card is one plot at one combination of its options; ticking several plots in the tree
   opens several cards, and "split" opens a second card of the same plot to set beside the first (10-15). The
   wheel zooms a plot under the mouse, a drag moves it, a double click resets it. The Claude fold appears only when
   api/hello answers (the Mac, room/agent.py). */
"use strict";
let I = null, P = {}, CLAUDE = false;
const T = {};  // Q&A cards by plot id, from the agent API
const TXT = {};  // table text by path, fetched once
const KIND = { q: "QUESTION", a: "ANSWER", pit: "PITFALL", alt: "ANOTHER WAY", act: "CHANGED FILES", ok: "NEEDS YOU" };

const store = {  // "cr2.": the keys of the first room (layouts, navigators, dark default) are left behind; "group2": by read
  // date became the default (10-21), so an old "group" pick does not hide it
  get(k, d) { try { const v = localStorage.getItem("cr2." + k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("cr2." + k, v); } catch (e) { /* private window: the toggles just reset */ } },
};
const PERSIST = ["theme", "kind", "order", "ptype"];
// the grouping by finding is gone (11-12): a stored pick of it reads by read date
const S = { order: store.get("order", "name"), navw: +store.get("navw", 300), sidew: +store.get("sidew", 360),
  navmin: store.get("navmin", "0") === "1", sidemin: store.get("sidemin", "0") === "1",
  group: store.get("group2", "read").replace(/^finding$/, "read"), theme: store.get("theme", "light"), kind: store.get("kind", "figure"), ptype: store.get("ptype", "").replace(/^all$/, ""), viewer: "owner", bytask: store.get("bytask", "1") === "1",
  filter: "", cards: [], focus: null, replyTo: "plot", open: {}, claude: { st: "idle", t0: 0, plot: null } };
{ const g = new URLSearchParams(location.search).get("group");  // /room?group=read, the dashboard's "Figures by read date"
  if (["script", "read"].includes(g)) { S.group = g; store.set("group2", g); } }
let NEXT = 0;

/* ---------- index helpers ---------- */
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// card text: escaped, then **bold** and `code` only, as dashboard.py's prose() does for findings
const md = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
const owner = () => S.viewer === "owner";
const keyOf = (pid, v) => P[pid].axes.map(([a]) => v[a]).join("|");
const parse = (pid, k) => { const o = {}, parts = k.split("|"); P[pid].axes.forEach(([a], i) => { o[a] = parts[i]; }); return o; };
const lexi = (a, b) => { for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]; return 0; };
const ranks = (pid, o) => P[pid].axes.map(([a, vals]) => vals.indexOf(o[a]));
const combos = pid => Object.keys(P[pid].files).map(k => parse(pid, k)).sort((x, y) => lexi(ranks(pid, x), ranks(pid, y)));
function best(pid) {  // the newest version, then the lowest-ranked option on each axis, among the files that exist
  const ver = P[pid].axes.findIndex(([a]) => a === "version");
  const score = o => { const s = ranks(pid, o); if (ver >= 0) s.unshift(s.splice(ver, 1)[0]); return s; };
  return combos(pid).sort((x, y) => lexi(score(x), score(y)))[0];
}
const DEF = {};  // a plot's first combination: the one its finding shows, else best()
const fileOf = c => P[c.pid].files[keyOf(c.pid, c.v)];
const label = (pid, v) => P[pid].axes.map(([a]) => v[a]).filter(Boolean).join(" · ") || "only version";
const thread = pid => T[pid] || [];
const live = pid => S.claude.plot === pid && !["idle", "done"].includes(S.claude.st);
// the plot's title as the figure states it; the owner's view keeps the script number, a title two plots share adds the name
const title = pid => { const p = P[pid]; return owner() ? `${p.nn} · ${p.title}${p.dup && p.name ? ` (${p.name})` : ""}` : p.title; };
// the filter menu (10-20): archived plots hide unless "Show archived" is ticked (every viewer); each other
// filter is one value of a plot, offered with its count among the plots of the kind shown; "" in S is "any"
const INF = new Set();  // the plots a finding shows, filled at the start
const aboutState = pid => { const st = [P[pid].about, ...Object.values(P[pid].files).map(f => f.about)].filter(Boolean).map(a => a.status);
  return !st.length ? "none" : st.includes("outdated") ? "outdated" : "written"; };
const FILTERS = [  // [state key, label, the plot's value(s)]: every viewer, as everything but Claude is (10-20)
  ["ptype", "Plot type", pid => P[pid].ptype],
  ["tag", "Tag", pid => P[pid].tags || []],
  ["read", "Read", pid => P[pid].read || "before the first readout"],
  ["inF", "Finding", pid => INF.has(pid) ? "in a finding" : "not in a finding"],
  ["about", "Purpose · How · Results", aboutState],
  ["src", "Drawn", pid => P[pid].drawn || "locally"],
  ["flag", "Flags", pid => (P[pid].flags || []).some(x => x === "behind" || x === "older") ? "behind or older" : "up to date"],
];
// a plot whose every file is an older drawing (a newer version of its name lives in another plot) hides like an archived one
const older = pid => Object.values(P[pid].files).every(f => f.flags.includes("superseded"));
const ofKind = pid => (S.kind === "all" || P[pid].kind === S.kind) && (S.arch || !P[pid].archived) && (S.old || !older(pid));
const shown = pid => ofKind(pid) && FILTERS.every(([k, , get]) => !S[k] || [].concat(get(pid)).includes(S[k])) &&
  (!S.filter || (P[pid].title + " " + P[pid].script + " " + Object.values(P[pid].files).map(f => f.path).join(" ")).toLowerCase().includes(S.filter.toLowerCase()));
function filterMenu() {
  const base = Object.keys(P).filter(ofKind), on = FILTERS.filter(([k]) => S[k]).length + !!S.arch + !!S.old;
  const sel = ([k, lab, get]) => { const n = {}; base.forEach(pid => [].concat(get(pid)).forEach(v => { n[v] = (n[v] || 0) + 1; }));
    if (S[k] && !n[S[k]]) n[S[k]] = 0;
    const vs = Object.keys(n).sort(); if (k === "read") vs.reverse();
    return vs.length ? `<label>${esc(lab)}</label><select data-filt="${k}"><option value="">any</option>${vs.map(v =>
      `<option value="${esc(v)}" ${S[k] === v ? "selected" : ""}>${esc(v)} (${n[v]})</option>`).join("")}</select>` : ""; };
  const count = test => Object.keys(P).filter(pid => test(pid) && (S.kind === "all" || P[pid].kind === S.kind)).length;
  return `<details class="fmenu" data-open="fmenu" ${S.open.fmenu ? "open" : ""}><summary class="btn ${on ? "act" : ""}">Filter${on ? ` · ${on}` : ""}</summary><div class="fpanel">
    <label class="wide"><input type="checkbox" id="farch" ${S.arch ? "checked" : ""}> Show archived (${count(pid => P[pid].archived)})</label>
    <label class="wide"><input type="checkbox" id="fold" ${S.old ? "checked" : ""}> Show older drawings only (${count(older)})</label>
    ${FILTERS.map(sel).join("")}${on ? `<button class="btn wide" id="freset">Clear the filters</button>` : ""}</div></details>`;
}
const cardOf = id => S.cards.find(c => c.id === id);
const focused = () => cardOf(S.focus) || S.cards[0];
const isOpen = pid => S.cards.some(c => c.pid === pid);
const newCard = (pid, v) => ({ id: "c" + NEXT++, pid, v: { ...(v || DEF[pid] || best(pid)) } });

function groupsNow() {
  if (S.group === "read") {  // newest read first and open; "" (before the first readout) last; a sitting is its date (11-12)
    const by = {};
    const tg = pid => (P[pid].tags || [])[0] || "￿";  // tagged plots first, by tag, so a request's pages sit together
    Object.keys(P).sort((a, b) => tg(a).localeCompare(tg(b)) || a.localeCompare(b)).forEach(pid => (by[P[pid].read] = by[P[pid].read] || []).push(pid));
    return Object.keys(by).sort().reverse().map((d, i) => ({ id: "read:" + d, plots: by[d], closed: i > 0, subs: taskSubs(by[d]),
      t: d || "Before the first readout" }));
  }
  return I.scripts.map(s => ({ id: s.id, t: s.title, plots: s.plots, closed: true }));
}

// a sitting split by the tasks a meeting set (.room/tasks.tsv; 10-21): a plot goes to the first task whose tags
// it carries, the rest to Other; a sitting with no task's plots stays one list
function taskSubs(ps) {
  const T = I.tasks || [], at = pid => T.findIndex(t => (P[pid].tags || []).some(x => t.tags.includes(x)));
  if (!ps.some(pid => at(pid) >= 0)) return null;
  return T.map((t, i) => ({ t: t.name, plots: ps.filter(pid => at(pid) === i) })).concat([{ t: "Other", plots: ps.filter(pid => at(pid) < 0) }]);
}

/* ---------- a card's options ---------- */
// the new file loads first and the card changes once it has, so the old figure stays on screen meanwhile (10-15)
const ready = f => f.img ? (() => { const im = new Image(); im.src = f.img; return im.decode().catch(() => {}); })() :
  TEXT.test(f.path) && typeof TXT[f.path] !== "string" ? fetch("f/" + f.path).then(r => r.text()).then(t => { TXT[f.path] = t; }).catch(() => {}) : Promise.resolve();
function setAxis(c, a, val) {
  let want = { ...c.v, [a]: val };
  if (!P[c.pid].files[keyOf(c.pid, want)]) want = combos(c.pid).filter(o => o[a] === val)[0];
  if (!want) return;
  const k = c.want = keyOf(c.pid, want);
  S.focus = c.id;
  ready(P[c.pid].files[k]).then(() => { if (c.want === k) { c.v = want; render(); } });  // a later click wins
}
// every switch has its ⓘ (11-12): its help from .room/switches.tsv (dashboard.py switch_help), shown on hover or
// on click (a CSS tooltip since 11-01: the title attribute never showed); `dashboard.py --export` lists the ones unwritten
function switcher(c) {
  const p = P[c.pid];
  if (!p.axes.length) return "";
  const help = a => (p.help || {})[a] || "Not described yet.";
  return p.axes.map(([a, vals]) => `<div class="axis"><span class="ax">${esc(a)} <span class="info" tabindex="0" role="note" aria-label="${esc(help(a))}" data-help="${esc(help(a))}">ⓘ</span></span>` + vals.map(val => {
    const ok = !!p.files[keyOf(c.pid, { ...c.v, [a]: val })];
    return `<button class="chip ${c.v[a] === val ? "on" : ""} ${ok ? "" : "off"}" data-axis="${c.id}|${esc(a)}|${esc(val)}" title="${ok ? "" : "not drawn with the other choices; click to jump"}">${esc(val)}</button>`;
  }).join("") + `</div>`).join("");
}

/* ---------- tables drawn as tables: Markdown (headings, paragraphs, pipe tables) and CSV ---------- */
const cells = l => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(x => x.trim());
const tableHTML = rows => `<table class="dt"><thead><tr>${rows[0].map(x => `<th>${md(x)}</th>`).join("")}</tr></thead><tbody>` +
  rows.slice(1).map(r => `<tr>${r.map(x => `<td>${md(x)}</td>`).join("")}</tr>`).join("") + `</tbody></table>`;
function mdHTML(text) {  // prose longer than a line or two folds under "Notes", so the tables come first (10-15)
  const out = [], lines = text.split("\n");
  let prose = [];
  const flush = () => { const t = prose.join(" ");
    if (t.length > 240) out.push(`<details class="notes"><summary>Notes: ${esc(t.replace(/[`*]/g, "").slice(0, 110))}…</summary>${prose.map(p => `<p>${md(p)}</p>`).join("")}</details>`);
    else out.push(...prose.map(p => `<p>${md(p)}</p>`));
    prose = []; };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*\|/.test(l)) {
      flush(); const rows = [];
      for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) rows.push(cells(lines[i]));
      i--; out.push(tableHTML(rows));
    } else if (/^#+ /.test(l)) { flush(); out.push(`<h6>${md(l.replace(/^#+ /, ""))}</h6>`); }
    else if (l.trim()) prose.push(l);
  }
  flush();
  return out.join("");
}
// LaTeX tables: each tabular or longtable drawn as a table, rows above \midrule as the header (10-15)
function group(s, i) {  // s[i] is "{": the index after its matching "}"
  for (let d = 0; i < s.length; i++) { if (s[i] === "{") d++; else if (s[i] === "}" && --d === 0) return i + 1; }
  return s.length;
}
function texCell(s) {  // -> [html, colspan]
  let span = 1;
  const m = s.match(/^\s*\\multicolumn\{(\d+)\}/);
  if (m) { span = +m[1]; let i = s.indexOf("{", m[0].length); i = group(s, i); s = s.slice(i).trim().replace(/^\{([\s\S]*)\}$/, "$1"); }
  s = s.replace(/\\textbackslash\{?\}?/g, "\u0000").replace(/\\([%&_#$])/g, "$1").replace(/\\(?:[,;!]|quad)/g, " ")
    .replace(/\\[a-zA-Z]+\*?(\[[^\]]*\])?/g, "").replace(/[{}$]/g, "").replace(/---/g, "—").replace(/--/g, "–").replace(/~/g, " ").replace(/\u0000/g, "\\");
  return [esc(s.trim()), span];
}
function texHTML(text) {
  const out = [], re = /\\begin\{(tabular\*?|tabularx|longtable)\}/g;
  let m;
  while ((m = re.exec(text))) {
    let i = m.index + m[0].length;
    while (/^\s*[{[]/.test(text.slice(i))) { i = text.indexOf(text.slice(i).trim()[0], i); i = text[i] === "{" ? group(text, i) : text.indexOf("]", i) + 1; }
    const end = text.indexOf(`\\end{${m[1]}}`, i);
    let body = text.slice(i, end < 0 ? text.length : end).replace(/(^|[^\\])%.*$/gm, "$1");
    body = body.replace(/\\shortstack(\[[^\]]*\])?\{/g, "\\shortstack{");  // its "\\" are line breaks inside one cell
    for (let k; (k = body.indexOf("\\shortstack{")) >= 0;) { const j = group(body, k + 11); body = body.slice(0, k) + body.slice(k + 12, j - 1).replace(/\\\\/g, " ") + body.slice(j); }
    let head = "";
    if (/\\endfirsthead/.test(body)) { head = body.split("\\endfirsthead")[0]; body = body.split(/\\endlastfoot|\\endhead/).pop(); }
    else if (/\\midrule/.test(body)) { [head, body] = [body.slice(0, body.indexOf("\\midrule")), body.slice(body.indexOf("\\midrule"))]; }
    const rows = s => s.replace(/\\(toprule|midrule|bottomrule|hline|endfoot|endhead|endlastfoot|endfirsthead)\b|\\cmidrule(\([^)]*\))?\{[^}]*\}/g, "")
      .split(/\\\\(?:\[[^\]]*\])?/).map(r => r.trim()).filter(Boolean).map(r => r.split(/(?<!\\)&/).map(texCell));
    const tr = (r, tag) => `<tr>${r.map(([h, n]) => `<${tag}${n > 1 ? ` colspan="${n}"` : ""}>${h}</${tag}>`).join("")}</tr>`;
    out.push(`<table class="dt"><thead>${rows(head).map(r => tr(r, "th")).join("")}</thead><tbody>${rows(body).map(r => tr(r, "td")).join("")}</tbody></table>`);
  }
  return out.length ? out.join("") : `<pre>${esc(text)}</pre>`;
}
function csvRows(text) {  // ponytail: quotes handled, embedded newlines are not; the release CSVs have none
  return text.trim().split(/\r?\n/).map(l => { const r = []; let cur = "", q = false;
    for (const ch of l) { if (ch === '"') q = !q; else if (ch === "," && !q) { r.push(cur); cur = ""; } else cur += ch; }
    r.push(cur); return r; });
}
function fillTables() {
  document.querySelectorAll(".tv[data-src]").forEach(el => {
    const path = el.dataset.src, done = () => { el.innerHTML = el.classList.contains("cap") ? `<p>${esc(TXT[path])}</p>` : path.endsWith(".md") ? mdHTML(TXT[path]) : path.endsWith(".csv") ? tableHTML(csvRows(TXT[path])) : /\\begin\{(tabular|longtable)/.test(TXT[path]) ? texHTML(TXT[path]) : `<pre>${esc(TXT[path])}</pre>`;
      el.removeAttribute("data-src"); };
    if (typeof TXT[path] === "string") done();
    else if (!TXT[path]) TXT[path] = fetch("f/" + path).then(r => r.text()).then(t => { TXT[path] = t; fillTables(); });
  });
}
const TEXT = /\.(md|csv|tex|txt|log)$/;
function view(f) {  // one file: a figure as its image, a text table drawn by fillTables(), anything else in a frame
  return f.img ? `<img src="${esc(f.img)}" alt="" draggable="false">` : TEXT.test(f.path) ? `<div class="tv" data-src="${esc(f.path)}">Loading…</div>` : `<iframe src="f/${esc(f.path)}"></iframe>`;
}

/* ---------- the right panel: the card in focus, in folds ---------- */
// a fold's open state is kept per browser, the same for every plot; only Switches starts open (11-12)
const sec = (k, head, body, def = false) => body ? `<details class="sec" data-open="sec:${k}" ${(S.open["sec:" + k] ?? store.get("sec:" + k, def ? "1" : "0") === "1") ? "open" : ""}><summary>${head}</summary><div class="sec-b">${body}</div></details>` : "";
// Specification: the estimating equation, drawn large by KaTeX from the text figure_rules.save(equation=) keeps beside
// the page (room/dashboard.py spec_of; 11-12); until KaTeX loads, or if cdnjs cannot be reached, the TeX itself
const KATEX = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/";
let KX = null;
const needKatex = () => KX || (KX = new Promise((ok, no) => {
  document.head.insertAdjacentHTML("beforeend", `<link rel="stylesheet" href="${KATEX}katex.min.css">`);
  const s = document.createElement("script"); s.src = KATEX + "katex.min.js"; s.onload = ok; s.onerror = no; document.head.appendChild(s);
}).then(render, () => {}));
// mathtext as figure_rules writes it: text with $…$ spans
const tex = s => window.katex ? s.split("$").map((x, i) => i % 2 ? katex.renderToString(x, { throwOnError: false }) : esc(x)).join("") : esc(s);
function specHTML(f) {
  if (!f.eq) return "";
  if (!window.katex) needKatex();
  // two equations joined by \quad in one span: each on its own line (11-12)
  const eqs = f.eq.eq.replace(/^\$|\$$/g, "").split(/,?\s*\\q?quad\s*/).map(x => `<div>${tex("$" + x + "$")}</div>`).join("");
  return `<div class="spec-eq">${eqs}</div>${f.eq.notes.length ? `<ul class="spec-notes">${f.eq.notes.map(n => `<li>${tex(n)}</li>`).join("")}</ul>` : ""}`;
}
// Purpose / How / Results (dashboard.py about_of; 10-20; "What it shows" is Results since 11-12): the fold line
// says whether it is written about an older drawing or quotes a number its sources do not give; no draft label or
// Approve (11-08: the owner does not review these). A family's file carries its own row (its member's) before the plot's.
function aboutSec(pid, f) {
  const a = f.about || P[pid].about, head = "Purpose · How · Results";
  if (!a) return owner() && P[pid].kind === "figure" ? sec("about", head, `<div class="about none">None written yet.</div>`) : "";
  const warn = a.unmatched.length ? `<div class="about-warn">Not given by its sources: ${a.unmatched.map(esc).join(", ")}</div>` : "";
  const st = (a.status === "outdated" ? ` <span class="badge outdated">written about an older drawing</span>` : "") +
    (warn ? ` <span class="badge outdated">numbers to check</span>` : "");
  return sec("about", head + st, `<div class="about"><p><b>Purpose.</b> ${md(a.purpose)}</p><p><b>How.</b> ${md(a.how)}</p><p><b>Results.</b> ${md(a.shows)}</p>${warn}</div>`);
}
// the mini codebook (dashboard.py variables): what each variable name shown in the table or figure means
const varsHTML = f => (f.vars || []).length ? `<dl class="vars">${f.vars.map(([n, d]) => `<dt><code>${esc(n)}</code></dt><dd>${md(d)}</dd>`).join("")}</dl>` : "";
function sideHTML() {
  const pin = `<button class="pin" data-min="sidemin" title="${S.sidemin ? "keep this panel open" : "fold this panel to the edge; it opens while the mouse is on it"}">${S.sidemin ? "«" : "»"}</button>`;
  const c = focused();
  if (!c) return `<div class="side-top">${pin}<span>Details</span></div><div class="empty">Open a plot to see its details here.</div>`;
  const p = P[c.pid], f = fileOf(c);
  const flags = f.flags.map(x => `<span class="flag">${esc(x)}</span>`).join(" ");
  // The owner's importance mark (10-21), how key the figure is to its task: the button cycles none → ! → !! → !!! → none, on every member of a family
  const imp = CLAUDE && owner() ? `<button class="btn" data-imp="${esc(c.pid)}" title="importance to its task: click for the next mark (none, !, !!, !!!)">Importance ${p.importance || "–"}</button>` : "";
  const arch = CLAUDE && owner() ? `<button class="btn" data-archive="${esc(c.pid)}" title="${p.archived ? "show it in the tree again" : "hide it from the tree, for collaborators too; the filter's Show archived brings it back"}">${p.archived ? "Restore" : "Archive"}</button>` : "";
  const cap = f.caption_txt ? `<div class="tv cap" data-src="${esc(f.path.replace(/\.[^.]+$/, ".txt"))}">Loading…</div>` : "";
  const qa = CLAUDE && owner() ? sec("claude", `Claude${thread(c.pid).length ? ` · ${thread(c.pid).length}` : ""}`, threadHTML(c.pid) + askBar()) : "";
  return `<div class="side-top">${pin}<span>Details</span></div><div class="side-head"><b>${esc(title(c.pid))}</b>${impHTML(c.pid)}${p.archived ? ' <span class="badge">archived</span>' : ""}
    <div class="pn-meta">${owner() ? `<span class="mono">${esc(f.path.split("/").pop())}</span><br>` : ""}updated ${esc(p.updated)} ${flags}</div>
    <div class="pn-act"><a class="btn" href="f/${esc(f.path)}" target="_blank">Open file</a>${imp}${arch}</div></div>` +
    sec("spec", "Specification", specHTML(f)) + aboutSec(c.pid, f) + sec("switches", "Switches", switcher(c), true) +
    sec("vars", "What the names mean", varsHTML(f)) + qa + sec("caption", "Caption", cap);
}

/* ---------- Q&A cards: a plot's thread in the Claude fold, each follow-up under the card it follows ---------- */
function cardHTML(c) {
  const secs = c.t0 ? Math.round((Date.now() - c.t0) / 1000) : 0;
  const stepList = (c.steps || []).map(s => `<li><b>${esc(s[0])}</b><span class="mono">${esc(s[1])}</span></li>`).join("");
  let body = "";
  if (c.live) {
    const head = c.queued ? `<div class="think">⏳ Queued behind the question in progress</div>` : c.waiting ? `<div class="think">✋ Waiting for you · ${secs} s</div>` :
      `<div class="think"><span class="dots"><span></span><span></span><span></span></span>${c.b ? "Writing" : "Thinking"} · ${secs} s</div>`;
    body = `${head}${c.thinking ? `<details class="done-steps" open><summary>thinking</summary><div class="body">${esc(c.thinking)}</div></details>` : ""}<ul class="steps">${stepList}</ul><div class="body ${c.b ? "cursor" : ""}">${md(c.b || "")}</div>`;
  } else {
    const steps = c.steps && c.steps.length ? `<details class="done-steps"><summary>${c.steps.length} step${c.steps.length > 1 ? "s" : ""}${c.thinking ? " · thinking" : ""}</summary>${c.thinking ? `<div class="body">${esc(c.thinking)}</div>` : ""}<ul class="steps">${stepList}</ul></details>` : "";
    body = `${c.h ? `<h5>${esc(c.h)}</h5>` : ""}${steps}<div class="body">${md(c.b || "")}</div>`;
    if (c.k === "ok" && c.commit && !c.res) body += `<div class="paths">${(c.paths || []).map(([path, on, note]) =>
      `<label><input type="checkbox" value="${esc(path)}" ${on ? "checked" : ""}> <span class="mono">${esc(path)}</span>${note ? ` <span class="badge">${esc(note)}</span>` : ""}</label>`).join("")}</div>
      <textarea class="msg">${esc(c.msg || "")}</textarea>`;
    if (c.k === "ok") body += c.res ? `<div class="by">${esc(c.res)}</div>` : (c.buttons || []).length ?
      `<div class="okrow">${c.buttons.map(([v, t], i) => `<button class="btn ${i ? "" : "pri"}" data-ok="${esc(c.id)}§${esc(v)}">${esc(t)}</button>`).join("")}</div>` : "";
  }
  const share = c.k === "q" && CLAUDE && owner() ? `<button data-share="${esc(c.pid)}§${esc(c.id)}§${c.shared ? "" : "1"}" title="shared threads go to the hosted site, read-only, once that export exists">${c.shared ? "✓ shared" : "share"}</button>` : "";
  const foot = c.k === "ok" || c.live ? "" : `<div class="by">${esc(c.by || "")}${CLAUDE && owner() ? `<button data-reply="${esc(c.pid)}§${esc(c.id)}">↳ follow up here</button>` : ""}${share}</div>`;
  return `<div class="card ${c.k} ${c.live ? "livecard" : ""}" data-card="${esc(c.id)}"><span class="kind ${c.k}">${KIND[c.k]}</span>${c.k === "q" ? `<h5>${esc(c.h)}</h5>` : body}${foot}</div>`;
}
function threadHTML(pid) {
  const qs = thread(pid);
  const walk = (par, d) => qs.filter(q => (q.par || "plot") === par).map(q => `<div class="qa" style="--d:${Math.min(d, 4)}">${q.edge ? `<div class="qa-edge">${esc(q.edge)}</div>` : ""}${cardHTML(q)}</div>` + walk(q.id, d + 1)).join("");
  return walk("plot", 0);
}
function askBar() {
  const c = focused();
  if (!CLAUDE || !owner() || !c) return "";
  const pid = c.pid, to = S.replyTo === "plot" ? "the plot" : (thread(pid).find(q => q.id === S.replyTo) || {}).h || "the plot";
  return `<div class="askbar"><div class="ctx">Asking about <b>${esc(label(pid, c.v))}</b> · follow-up to <b>${esc(to)}</b>${S.replyTo !== "plot" ? '<button class="btn" data-reply="reset">reset</button>' : ""}</div>
    <textarea id="q" placeholder="Ask Claude about this plot…"></textarea>
    <div class="askrow"><button class="btn pri" id="send" title="Enter: queued behind any run in progress">Ask ⏎</button><button class="btn" id="sendnow" title="start at once, beside any run in progress">Ask now</button><span class="hint">Opus · full agent · edits need your OK</span></div></div>`;
}

/* ---------- the cards: a grid that fits the room; the wheel zooms the plot under the mouse ---------- */
function plotCard(c) {
  const p = P[c.pid], f = fileOf(c), sel = S.cards.length > 1 && c.id === focused().id;
  const split = Object.keys(p.files).length > 1 ? `<button class="btn" data-split="${c.id}" title="open this plot again beside this card, to set another option">⇆ split</button>` : "";
  return `<div class="pc ${sel ? "sel" : ""}" data-node="${c.id}"><div class="pc-head"><span class="kind">${p.kind === "table" ? "TABLE" : "PLOT"}</span><b title="${esc(title(c.pid))}">${esc(title(c.pid))}</b>${impHTML(c.pid)}${p.archived ? '<span class="badge">archived</span>' : ""}${live(c.pid) ? '<span class="live"></span>' : ""}${split}<button class="x" data-close="${c.id}" title="close this card">×</button></div>
    ${p.axes.length ? `<div class="pc-opt">${esc(label(c.pid, c.v))}</div>` : ""}<div class="pv" data-pv="${c.id}">${view(f)}</div></div>`;
}
// a card's zoom {x, y, k}: the image scaled by k from its top left and moved by x, y, kept inside its box
function zoomTo(pv, z) {
  const W = pv.clientWidth, H = pv.clientHeight;
  z.x = Math.min(0, Math.max(W * (1 - z.k), z.x)); z.y = Math.min(0, Math.max(H * (1 - z.k), z.y));
  const im = pv.querySelector("img"); if (im) im.style.transform = `translate(${z.x}px,${z.y}px) scale(${z.k})`;
  pv.classList.toggle("zoomed", z.k > 1);
}
document.addEventListener("wheel", e => {
  const pv = e.target.closest(".pv"), c = pv && cardOf(pv.dataset.pv);
  if (!c || !pv.querySelector("img")) return;  // a table scrolls itself
  e.preventDefault();
  const z = c.z || (c.z = { x: 0, y: 0, k: 1 }), r = pv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.min(12, Math.max(1, z.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015))));  // ctrlKey: a trackpad pinch
  z.x = mx - (mx - z.x) * k / z.k; z.y = my - (my - z.y) * k / z.k; z.k = k; zoomTo(pv, z);
}, { passive: false });
let pan = null;
document.addEventListener("pointerdown", e => {
  const pv = e.target.closest(".pv"), c = pv && cardOf(pv.dataset.pv);
  if (!c || !c.z || c.z.k <= 1 || !pv.querySelector("img")) return;
  pan = { pv, z: c.z, x: e.clientX - c.z.x, y: e.clientY - c.z.y }; pv.setPointerCapture(e.pointerId); pv.classList.add("drag");
});
document.addEventListener("pointermove", e => { if (pan) { pan.z.x = e.clientX - pan.x; pan.z.y = e.clientY - pan.y; zoomTo(pan.pv, pan.z); } });
document.addEventListener("pointerup", () => { if (pan) { pan.pv.classList.remove("drag"); pan = null; } });
document.addEventListener("dblclick", e => { const pv = e.target.closest(".pv"), c = pv && cardOf(pv.dataset.pv); if (c && c.z) { c.z = { x: 0, y: 0, k: 1 }; zoomTo(pv, c.z); } });

/* ---------- the tree: groups fold; a plot is a row with a box that opens it beside the others ---------- */
// The owner's tags (.room/tags.tsv): one colour per tag from a fixed set of hues; a click shows that tag's plots only
const HUES = [210, 25, 140, 285, 350, 48, 175, 95];
const hue = t => HUES[[...t].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 9973, 7) % HUES.length];
const tagChip = (t, x = "") => `<span class="tag ${S.tag === t ? "on" : ""}" style="--h:${hue(t)}" data-tag="${esc(t)}" title="${S.tag === t ? "show every plot" : "show only " + esc(t)}">${esc(t)}${x}</span>`;
const tagsHTML = pid => (P[pid].tags || []).map(t => tagChip(t)).join("");
// a small picture of the plot's main file on its row, and that picture large beside the tree while the row is hovered
// (10-20); ponytail: the thumb loads the card's full-size PNG, a smaller rendering if the tree gets slow
const imgOf = pid => { const f = P[pid].files[keyOf(pid, DEF[pid] || best(pid))]; return f && f.img; };
const thumb = pid => imgOf(pid) ? `<img class="thumb" src="${esc(imgOf(pid))}" alt="" loading="lazy">` : "";
const impHTML = pid => P[pid].importance ? `<span class="badge imp" title="importance to its task">${P[pid].importance}</span>` : "";
function badges(pid) {
  const n = Object.keys(P[pid].files).length, q = thread(pid).length;
  return `${impHTML(pid)}${live(pid) ? '<span class="live"></span>' : ""}${P[pid].archived ? '<span class="badge">archived</span>' : ""}${n > 1 ? `<span class="badge" title="files: options and versions">${n}</span>` : ""}${owner() && q ? `<span class="badge qa">${q} Q&A</span>` : ""}`;
}
// "newest": plots by their newest file's date, newest first, and groups by their newest plot; "name": as the index has them
const sorted = ps => S.order === "date" ? ps.slice().sort((a, b) => P[b].updated.localeCompare(P[a].updated)) : ps;
const newest = g => g.plots.filter(pid => P[pid] && shown(pid)).reduce((m, pid) => P[pid].updated > m ? P[pid].updated : m, "");
function treeNav() {
  const f = focused();
  const gs = groupsNow();
  if (S.order === "date") gs.sort((a, b) => newest(b).localeCompare(newest(a)));
  return `<div class="tree">` + gs.map(g => {
    const ps = sorted(g.plots.filter(pid => P[pid] && shown(pid))); if (!ps.length) return "";
    const open = S.open["g:" + g.id] ?? (!g.closed || ps.some(isOpen));
    // the name on a line of its own, its tags and counts under it, so a narrow tree never squeezes it (11-12)
    const leaves = qs => qs.map(pid => { const m = tagsHTML(pid) + badges(pid);
      return `<div class="leaf ${f && f.pid === pid ? "on" : ""} ${P[pid].archived ? "arch" : ""}"><input type="checkbox" data-tick="${esc(pid)}" ${isOpen(pid) ? "checked" : ""} title="show beside the open cards">${thumb(pid)}<div class="lb"><span class="ttl" data-sel="${esc(pid)}" title="${esc(title(pid))}">${esc(title(pid))}</span>${m ? `<div class="lm">${m}</div>` : ""}</div></div>`; }).join("");
    const kids = g.subs && S.bytask ? g.subs.map(x => { const qs = ps.filter(pid => x.plots.includes(pid)); if (!qs.length) return "";
      const k = `g:${g.id}|${x.t}`; return `<details class="grp sub" data-open="${esc(k)}" ${S.open[k] ?? true ? "open" : ""}><summary><span class="ttl">${esc(x.t)}</span><span class="badge">${qs.length}</span></summary><div class="kids">${leaves(qs)}</div></details>`; }).join("") : leaves(ps);
    return `<details class="grp" data-open="g:${esc(g.id)}" ${open ? "open" : ""}><summary><span class="ttl">${esc(g.t)}</span><span class="badge">${ps.length}</span></summary><div class="kids">${kids}</div></details>`;
  }).join("") + `</div>`;
}
function navPane() {
  const pin = `<button class="pin" data-min="navmin" title="${S.navmin ? "keep the list open" : "fold the list to the edge; it opens while the mouse is on it"}">${S.navmin ? "»" : "«"}</button>`;
  const grp = I.hosted ? "" : `<select id="grp"><option value="script" ${S.group === "script" ? "selected" : ""}>by script</option><option value="read" ${S.group === "read" ? "selected" : ""}>by read date</option></select>`;
  const ord = `<select id="order"><option value="name" ${S.order === "name" ? "selected" : ""}>by name</option><option value="date" ${S.order === "date" ? "selected" : ""}>newest first</option></select>`;
  const bytask = S.group === "read" && (I.tasks || []).length ? `<label class="bytask" title="split each sitting by the tasks a meeting set"><input type="checkbox" id="bytask" ${S.bytask ? "checked" : ""}> by task</label>` : "";
  return `<h4>${pin}Plots${bytask}${grp}${ord}</h4>${S.tag ? `<div class="tagbar">showing ${tagChip(S.tag, " ×")}</div>` : ""}${treeNav()}`;
}

/* ---------- render ---------- */
function seg(name, opts) { return `<span class="seg">` + opts.map(([v, t]) => `<button data-set="${name}|${v}" class="${S[name] === v ? "on" : ""}">${t}</button>`).join("") + `</span>`; }
function pill() {
  if (!CLAUDE || !owner()) return "";
  const c = S.claude, secs = c.t0 ? Math.round((Date.now() - c.t0) / 1000) : 0;
  const txt = { idle: "Claude · idle", thinking: `Thinking · ${secs} s`, writing: `Writing · ${secs} s`, waiting: "Needs your OK", done: `Answered · ${secs} s` }[c.st];
  const q = c.running > 1 || c.queued ? ` · ${c.running || 0} running, ${c.queued || 0} queued` : "";
  return `<span class="pill ${c.st}" id="pill" title="jump to the plot Claude is working on"><span class="lamp"></span>${txt}${q}${c.plot && c.st !== "idle" && P[c.plot] ? ` · <span style="color:var(--muted)">${esc(title(c.plot).slice(0, 28))}…</span>` : ""}</span>`;
}
const SCROLL = [".pane.nav", ".pane.side", ".cards"];  // kept across a redraw
function render() {
  const typed = document.getElementById("q"), qv = typed ? typed.value : "", qf = typed && document.activeElement === typed;
  const at = SCROLL.map(s => { const el = document.querySelector(s); return el ? el.scrollTop : 0; });
  draw();
  SCROLL.forEach((s, i) => { const el = document.querySelector(s); if (el) el.scrollTop = at[i]; });
  const q = document.getElementById("q");
  if (q && qv) { q.value = qv; }
  if (q && qf) q.focus();
}
function draw() {
  document.documentElement.dataset.theme = S.theme;
  document.getElementById("viewbar").innerHTML = `<span>Show ${seg("kind", [["figure", "Figures"], ["table", "Tables"], ["all", "All"]])}</span>
    ${I.hosted ? "" : `<span>View as ${seg("viewer", [["owner", "Owner"], ["collab", "Collaborator"]])}</span>`}<span>${seg("theme", [["light", "Light"], ["dark", "Dark"]])}</span>
    <span class="spacer"></span><span>index read ${esc(I.read)}</span>`;
  document.getElementById("top").innerHTML = `<span class="brand">${esc(I.title)}<small>control room</small></span>
    <input class="filter" id="filter" placeholder="filter plots…" value="${esc(S.filter)}">${filterMenu()}<span class="spacer"></span>${pill()}`;
  document.getElementById("bannerslot").innerHTML = owner() || I.hosted ? "" : `<div class="banner">Collaborator preview: what collaborators would see once this is on the hosted site. No Claude, no Q&A, no script names.</div>`;
  const m = document.getElementById("main"), n = S.cards.length;
  m.className = [S.navmin ? "navmin" : "", S.sidemin ? "sidemin" : ""].join(" ");
  m.style.setProperty("--navw", S.navw + "px"); m.style.setProperty("--sidew", S.sidew + "px");
  // the cards fill the room column by column, two to a column: the second under the first, the third top right, the
  // fourth bottom right (11-12); a fifth would open a third column
  m.innerHTML = `<div class="pane nav ${S.navmin ? "min" : ""}">${navPane()}</div><div class="grip l" data-grip="navw" title="drag to resize"></div>
    <div class="room">${n ? `<div class="cards" style="--rows:${n > 1 ? 2 : 1}">${S.cards.map(plotCard).join("")}</div>` : '<div class="empty">Tick a plot in the tree to open it.</div>'}</div>
    <div class="grip r" data-grip="sidew" title="drag to resize"></div><aside class="pane side ${S.sidemin ? "min" : ""}">${sideHTML()}</aside>`;
  document.querySelectorAll(".pv[data-pv]").forEach(pv => { const c = cardOf(pv.dataset.pv); if (c && c.z) zoomTo(pv, c.z); });
  fillTables();
}

/* ---------- events ---------- */
function only(pid) {  // a click on a plot's name: that plot alone (the boxes add plots beside it)
  const keep = S.cards.find(c => c.pid === pid);
  S.cards = [keep || newCard(pid)]; S.focus = S.cards[0].id; S.replyTo = "plot"; render();
}
function tick(pid, on) {
  if (on) { const c = newCard(pid); S.cards.push(c); S.focus = c.id; }
  else { S.cards = S.cards.filter(c => c.pid !== pid); }
  S.replyTo = "plot"; render();
}
function split(c) {  // a second card of the same plot, at the next combination along its first axis that exists
  const p = P[c.pid], [a, vals] = p.axes[0], i = vals.indexOf(c.v[a]);
  const next = vals.slice(i + 1).concat(vals.slice(0, i)).map(v => ({ ...c.v, [a]: v })).find(o => p.files[keyOf(c.pid, o)]) || combos(c.pid).find(o => keyOf(c.pid, o) !== keyOf(c.pid, c.v));
  const n = newCard(c.pid, next || c.v); S.cards.splice(S.cards.indexOf(c) + 1, 0, n); S.focus = n.id; render();
}
let popT = 0;
const pop = () => document.getElementById("pop") || document.body.appendChild(Object.assign(document.createElement("img"), { id: "pop", alt: "" }));
const unpop = () => { clearTimeout(popT); pop().style.display = "none"; };
document.addEventListener("mouseover", e => {
  const leaf = e.target.closest(".leaf"), im = leaf && leaf.querySelector(".thumb");
  if (!im) return unpop();
  if (e.relatedTarget && leaf.contains(e.relatedTarget)) return;
  clearTimeout(popT);
  popT = setTimeout(() => { const p = pop(), r = leaf.getBoundingClientRect(), nav = leaf.closest(".tree").getBoundingClientRect();
    const place = () => { p.style.top = Math.max(8, Math.min(r.top - 40, innerHeight - p.offsetHeight - 8)) + "px"; };
    p.onload = place; p.src = im.src; p.style.display = "block"; p.style.left = nav.right + 10 + "px"; place(); }, 200);
});
document.addEventListener("mouseleave", unpop);
addEventListener("scroll", unpop, true);
document.addEventListener("click", e => {
  unpop();
  if (S.open.fmenu && !e.target.closest(".fmenu")) { S.open.fmenu = false; const m = document.querySelector(".fmenu"); if (m) m.open = false; }  // a click elsewhere folds the filter menu
  const t = e.target.closest("[data-set],[data-sel],[data-axis],[data-reply],[data-ok],[data-share],[data-split],[data-close],[data-archive],[data-imp],[data-tag],[data-min],#freset,#send,#sendnow,#pill");
  if (!t) { const n = e.target.closest(".pc[data-node]"); if (n && S.focus !== n.dataset.node) { S.focus = n.dataset.node; S.replyTo = "plot"; render(); } return; }
  if (t.dataset.share && window.agent) { const [pid, id, on] = t.dataset.share.split("§"); window.agent.share(pid, id, !!on); return; }
  if (t.dataset.set) { const [k, v] = t.dataset.set.split("|"); S[k] = v; if (PERSIST.includes(k)) store.set(k, v); render(); }
  else if (t.dataset.axis) { const [id, a, v] = t.dataset.axis.split("|"); setAxis(cardOf(id), a, v); }
  else if (t.dataset.sel) only(t.dataset.sel);
  else if (t.dataset.split) split(cardOf(t.dataset.split));
  else if (t.dataset.close) { S.cards = S.cards.filter(c => c.id !== t.dataset.close); if (!cardOf(S.focus)) S.focus = S.cards.length ? S.cards[0].id : null; render(); }
  else if (t.dataset.min) { const k = t.dataset.min; S[k] = !S[k]; store.set(k, S[k] ? "1" : "0"); render(); }
  else if (t.dataset.reply) { if (t.dataset.reply === "reset") S.replyTo = "plot";
    else { const [pid, id] = t.dataset.reply.split("§"); S.focus = S.cards.find(c => c.pid === pid).id; S.replyTo = id; }
    render(); const q = document.getElementById("q"); if (q) q.focus(); }
  else if (t.dataset.tag) { S.tag = S.tag === t.dataset.tag ? "" : t.dataset.tag; render(); }
  else if (t.dataset.imp) { const pid = t.dataset.imp, M = ["", "!", "!!", "!!!"], level = M[(M.indexOf(P[pid].importance || "") + 1) % 4];
    post("api/importance", { ids: P[pid].members || [pid], level }).then(r => { if (r.ok) { P[pid].importance = level; render(); } }); }
  else if (t.dataset.archive) { const pid = t.dataset.archive, on = !P[pid].archived;  // every member, so a later page brings the family back
    post("api/archive", { ids: P[pid].members || [pid], on }).then(r => { if (r.ok) { P[pid].archived = on; render(); } }); }
  else if (t.id === "freset") { S.arch = S.old = false; FILTERS.forEach(([k]) => { S[k] = ""; }); store.set("ptype", ""); render(); }
  else if (t.dataset.ok && window.agent) { const [id, v] = t.dataset.ok.split("§"); window.agent.approve(id, v); }
  else if ((t.id === "send" || t.id === "sendnow") && window.agent) { const q = document.getElementById("q"); if (q && q.value.trim()) window.agent.ask(focused(), S.replyTo, q.value.trim(), t.id === "sendnow"); }
  else if (t.id === "pill" && S.claude.plot && P[S.claude.plot]) only(S.claude.plot);
});
document.addEventListener("toggle", e => { const d = e.target; if (d.dataset && d.dataset.open) { S.open[d.dataset.open] = d.open;
  if (d.dataset.open.startsWith("sec:")) store.set(d.dataset.open, d.open ? "1" : "0"); } }, true);
document.addEventListener("change", e => {
  if (e.target.id === "bytask") { S.bytask = e.target.checked; store.set("bytask", S.bytask ? "1" : "0"); render(); }
  if (e.target.id === "grp") { S.group = e.target.value; store.set("group2", S.group); render(); }
  if (e.target.dataset.filt) { S[e.target.dataset.filt] = e.target.value; if (e.target.dataset.filt === "ptype") store.set("ptype", S.ptype); render(); }
  if (e.target.id === "farch") { S.arch = e.target.checked; render(); }
  if (e.target.id === "fold") { S.old = e.target.checked; render(); }
  if (e.target.id === "order") { S.order = e.target.value; store.set("order", S.order); render(); }
  if (e.target.dataset.tick) tick(e.target.dataset.tick, e.target.checked);
});
document.addEventListener("input", e => { if (e.target.id === "filter") { S.filter = e.target.value; const at = e.target.selectionStart; render();
  const f = document.getElementById("filter"); f.focus(); f.setSelectionRange(at, at); } });
document.addEventListener("keydown", e => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") {
    if (e.key === "Enter" && !e.shiftKey && e.target.id === "q") { e.preventDefault(); document.getElementById("send").click(); } return; }
  const c = focused(); if (!c || !P[c.pid].axes.length || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
  const p = P[c.pid], [a, vals] = p.axes[0], avail = vals.filter(v => p.files[keyOf(c.pid, { ...c.v, [a]: v })]);
  const i = avail.indexOf(c.v[a]); setAxis(c, a, avail[(i + (e.key === "ArrowRight" ? 1 : -1) + avail.length) % avail.length]);
});
window.addEventListener("resize", () => document.querySelectorAll(".pv[data-pv]").forEach(pv => { const c = cardOf(pv.dataset.pv); if (c && c.z) zoomTo(pv, c.z); }));
document.addEventListener("pointerdown", e => {  // a grip beside a side panel sets its width; kept per browser
  const g = e.target.dataset && e.target.dataset.grip; if (!g) return;
  const m = document.getElementById("main"), x0 = e.clientX, w0 = S[g], dir = g === "navw" ? 1 : -1;
  const move = ev => { S[g] = Math.max(180, Math.min(window.innerWidth * 0.6, w0 + dir * (ev.clientX - x0))); m.style.setProperty("--" + g, S[g] + "px"); };
  const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); store.set(g, Math.round(S[g])); };
  addEventListener("pointermove", move); addEventListener("pointerup", up); e.preventDefault();
});

/* ---------- the Claude fold: only when api/hello answered (room/agent.py behind dashboard.py --claude) ---------- */
const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json());
function upsert(pid, c) { c.pid = pid; const cs = T[pid] = T[pid] || []; const i = cs.findIndex(x => x.id === c.id); if (i >= 0) cs[i] = c; else cs.push(c); }
function patch(c) {  // a card already on screen changes in place; a new card needs a full render
  const els = document.querySelectorAll(`[data-card="${CSS.escape(c.id)}"]`);
  if (!els.length) return false;
  els.forEach(el => { el.outerHTML = cardHTML(c); });
  return true;
}
function repill() { const p = document.getElementById("pill"); if (p) p.outerHTML = pill(); }
window.agentStart = async () => {
  const d = await (await fetch("api/threads")).json();
  Object.entries(d.cards).forEach(([pid, cs]) => cs.forEach(c => upsert(pid, c)));
  Object.assign(S.claude, d.status);
  render();
  const es = new EventSource("api/events");
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.t === "card") { upsert(ev.plot, ev.card); if (!patch(ev.card)) render(); }
    if (ev.t === "status") { const was = S.claude.st; Object.assign(S.claude, ev); if ((was === "idle") !== (ev.st === "idle") || ev.st === "done") render(); else repill(); }
  };
  setInterval(() => { if (!["idle", "done"].includes(S.claude.st)) { repill(); Object.values(T).flat().filter(c => c.live).forEach(patch); } }, 1000);
};
window.agent = {
  ask(c, parent, text, now) {
    const pid = c.pid, f = fileOf(c);
    post("api/ask", { plot: pid, parent, text, now, ctx: { title: P[pid].title, variant: label(pid, c.v), path: f.path, script: P[pid].script,
      caption: f.caption_txt ? f.path.replace(/\.[^.]+$/, ".txt") : "" } });
    document.getElementById("q").value = ""; S.replyTo = "plot";
  },
  approve(id, v) {
    const el = document.querySelector(`[data-card="${CSS.escape(id)}"]`), msg = el && el.querySelector("textarea.msg");
    const choice = msg ? { do: v, msg: msg.value, paths: [...el.querySelectorAll("input[type=checkbox]:checked")].map(x => x.value) } : v;
    post("api/approve", { id, choice });
  },
  share(pid, id, on) { post("api/share", { plot: pid, id, on }); },
};

/* ---------- start ---------- */
(async () => {
  I = await (await fetch("index.json")).json();
  P = I.plots;
  I.findings.forEach(f => f.plots.forEach(([pid, k]) => { if (P[pid] && P[pid].files[k] && !DEF[pid]) DEF[pid] = parse(pid, k); INF.add(pid); }));
  // the room opens empty, nothing ticked, only the newest sitting unfolded (10-21)
  // the hosted copy (dashboard.py hosted()): the collaborator's view, everything but Claude (10-20); Pages answers any
  // unknown path, api/hello included, with its root page, so the probe would say yes; it has no scripts, so by read date
  if (I.hosted) Object.assign(S, { viewer: "collab", group: "read" });
  else try { CLAUDE = (await fetch("api/hello")).ok; } catch (e) { CLAUDE = false; }
  render();
  if (CLAUDE && window.agentStart) window.agentStart();
})();
