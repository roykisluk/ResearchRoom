// The Brief mode's choices (app.js briefOf, briefOrder, briefTid): which point a ?brief= address opens, the order the
// points list in, and which thread a question goes to. Run from the repo root.
import { readFileSync } from "fs";
const src = readFileSync("room/site/app/app.js", "utf8").split("\n");
const line = p => src.find(l => l.startsWith(p)) || (console.error("missing in app.js: " + p), process.exit(1));
const { briefOf, briefOrder, briefTid } = new Function(["const briefOrder = pts =>", "const briefOf = (qs, tk) =>", "const briefTid = (b, role, pid) =>"]
  .map(line).join("\n") + "\nreturn { briefOf, briefOrder, briefTid };")();
const eq = (a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.error("got", JSON.stringify(a), "want", JSON.stringify(b)); process.exit(1); } };
const tk = { "2026-10-20": [{ n: 1, ask: { t: "a", link: "" } }, { n: 2, ask: null }, { n: 3, ask: { t: "", link: "" } }], "2026-10-21": [] };
eq(briefOrder(tk["2026-10-20"]).map(x => x.n), [1, 3, 2]);
eq(briefOf("?brief=2026-10-20", tk), { date: "2026-10-20", n: 1 });
eq(briefOf("?brief=2026-10-20&point=2", tk), { date: "2026-10-20", n: 2 });
eq(briefOf("?brief=2026-10-20&point=9", tk), { date: "2026-10-20", n: 3 });
eq(briefOf("?brief=2026-10-20&point=1.5", tk), { date: "2026-10-20", n: 2 });   // a point that is no whole number: the nearest
eq(briefOf("?brief=2026-10-20&point=x", tk), { date: "2026-10-20", n: 1 });
eq(briefOf("?brief=2026-01-01", tk), null);     // no brief file for that date
eq(briefOf("?brief=2026-10-21", tk), null);     // a file with no points
eq(briefOf("?open=x", tk), null);
eq(briefOf("?brief=2026-10-20", undefined), null);   // an index from before the Brief mode
eq(briefTid({ date: "2026-10-20", n: 2 }, "owner", "p"), "sit:2026-10-20:2");
eq(briefTid({ date: "2026-10-20", n: 2 }, "reader", "p"), "p");
eq(briefTid(null, "owner", "p"), "p");
console.log("brief check ok");
