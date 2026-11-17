// The card-text renderer (app.js md): a [text](?open=id) link renders; any other address stays text. Run from the repo root.
import { readFileSync } from "fs";
const src = readFileSync("room/site/app/app.js", "utf8");
const escL = src.split("\n").find(l => l.startsWith("const esc = "));
const i = src.indexOf("const md = s =>"), j = src.indexOf(";\n", i);
const { esc, md } = new Function(escL + "\n" + src.slice(i, j + 1) + "\nreturn { esc, md };")();
const a = md("see [the table](?open=tables%2F110_x.csv) and **b**");
const b = md('[x](https://evil.com) [y](?open=a" onclick="z)');
console.log(a); console.log(b);
if (!a.includes('<a href="?open=tables%2F110_x.csv" data-goto="tables%2F110_x.csv">the table</a>')) process.exit(1);
if (b.includes("<a")) process.exit(2);
console.log("md link check ok");
