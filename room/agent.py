"""Claude in the control room (plan docs/plans/2026-10-15-control-room.md §1.4-1.7): the owner asks about a plot at /room,
a full Claude Code agent answers through the Python Agent SDK, and the answer becomes cards saved as JSON Canvas in
docs/qa/<plot>.canvas (Obsidian opens them; editing one there can drop the x-card keys the dashboard reads).

Behind `room/dashboard.py --claude` (127.0.0.1, a per-launch token cookie, Host and Origin checks there): on the Mac,
and on a server through a tunnel for the owner's Access login only (`--public`, plan 2026-10-20-claude-tunnel).
Claude acts only on what the owner types here, never on notes or requests from the hosted site (DECISIONS 10-13). Every tool
the settings do not pre-allow becomes a "Needs you" card. `git commit` and `git push` always do, even though
~/.claude/settings.json allows Bash(git:*): a PreToolUse hook holds the command, and on the owner's OK the dashboard commits
the paths he ticked itself (only paths changed since the thread began are ticked), so other sessions' edits stay out.

    uv run --with claude-agent-sdk python room/dashboard.py --claude
    python3 room/agent.py --selftest      # card parsing and the canvas round trip, no model call
"""
import asyncio, dataclasses, json, os, queue, re, shlex, subprocess, sys, tempfile, threading, time, uuid

from project import CFG, PEOPLE, ROOT  # the project and its .room/config.toml (room/project.py)

QA = os.path.join(ROOT, "docs", "qa")
# Obsidian's canvas colours, as the owner's 2017 Q3 canvas uses them: red pitfall, green answer, cyan another way
COLOR = {"a": "4", "pit": "1", "alt": "5", "act": "6", "ok": "3"}
# git subcommands, after any global options (-C path, -c k=v, --no-pager): only read-only ones pass without a card,
# because Bash(git:*) in the owner's settings would otherwise let reset --hard, checkout --, stash or clean wipe other
# sessions' uncommitted work; commit and push get the commit card
GIT_SUB = re.compile(r"\bgit((?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*)\s+([a-z][\w-]*)")
GIT_READ = {"status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "ls-tree", "grep", "shortlog", "describe", "cat-file"}


def git_subs(cmd):
    return [m.group(2) for m in GIT_SUB.finditer(cmd)]
MODELS, EFFORTS = ("opus", "sonnet"), ("low", "medium", "high")  # the ask bar's choices; the first model, medium effort by default
OWNER = PEOPLE[0] if PEOPLE else "Owner"    # named on the question cards
WHO = PEOPLE[0] if PEOPLE else "the owner"  # named in the prompts
SYS = f"""You are answering inside {WHO}'s research control room (the dashboard at /room), not in a terminal session.
- Answer the question about the plot directly. No session preamble, no session log, no skills unless {WHO} asks for one.
- First lines of your final reply: `Question: <{WHO}'s question with spelling, capitals and grammar fixed and phrased clearly; the same meaning, names and numbers>`, then `Label: <2-4 words>` (the edge label on the canvas), then `Title: <the answer in one line>`. Then the answer, short and in plain words.
- Answer from the project's files, and run quick checks on what is on this machine if that settles it. When the answer needs data that is not on this machine, or a new script or version, say so plainly and add a section `### Issue: <title>`: a few lines on what to compute and why. {WHO} files it as a GitHub issue from the dashboard; never file one yourself.
- When there is a real pitfall, or a second way to see it, add a section `### Pitfall: <title>` or `### Another way: <title>`, a few sentences each.
- You may read, run and edit as in a terminal session; anything the settings do not allow asks {WHO} through a card.
- Never commit or push on your own. If {WHO} asks for a commit, run `git commit -m "<message>"`: the dashboard shows {WHO} a card and commits the files they tick."""
if _pf := CFG.get("claude", {}).get("prompt_file"):  # the project's own lines, appended (.room/claude-prompt.md)
    try:
        with open(os.path.join(ROOT, _pf), encoding="utf-8") as _fh:
            SYS += "\n" + _fh.read().strip()
    except OSError as e:
        sys.exit(f"[claude] prompt_file in .room/config.toml: {e}")


READER_SYS = """You are answering {who}, a coauthor, in the project's shared dashboard, not in a terminal session.
- You can only read. The folder you are in holds what the dashboard shows (its figures, tables, captions, equations and
  findings) and the analysis code under code/. Read, Grep and Glob are your only tools, and nothing outside this folder
  can be read. Do not suggest commands for {who} to run on the server.
- First lines of your final reply: `Question: <the question with spelling, capitals and grammar fixed and phrased clearly;
  the same meaning, names and numbers>`, then `Label: <2-4 words>`, then `Title: <the answer in one line>`. Then the
  answer, short and in plain words.
- When there is a real pitfall, or a second way to see it, add a section `### Pitfall: <title>` or `### Another way:
  <title>`, a few sentences each.
- When the answer needs data or an analysis that is not in this folder, say so plainly and add a section
  `### Issue: <title>`: a few lines on what to compute and why. It can be filed as a GitHub issue from the dashboard."""


def slug(pid):
    return re.sub(r"[^A-Za-z0-9]+", "-", pid).strip("-")[:120]


EXTRA = {"Pitfall": "pit", "Another way": "alt", "Issue": "iss"}  # the reply's ### sections -> card kinds


def split_answer(text):
    """Claude's final reply -> (label, title, question, body, [(kind, title, body)]) per the SYS format; the header
    lines in any order, each one optional."""
    lines, head = text.strip().splitlines(), {}
    while lines and re.match(r"^\s*(Label|Title|Question):", lines[0]):
        k, _, v = lines.pop(0).partition(":")
        head[k.strip()] = v.strip()
    parts = re.split(r"^###\s*(Pitfall|Another way|Issue):\s*(.*)$", "\n".join(lines), flags=re.M)
    extra = [(EXTRA[parts[i]], parts[i + 1].strip(), parts[i + 2].strip()) for i in range(1, len(parts) - 2, 3)]
    return head.get("Label", ""), head.get("Title", ""), head.get("Question", ""), parts[0].strip(), extra


READ = ("Read", "Grep", "Glob")  # a coauthor's run has these tools and no other (design 11-15, shared Q&A)


def reader_gate(tool, inp, root):
    """May a coauthor's run make this call? Only Read, Grep or Glob, and only on paths that resolve (symlinks followed)
    inside root, the reader folder; Read must name its file, Grep and Glob default to root."""
    if tool not in READ or (tool == "Read" and not inp.get("file_path")):
        return False
    real = os.path.realpath(root)
    for p in [inp[k] for k in ("file_path", "path") if inp.get(k)]:
        full = os.path.realpath(os.path.join(root, os.path.expanduser(str(p))))
        if os.path.commonpath([full, real]) != real:
            return False
    return True


def session_for(cards, par_id, role):
    """A follow-up to card par_id -> (the card whose Claude session it resumes, or None; the thread's text, when it starts
    fresh). A session is resumed only within one role: a coauthor must not inherit what the owner's run saw, and the
    owner's powers must not run on a coauthor's text unread (design 11-15, shared Q&A). Across roles the new run gets the
    questions and answers on the way to par_id, as the dashboard shows them."""
    by = {c["id"]: c for c in cards}
    par = by.get(par_id)
    src = par if par is None or par.get("session") else by.get(par["par"])
    if src is None or not src.get("session"):
        return None, ""
    if src.get("role", "owner") == role:
        return src, ""
    chain, c = [], par
    while c:
        chain.append(c)
        c = by.get(c.get("par"))
    return None, "\n\n".join(f"{'Question' if c['k'] == 'q' else 'Answer'}: {c.get('h', '')}\n{c.get('b', '')}".strip()
                              for c in reversed(chain) if c["k"] in ("q", "a"))


def to_canvas(pid, path, cards):
    """Cards -> a JSON Canvas document: the plot as a file node, each card a text node, labelled edges, a tidy layout."""
    kids = {}
    for c in cards:
        kids.setdefault(c["par"], []).append(c)
    nodes, edges, row = [{"id": "plot", "type": "file", "file": path, "x": 0, "y": 0, "width": 520, "height": 420}], [], [0]

    def place(par, depth):
        for c in kids.get(par, []):
            text = f"**{ {'q': 'Question', 'a': 'Answer', 'pit': 'Pitfall', 'alt': 'Another way', 'iss': 'Issue', 'act': 'Changed files', 'ok': 'Needs you'}[c['k']] }**\n\n" \
                   + (f"# {c['h']}\n\n" if c.get("h") else "") + (c.get("b") or "")
            n = {"id": c["id"], "type": "text", "text": text, "x": 600 + depth * 420, "y": row[0] * 260, "width": 380, "height": 220, "x-card": c}
            if c["k"] in COLOR:
                n["color"] = COLOR[c["k"]]
            nodes.append(n)
            e = {"id": "e-" + c["id"], "fromNode": par, "fromSide": "right", "toNode": c["id"], "toSide": "left"}
            if c.get("edge"):
                e["label"] = c["edge"]
            edges.append(e)
            row[0] += 1 if not kids.get(c["id"]) else 0
            place(c["id"], depth + 1)
    place("plot", 0)
    return {"nodes": nodes, "edges": edges, "x-plot": pid}


def from_canvas(doc):
    return doc.get("x-plot"), [n["x-card"] for n in doc.get("nodes", []) if "x-card" in n]


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def dirty():
    """{path: status} of every changed or untracked path in the working tree."""
    out = git("status", "--porcelain", "--untracked-files=all").stdout
    return {line[3:].split(" -> ")[-1].strip('"'): line[:2] for line in out.splitlines() if line.strip()}


def commit_message(cmd):
    try:
        t = shlex.split(cmd)
        return t[t.index("-m") + 1] if "-m" in t else ""
    except ValueError:  # a heredoc or $(...) message: the owner edits it on the card
        return ""


def short(name, inp):
    """One line saying what a tool call does, for the steps list and the approval cards."""
    v = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or inp.get("url") or ""
    return (v if isinstance(v, str) else json.dumps(v))[:300]


class Hub:
    """Cards, runs and the event stream, shared by the HTTP threads and one thread per run (each with its own asyncio loop)."""

    def __init__(self, fake=False):
        self.fake = fake  # replay a scripted run through the same cards, approvals and save; no model call
        self.lock = threading.Lock()
        self.cards, self.paths = {}, {}  # pid -> [card]; pid -> the plot's file path
        self.qa = tempfile.mkdtemp(prefix="cr-fake-") if fake else QA
        os.makedirs(self.qa, exist_ok=True)
        for f in sorted(os.listdir(self.qa)):
            if f.endswith(".canvas"):
                with open(os.path.join(self.qa, f), encoding="utf-8") as fh:
                    pid, cs = from_canvas(json.load(fh))
                if pid:
                    self.cards[pid] = cs
        self.subs, self.waits = [], {}  # event queues; card id -> (loop, future)
        # set by dashboard.py under --claude: the plots the shared site shows (None: every plot) and the reader folder
        self.visible, self.reader_dir = None, ""
        self.q, self.running, self.queued, self.status = queue.Queue(), 0, 0, dict(st="idle", plot=None, t0=0)
        threading.Thread(target=self.worker, daemon=True).start()

    # ---- events
    def emit(self, ev):
        with self.lock:
            for s in self.subs:
                if ev.get("t") != "card":
                    s.put(ev)
                elif (c := self.seen(ev["plot"], ev["card"], s.role)) is not None:
                    s.put(dict(ev, card=c))

    def seen(self, pid, c, role):
        """A card as `role` sees it, or None: a coauthor sees the plots the shared site shows, and no steps, thinking,
        approvals or changed files (the owner's runs show commands and paths)."""
        if role == "owner":
            return c
        if (self.visible is not None and pid not in self.visible) or c["k"] in ("ok", "act"):
            return None
        return {k: v for k, v in c.items() if k not in ("steps", "thinking")}

    def view(self, role):
        return {pid: v for pid, cs in self.cards.items() if (v := [x for c in cs if (x := self.seen(pid, c, role)) is not None])}

    def subscribe(self, role="owner"):
        s = queue.Queue()
        s.role = role
        with self.lock:
            self.subs.append(s)
        return s

    def unsubscribe(self, s):
        with self.lock:
            self.subs.remove(s)

    def card(self, pid, c):
        self.emit({"t": "card", "plot": pid, "card": c})

    def set_status(self, **kw):
        self.status.update(kw)
        self.emit({"t": "status", **self.status, "running": self.running, "queued": self.queued})

    def save(self, pid):
        cs = [c for c in self.cards.get(pid, []) if not c.get("live") and not (c["k"] == "ok" and not c.get("res"))]
        with open(os.path.join(self.qa, slug(pid) + ".canvas"), "w", encoding="utf-8") as fh:
            json.dump(to_canvas(pid, self.paths.get(pid, ""), cs), fh, ensure_ascii=False, indent=1)
            fh.write("\n")

    # ---- requests from the page
    def ask(self, pid, parent, text, now, ctx, model=None, effort="medium", who="", role="owner"):
        self.paths[pid] = ctx.get("path", "")
        model = model if model in MODELS else ("sonnet" if role == "reader" else "opus")  # a coauthor's default: Sonnet (the owner, 11-15)
        effort = effort if effort in EFFORTS else "medium"
        now = now and role == "owner"  # a coauthor's question waits its turn: one run at a time on the owner's quota
        i = uuid.uuid4().hex[:8]
        q = dict(id="q" + i, par=parent, k="q", h=text, edge="…", by=f"{who or OWNER} · " + time.strftime("%m-%d %H:%M"), role=role)
        a = dict(id="a" + i, par=q["id"], k="a", h="", b="", live=True, t0=int(time.time() * 1000), steps=[], thinking="", role=role)
        self.cards.setdefault(pid, []).extend([q, a])
        self.card(pid, q), self.card(pid, a)
        run = dict(pid=pid, q=q, a=a, text=text, ctx=ctx, model=model, effort=effort, who=who or WHO, role=role)
        if now:
            threading.Thread(target=self.execute, args=(run,), daemon=True).start()
        else:
            a["queued"] = True
            self.queued += 1
            self.set_status()
            self.q.put(run)
        return a["id"]

    def answer(self, cid, choice):
        """The owner clicked a button on a Needs-you card; choice is a string, or a dict for the commit card."""
        loop, fut = self.waits.pop(cid, (None, None))
        if fut is None:
            return False
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(choice))
        return True

    def share(self, pid, cid, on):
        for c in self.cards.get(pid, []):
            if c["id"] == cid and c["k"] == "q":
                c["shared"] = bool(on)
                self.save(pid)
                self.card(pid, c)
                return True
        return False

    def issue(self, pid, cid, link=""):
        """The owner clicked Open GitHub issue on an issue card: gh issue create in the project, once per card."""
        with self.lock:
            c = next((c for c in self.cards.get(pid, []) if c["id"] == cid and c["k"] == "iss"), None)
            if c is None or c.get("busy") or str(c.get("res", "")).startswith(("✓", "(fake)")):
                return dict(ok=False, res=(c or {}).get("res", "no such card"))
            c["busy"] = True
        q = next((x for x in self.cards[pid] if x["k"] == "q" and any(a["id"] == c["par"] and a["par"] == x["id"] for a in self.cards[pid])), {})
        body = f"{c.get('b', '')}\n\nAsked in the room about {self.paths.get(pid) or pid}: {q.get('h', '')}" + (f"\n{link}" if link else "")
        if self.fake:
            res, ok = f"(fake) would open: {c['h']}", True
        else:
            try:
                r = subprocess.run(["gh", "issue", "create", "--title", c["h"], "--body", body], cwd=ROOT, capture_output=True, text=True, timeout=60)
                ok = r.returncode == 0
                url = r.stdout.strip().splitlines()[-1] if ok and r.stdout.strip() else ""
                res = f"✓ Opened #{url.rsplit('/', 1)[-1]} {url}" if ok else "gh failed: " + (r.stderr or r.stdout).strip()[:300]
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, res = False, f"gh failed: {e}"
        c.pop("busy", None)
        c["res"] = res
        self.save(pid)
        self.card(pid, c)
        return dict(ok=ok, res=res)

    # ---- runs
    def worker(self):  # sequential questions (Enter, the default) run one after another here
        while True:
            run = self.q.get()
            self.queued -= 1
            self.execute(run)

    def execute(self, run):
        run["a"].pop("queued", None)
        run["a"]["t0"] = int(time.time() * 1000)
        self.running += 1
        self.set_status(st="thinking", plot=run["pid"], t0=run["a"]["t0"])
        try:
            asyncio.run(self.fake_go(run) if self.fake else self.go(run))
        except Exception as e:  # the card says what went wrong instead of spinning forever
            run["a"].update(live=False, h="The run failed", b=f"{type(e).__name__}: {e}")
            self.card(run["pid"], run["a"])
        finally:
            self.running -= 1
            self.save(run["pid"])
            self.set_status(st="done" if not self.running else self.status["st"])

    async def wait_card(self, run, c):
        """Show a Needs-you card under the answer and wait for the owner's click."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        c.update(id="ok" + uuid.uuid4().hex[:8], par=run["a"]["id"], k="ok", edge="needs you")
        self.cards[run["pid"]].append(c)
        self.waits[c["id"]] = (loop, fut)
        run["a"]["waiting"] = True
        self.card(run["pid"], c), self.card(run["pid"], run["a"])
        self.set_status(st="waiting")
        choice = await fut
        run["a"]["waiting"] = False
        self.set_status(st="thinking")
        return c, choice

    async def go(self, run):
        from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher, types as T
        pid, a, q, ctx = run["pid"], run["a"], run["q"], run["ctx"]
        start = dirty()
        cards = self.cards[pid]
        src, history = session_for(cards, q["par"], run["role"])
        resume = dict(resume=src["session"]) if src else {}
        asker, reader = run["who"], run["role"] == "reader"
        if resume and src.get("msg") and src["msg"] != self.last_msg(pid, src["session"]):
            resume.update(fork_session=True, resume_session_at=src["msg"])  # a branch from an earlier answer
        if resume:
            prompt_text = f"{asker}'s follow-up (now showing: {ctx.get('variant', '')}): {run['text']}"
        elif pid.startswith("sit:"):  # the room's sitting box: a question about one takeaway, or about all of them
            prompt_text = (f"Question about the takeaways of one enclave sitting, in the control room.\n{ctx.get('title', pid)}\n"
                           f"Takeaways: {ctx.get('path', '')}; the full readout: {ctx.get('script', '')} (Read both).\n"
                           f"About: {ctx.get('variant', 'all the points')}\n"
                           "Answer in plain words, few numbers; expand on the point and say what supports it.\n"
                           f"\n{asker}'s question: {run['text']}")
        else:
            prompt_text = (f"Question about one plot in the control room.\nPlot: {ctx.get('title', pid)}\n"
                           f"Showing: {ctx.get('variant', '')}\nFile: {ctx.get('path', '')} (open it with Read to see it)\n"
                           f"Drawn by: {ctx.get('script', '')}\n" + (f"Caption: {ctx['caption']}\n" if ctx.get("caption") else "")
                           + f"\n{asker}'s question: {run['text']}")
        if history:  # a follow-up across roles starts fresh from what the dashboard shows of the thread
            prompt_text = f"The thread so far, as the dashboard shows it:\n\n{history}\n\n" + prompt_text

        async def can_use_tool(name, inp, tctx):
            rules = [r for u in (tctx.suggestions or []) for r in (getattr(u, "rules", None) or [])]
            rule = ", ".join(f"{getattr(r, 'tool_name', '')}({getattr(r, 'rule_content', '') or ''})" for r in rules)
            buttons = [["once", "Allow once"]] + ([["thread", f"Allow {rule} for this thread"]] if rule else []) + [["deny", "Deny"]]
            c, choice = await self.wait_card(run, dict(h=f"Claude wants to use {name}", b=short(name, inp), buttons=buttons))
            c.update(res={"once": "✓ Allowed once", "thread": f"✓ Allowed {rule} for this thread", "deny": "✕ Denied"}.get(choice, str(choice)))
            self.card(pid, c)
            if choice == "deny":
                return T.PermissionResultDeny(message=f"Denied in the dashboard by {WHO}.")
            # the SDK's suggestions may name localSettings; "for this thread" must not outlive the thread
            keep = [dataclasses.replace(u, destination="session") for u in (tctx.suggestions or [])]
            return T.PermissionResultAllow(updated_permissions=keep if choice == "thread" else None)

        async def gate(inp, tool_use_id, hctx):
            cmd = (inp.get("tool_input") or {}).get("command", "")
            subs = git_subs(cmd) if inp.get("tool_name") == "Bash" else []
            if not set(subs) - GIT_READ:
                return {}  # no git, or read-only git: the normal permission flow decides
            if not {"commit", "push"} & set(subs):  # any other git write: a plain card, once or deny
                c, choice = await self.wait_card(run, dict(h=f"Claude wants to run git {' '.join(s for s in subs if s not in GIT_READ)}",
                                                          b=cmd[:400], buttons=[["once", "Allow once"], ["deny", "Deny"]]))
                c.update(res="✓ Allowed once" if choice == "once" else "✕ Denied")
                self.card(pid, c)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" if choice == "once" else "deny",
                                               "permissionDecisionReason": f"Decided in the dashboard by {WHO}."}}
            now = dirty()
            paths = [[p, True, ""] for p, s in sorted(now.items()) if p not in start or start[p] != s]
            other = len(now) - len(paths)  # changed before this question (other sessions' work): not offered
            c, choice = await self.wait_card(run, dict(h="Claude wants to commit", commit=True, paths=paths,
                                                      b=cmd[:400] + (f"\n\n{other} other changed path(s) predate this question and are left out." if other else ""),
                                                      msg=commit_message(cmd) or f"Dashboard: {run['text'][:70]}",
                                                      buttons=[["commit", "Commit"], ["push", "Commit and push"], ["deny", "Deny"]]))
            if not isinstance(choice, dict) or choice.get("do") not in ("commit", "push"):
                c.update(res="✕ Denied")
                self.card(pid, c)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                               "permissionDecisionReason": f"The commit was denied in the dashboard by {WHO}."}}
            note = self.commit(choice.get("paths") or [], choice.get("msg") or "Dashboard commit", choice["do"] == "push")
            c.update(res=note)
            self.card(pid, c)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": f"The dashboard made the commit itself on the owner's OK: {note}. Do not run git commit or push again."}}

        async def prompt():
            yield {"type": "user", "message": {"role": "user", "content": prompt_text}}

        async def read_only(inp, tool_use_id, hctx):  # a coauthor's run: every call is checked here; no card is ever shown
            ok = reader_gate(inp.get("tool_name", ""), inp.get("tool_input") or {}, self.reader_dir)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" if ok else "deny",
                                           "permissionDecisionReason": "Only reading the dashboard's files is allowed here."}}

        async def deny(name, inp, tctx):
            return T.PermissionResultDeny(message="Only reading the dashboard's files is allowed here.")

        if reader and not self.reader_dir:
            raise RuntimeError("the reader folder is not built, so a coauthor's question cannot run")
        opts = ClaudeAgentOptions(cwd=self.reader_dir, model=run["model"], effort=run["effort"], tools=list(READ), setting_sources=[],
                                  can_use_tool=deny, include_partial_messages=True, permission_mode="default", strict_mcp_config=True,
                                  system_prompt={"type": "preset", "preset": "claude_code", "append": READER_SYS.format(who=asker)},
                                  thinking=T.ThinkingConfigAdaptive(type="adaptive", display="summarized"),
                                  hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[read_only])]}, **resume) if reader else \
            ClaudeAgentOptions(cwd=ROOT, model=run["model"], effort=run["effort"], can_use_tool=can_use_tool, include_partial_messages=True,
                                  permission_mode="default", setting_sources=["user", "project", "local"], strict_mcp_config=True,
                                  system_prompt={"type": "preset", "preset": "claude_code", "append": SYS},
                                  thinking=T.ThinkingConfigAdaptive(type="adaptive", display="summarized"),
                                  hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[gate], timeout=86400)]}, **resume)
        last, result = 0.0, None
        async for m in query(prompt=prompt(), options=opts):
            kind = type(m).__name__
            if kind == "StreamEvent" and m.parent_tool_use_id is None:
                ev = m.event
                if ev.get("type") == "content_block_delta":
                    d = ev["delta"]
                    if d["type"] == "thinking_delta":
                        a["thinking"] += d.get("thinking", "")
                    elif d["type"] == "text_delta":
                        a["b"] += d.get("text", "")
                        if self.status["st"] != "writing":
                            self.set_status(st="writing")
            elif kind == "AssistantMessage" and m.parent_tool_use_id is None:
                a.update(session=m.session_id, msg=m.uuid)
                for blk in m.content:
                    if type(blk).__name__ == "ToolUseBlock":
                        a["steps"].append([blk.name, short(blk.name, blk.input)])
                        a["b"] = ""  # narration before a tool call is not the answer
            elif kind == "ResultMessage":
                result = m
            if time.time() - last > 0.15:  # ponytail: whole-card updates, throttled; deltas if answers get long
                self.card(pid, a)
                last = time.time()
        if result:
            a["session"] = result.session_id
        self.finish(run, start, (result.result if result and result.result else a["b"]) or "",
                    f" · ${result.total_cost_usd:.2f}" if result and result.total_cost_usd else "")

    def finish(self, run, start, text, cost=""):
        """The run's final reply becomes the answer card, its pitfall and another-way cards, and a changed-files card."""
        pid, a, q = run["pid"], run["a"], run["q"]
        label, title, question, body, extra = split_answer(text)
        q["edge"] = label or "question"
        if question and question != q["h"]:  # the cleaned question; the typed one stays on the card
            q.update(orig=q["h"], h=question)
        secs = round((time.time() * 1000 - a["t0"]) / 1000)
        a.update(live=False, h=title, b=body, by=f"Claude {run['model'].title()} · {run['effort']} · {secs} s · {len(a['steps'])} steps{cost}")
        for k, t, b in extra:
            self.cards[pid].append(dict(id=k + uuid.uuid4().hex[:8], par=a["id"], k=k, h=t, b=b, by="Claude",
                                        edge={"pit": "watch out", "alt": "another way", "iss": "to do"}[k]))
            self.card(pid, self.cards[pid][-1])
        changed = sorted(p for p, s in dirty().items() if p not in start or start[p] != s)
        if changed:
            self.cards[pid].append(dict(id="act" + uuid.uuid4().hex[:8], par=a["id"], k="act", h=f"{len(changed)} path(s) changed",
                                        b="\n".join(changed), by="Claude · " + ("committed" if "commit" in json.dumps(a["steps"]) else "not committed"),
                                        edge="changed"))
            self.card(pid, self.cards[pid][-1])
        self.card(pid, q), self.card(pid, a)

    async def fake_go(self, run):
        """--fake: a scripted run (thinking, three steps, an approval, a streamed answer, a commit card), for testing
        the page and the plumbing without spending quota or starting a Claude session. Nothing is written or committed."""
        pid, a = run["pid"], run["a"]
        start = dirty()
        for w in "The owner asks which bin scheme is safest; check each scheme's smallest bin.".split():
            a["thinking"] += w + " "
            self.card(pid, a)
            await asyncio.sleep(0.05)
        for step in [["Read", run["ctx"].get("script", "")], ["Read", run["ctx"].get("path", "")], ["Bash", "python3 - (counts per bin)"]]:
            a["steps"].append(step)
            self.card(pid, a)
            await asyncio.sleep(0.6)
        c, choice = await self.wait_card(run, dict(h="Claude wants to use Write", b="code/local/scratch_fake.py (fake: nothing is written)",
                                                   buttons=[["once", "Allow once"], ["thread", "Allow Write(code/local/**) for this thread"], ["deny", "Deny"]]))
        c.update(res={"once": "✓ Allowed once", "thread": "✓ Allowed for this thread", "deny": "✕ Denied"}.get(choice, str(choice)))
        self.card(pid, c)
        c2, choice2 = await self.wait_card(run, dict(h="Claude wants to commit", b='git commit -m "fake"', commit=True,
                                                     paths=[["code/local/scratch_fake.py", True, ""], ["DECISIONS.md", False, "changed before"]],
                                                     msg="fake: add 15 equal-count bins", buttons=[["commit", "Commit"], ["push", "Commit and push"], ["deny", "Deny"]]))
        c2.update(res=f"(fake) would commit {choice2.get('paths') if isinstance(choice2, dict) else 'nothing'}" if isinstance(choice2, dict) and choice2.get("do") != "deny" else "✕ Denied")
        self.card(pid, c2)
        self.set_status(st="writing")
        text = ("Question: (fake) " + run["text"][:1].upper() + run["text"][1:].rstrip("?. ") + "?\n"
                "Label: safest bins\nTitle: (fake) 20 equal-count bins keep every bin well filled\n"
                "(fake answer) Every scheme keeps enough points in each bin; 33 bins is the tightest.\n\n"
                "### Pitfall: (fake) the equal-width schemes drop bins\nA bin with too few points is not drawn, so the narrow scheme shows few points.\n"
                "### Issue: (fake) Count the points per bin in the enclave\nThe counts are not in the release; the enclave run would add them.")
        for w in text.split(" "):
            a["b"] += w + " "
            self.card(pid, a)
            await asyncio.sleep(0.03)
        self.finish(run, start, text, " · fake")

    def last_msg(self, pid, session):
        ms = [c.get("msg") for c in self.cards.get(pid, []) if c.get("session") == session and c.get("msg")]
        return ms[-1] if ms else None

    def commit(self, paths, msg, push):
        """Commit exactly the ticked paths (git add, then git commit -- paths); push if asked. Returns a one-line note."""
        if not paths:
            return "nothing ticked, nothing committed"
        r = git("add", "--", *paths)
        if r.returncode == 0:
            r = git("commit", "-q", "-m", msg, "--", *paths)
        if r.returncode:
            return "commit failed: " + (r.stderr or r.stdout).strip()[:300]
        sha = git("rev-parse", "--short", "HEAD").stdout.strip()
        if not push:
            return f"✓ Committed {sha} ({len(paths)} path(s)); not pushed"
        p = git("push", "-q", "origin", "HEAD")
        return f"✓ Committed and pushed {sha}" if p.returncode == 0 else f"✓ Committed {sha}; push failed, tell {WHO}: {p.stderr.strip()[:200]}"


def selftest():
    lab, tit, qn, body, extra = split_answer("Label: why one line\nTitle: The line ignores the bins\nIt is one fit.\n\n"
                                             "### Pitfall: Nine sectors off panel\nThey are in the fit.\n### Another way: Read the slope\nSame slope.")
    assert (lab, tit, qn, body) == ("why one line", "The line ignores the bins", "", "It is one fit."), (lab, tit, qn, body)
    assert extra == [("pit", "Nine sectors off panel", "They are in the fit."), ("alt", "Read the slope", "Same slope.")], extra
    assert split_answer("Just an answer.") == ("", "", "", "Just an answer.", [])
    got = split_answer("Question: Why is the fit flat?\nLabel: flat fit\nTitle: It is one slope\nBody.\n\n"
                       "### Issue: Run the fit by sector\nNeeds the enclave.")
    assert got == ("flat fit", "It is one slope", "Why is the fit flat?", "Body.", [("iss", "Run the fit by sector", "Needs the enclave.")]), got
    assert split_answer("Title: T\nQuestion: Q?\nB")[1:4] == ("T", "Q?", "B")  # the header lines in any order
    cards = [dict(id="q1", par="plot", k="q", h="Why?", edge="why"), dict(id="a1", par="q1", k="a", h="Because", b="x"),
             dict(id="p1", par="a1", k="pit", h="Careful", b="y", edge="watch out")]
    doc = to_canvas("fam:Sector scatter", "figures/76_x_v2.pdf", cards)
    assert from_canvas(json.loads(json.dumps(doc))) == ("fam:Sector scatter", cards)
    assert [n.get("color") for n in doc["nodes"]] == [None, None, "4", "1"], doc["nodes"]
    to_canvas("p", "f.pdf", [dict(id="i1", par="plot", k="iss", h="Run it", b="x")])  # an issue card has a kind name
    hub = Hub(fake=True)
    hub.q.put = lambda run: setattr(hub, "last", run)  # keep the run instead of the worker taking it
    hub.ask("p", "plot", "why?", False, {}, model="gpt", effort="max")
    assert (hub.last["model"], hub.last["effort"]) == ("opus", "medium"), hub.last
    hub.ask("p", "plot", "why?", False, {}, model="sonnet", effort="low")
    assert (hub.last["model"], hub.last["effort"]) == ("sonnet", "low"), hub.last
    hub.cards["p"].append(dict(id="iss1", par="a1", k="iss", h="Run the fit by sector", b="Needs the enclave."))
    r1, r2 = hub.issue("p", "iss1"), hub.issue("p", "iss1")
    assert r1["ok"] and r1["res"].startswith("(fake) would open") and not r2["ok"], (r1, r2)  # one issue per card
    assert not hub.issue("p", "nope")["ok"]
    assert {(e["fromNode"], e["toNode"], e.get("label")) for e in doc["edges"]} == {("plot", "q1", "why"), ("q1", "a1", None), ("a1", "p1", "watch out")}
    for cmd, subs in [("git commit -m x", ["commit"]), ("git -C /r push origin main", ["push"]), ("git status", ["status"]),
                      ("git log --grep commit", ["log"]), ("echo commit", []), ("git stash", ["stash"]),
                      ("cd x && git checkout -- a.py", ["checkout"]), ("git --no-pager diff && git reset --hard", ["diff", "reset"]),
                      ("git -c core.x=1 clean -fd", ["clean"]), ("git add a.txt && git commit -m add", ["add", "commit"])]:
        assert git_subs(cmd) == subs, (cmd, git_subs(cmd))
    assert not set(git_subs("git status && git log")) - GIT_READ and set(git_subs("git stash")) - GIT_READ
    assert commit_message('git commit -m "add the 15-bin option"') == "add the 15-bin option"
    assert commit_message("git commit -m \"$(cat <<'EOF'\nx\nEOF\n)\"") in ("", "$(cat <<'EOF'\nx\nEOF\n)")
    # a coauthor's run (design 11-15, shared Q&A): only Read, Grep and Glob, and only inside its folder
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "figures"))
        open(os.path.join(root, "figures", "a.csv"), "w").close()
        os.symlink("/etc", os.path.join(root, "out"))
        ok = lambda t, **i: reader_gate(t, i, root)
        assert ok("Read", file_path="figures/a.csv") and ok("Read", file_path=os.path.join(root, "figures", "a.csv"))
        assert ok("Grep", pattern="x") and ok("Glob", pattern="*.csv", path="figures") and ok("Grep", pattern="x", path=root)
        for t, i in [("Bash", {"command": "ls"}), ("Write", {"file_path": "figures/b"}), ("Edit", {"file_path": "figures/a.csv"}),
                     ("WebFetch", {"url": "https://example.org"}), ("Read", {"file_path": os.path.expanduser("~/.config/x")}),
                     ("Read", {"file_path": "../x"}), ("Read", {"file_path": "/etc/passwd"}), ("Read", {"file_path": "out/passwd"}),
                     ("Grep", {"pattern": "x", "path": "/"}), ("Glob", {"pattern": "*", "path": ".."}), ("Read", {})]:
            assert not ok(t, **i), (t, i)
    # a follow-up resumes the earlier answer's session within one role; across roles it starts fresh from the thread's text
    cs = [dict(id="q1", par="plot", k="q", h="Why flat?"), dict(id="a1", par="q1", k="a", h="One slope", b="It is one fit.", session="s1"),
          dict(id="q2", par="a1", k="q", h="And the bins?", role="reader"),
          dict(id="a2", par="q2", k="a", h="Twenty", b="Equal counts.", session="s2", role="reader")]
    assert session_for(cs, "a1", "owner") == (cs[1], "") and session_for(cs, "a2", "reader") == (cs[3], "")
    src, text = session_for(cs, "a1", "reader")
    assert src is None and "Why flat?" in text and "It is one fit." in text, text
    src, text = session_for(cs, "a2", "owner")
    assert src is None and "And the bins?" in text and "Equal counts." in text and "Why flat?" in text, text
    # a coauthor's question defaults to Sonnet, queues, and names who asked; a coauthor sees only what the site shows
    hub.ask("p", "plot", "why?", True, {}, who="pat", role="reader")
    assert (hub.last["model"], hub.last["role"], hub.last["q"]["by"].split(" · ")[0]) == ("sonnet", "reader", "pat"), hub.last
    hub.visible = {"p"}
    hub.cards = {"p": [dict(id="q9", par="plot", k="q", h="Q"), dict(id="a9", par="q9", k="a", h="A", b="b", steps=[["Bash", "ls"]], thinking="t"),
                       dict(id="ok9", par="a9", k="ok", h="Claude wants to use Bash"), dict(id="act9", par="a9", k="act", h="1 path")],
                 "hidden": [dict(id="q8", par="plot", k="q", h="Q")]}
    assert hub.view("reader") == {"p": [dict(id="q9", par="plot", k="q", h="Q"), dict(id="a9", par="q9", k="a", h="A", b="b")]}, hub.view("reader")
    assert hub.view("owner") == hub.cards
    print("agent selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
