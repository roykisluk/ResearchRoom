// The card's "compare versions" (app.js prevV): which older version ⇆ vK and ◐ compare a card with. Run from the repo root.
import { readFileSync } from "fs";
const src = readFileSync("room/site/app/app.js", "utf8").split("\n");
const p0 = "const prevV = (p, v) =>", line = src.find(l => l.startsWith(p0)) || (console.error("missing in app.js: " + p0), process.exit(1));
const prevV = new Function(line + "\nreturn prevV;")();
const eq = (a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.error("got", JSON.stringify(a), "want", JSON.stringify(b)); process.exit(1); } };
const p = { axes: [["outcome", ["wage", "hours"]], ["version", ["v3", "v2", "v1"]]],
  files: { "wage|v3": {}, "hours|v3": {}, "wage|v2": {}, "hours|v1": {}, "wage|v1": {} } };
eq(prevV(p, { outcome: "wage", version: "v3" }), { outcome: "wage", version: "v2" });   // same option, one version back
eq(prevV(p, { outcome: "hours", version: "v3" }), { outcome: "wage", version: "v2" });  // v2 lacks hours: v2's first file
eq(prevV(p, { outcome: "wage", version: "v1" }), null);                                  // the oldest
eq(prevV({ axes: [["outcome", ["wage"]]], files: { wage: {} } }, { outcome: "wage" }), null);  // no version axis
console.log("compare check ok");
