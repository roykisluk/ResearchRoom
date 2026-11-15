// The shared site's Claude Q&A: forward /api/* to the project's room on its server, through Access with a service token,
// carrying the reader's own Access login so the room can tell who asks (the room verifies it; nothing here names a
// user). A template: site_files() fills in QA from [hosted] qa. The secrets QA_CLIENT_ID and QA_CLIENT_SECRET are the
// Pages project's. _middleware.js runs first: only the listed hosts, only requests that came through Access.
const QA = __QA__;
const json = (o, status) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });

export async function onRequest({ request, env, params }) {
  const url = new URL(request.url);
  // the site's own page only: a form or a script on another site cannot ask in a reader's name
  if (request.method !== "GET" && request.headers.get("Origin") !== url.origin) return json({ ok: false }, 403);
  const headers = new Headers({
    "CF-Access-Client-Id": env.QA_CLIENT_ID || "", "CF-Access-Client-Secret": env.QA_CLIENT_SECRET || "",
    "X-Room-Jwt": request.headers.get("Cf-Access-Jwt-Assertion") || "", "Origin": QA,
    "Content-Type": request.headers.get("Content-Type") || "application/json",
  });
  let res;
  try {
    res = await fetch(`${QA}/api/${[].concat(params.path || []).join("/")}${url.search}`, {
      method: request.method, headers, redirect: "manual", body: request.method === "GET" ? undefined : await request.arrayBuffer() });
  } catch (e) {
    return json({ ok: false, offline: true }, 503);  // the server is down: the dashboard works, the fold says so
  }
  // a redirect is Access's login page: the service token is missing or wrong, so to the page the room is offline
  if (res.status >= 300 && res.status < 400) return json({ ok: false, offline: true }, 503);
  // only these headers go back, so no cookie the room's Access sets ever reaches a browser; the event stream streams
  return new Response(res.body, { status: res.status, headers: {
    "Content-Type": res.headers.get("Content-Type") || "application/json", "Cache-Control": "no-cache" } });
}
