// Fail closed: serve only the hostnames the project's Access app gates, and only requests that came through Access.
// Every deployment also gets <hash>.<project>.pages.dev, which Access does not cover. A template: site_files() fills in the hosts.
const HOSTS = new Set(__HOSTS__);
// Text outputs go out as plain text, as dashboard.py's TEXT does locally: Pages sends .tex as application/x-tex and
// .csv as text/csv, which the browser downloads, so the dashboard's frame for F6's .tex exhibit saved the file on load.
const ICONS = new Set(["/favicon.ico", "/apple-touch-icon.png"]);
const TEXT = /^\/f\/.+\.(md|csv|tex|txt|log)$/i;

export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  if (!HOSTS.has(url.hostname)) return new Response("not found", { status: 404 });
  // the two icon files are public, as an Access Bypass app also makes them: Safari fetches bookmark icons with no login
  if (ICONS.has(url.pathname)) return next();
  if (!request.headers.get("Cf-Access-Jwt-Assertion")) return new Response("forbidden", { status: 403 });
  const res = await next();
  if (!TEXT.test(url.pathname) || !res.ok) return res;
  const out = new Response(res.body, res);
  out.headers.set("Content-Type", "text/plain; charset=utf-8");
  return out;
}
