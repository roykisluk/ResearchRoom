// room/site/qa.js, the shared site's Q&A forwarder, against a stub fetch: what it sends to the room and what reaches the
// browser. Run: node tools/qa-function-test.mjs (pre-push runs it when node is installed).
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const src = readFileSync(new URL("../room/site/qa.js", import.meta.url), "utf8").replace("__QA__", '"https://room.example.org"');
const file = join(mkdtempSync(join(tmpdir(), "qa-")), "qa.mjs");
writeFileSync(file, src);
const { onRequest } = await import(file);
let fails = 0, sent = null, reply = () => new Response('{"ok":true}', { headers: { "Content-Type": "application/json", "Set-Cookie": "CF_Authorization=svc" } });
globalThis.fetch = async (url, init) => { sent = { url, ...init, headers: new Headers(init.headers) }; return reply(); };
const ok = (cond, what) => { console.log((cond ? "PASS " : "FAIL ") + what); if (!cond) fails = 1; };
const env = { QA_CLIENT_ID: "id1", QA_CLIENT_SECRET: "s3cret" };
const req = (method, path, h = {}) => new Request(`https://site.example.org/api/${path}?x=1`, {
  method, headers: { "Cf-Access-Jwt-Assertion": "reader.jwt", ...h }, body: method === "GET" ? undefined : "{}" });

let r = await onRequest({ request: req("GET", "threads", { "X-Room-User": "owner@example.org" }), env, params: { path: ["threads"] } });
ok(sent.url === "https://room.example.org/api/threads?x=1", "forwards the path and query to the room");
ok(sent.headers.get("X-Room-Jwt") === "reader.jwt" && sent.headers.get("CF-Access-Client-Id") === "id1", "sends the reader's own login and the service token");
ok(!sent.headers.has("X-Room-User"), "a header the browser sent naming a user is not passed on");
ok(r.status === 200 && !r.headers.has("Set-Cookie"), "no cookie from the room's Access reaches the browser");
sent = null;
r = await onRequest({ request: req("POST", "ask", { Origin: "https://evil.example.org" }), env, params: { path: ["ask"] } });
ok(r.status === 403 && sent === null, "a POST from another site is refused before it reaches the room");
r = await onRequest({ request: req("POST", "ask", { Origin: "https://site.example.org" }), env, params: { path: ["ask"] } });
ok(r.status === 200 && sent.method === "POST" && sent.headers.get("Origin") === "https://room.example.org", "a POST from the site's own page goes through");
reply = () => { throw new TypeError("connection refused"); };
r = await onRequest({ request: req("GET", "hello"), env, params: { path: ["hello"] } });
ok(r.status === 503 && (await r.json()).offline === true, "the room down: 503, offline");
reply = () => new Response("", { status: 302, headers: { Location: "https://team.cloudflareaccess.com/login" } });
r = await onRequest({ request: req("GET", "hello"), env, params: { path: ["hello"] } });
ok(r.status === 503, "Access's login page (a wrong token): offline, not a redirect for the browser");
process.exit(fails);
