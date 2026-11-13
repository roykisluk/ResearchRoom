"""Where the project is and what its config says. Imported by dashboard.py and agent.py.

ROOT is the folder given with --project, else the folder the engine is run from: never the engine's own location.
DATA is the project's dashboard data, ROOT/.room. CFG is DATA/config.toml, {} when the project has none
(design docs/plans/2026-11-13-shared-dashboard-design.md §2, §3).
"""
import argparse, os, sys

if sys.version_info < (3, 11):
    sys.exit(f"needs Python 3.11 or newer (this is {sys.version_info.major}.{sys.version_info.minor})")
import tomllib  # noqa: E402  3.11 and later

_ap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)  # the engine's own parser lists --project for --help
_ap.add_argument("--project", default=".")
ROOT = os.path.abspath(os.path.expanduser(_ap.parse_known_args()[0].project))
DATA = os.path.join(ROOT, ".room")
# Every key the config may hold; a table's value is its own keys. A key is added here only with a reason, written in
# the commit: the file must not grow into a second program (design, anti-goals).
ALLOWED = {"name": None, "title": None, "floor": None, "marks_from": None, "people": None,
           "hosted": {"project", "hosts", "access_team", "emails", "env_file"}, "claude": {"prompt_file"}, "siblings": {"codebooks"}}


def config(path):
    """The parsed config, {} when there is no file; exits with one line on a key outside ALLOWED or a file that
    does not parse, so a misspelt key is never read as "not set"."""
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        sys.exit(f".room/config.toml does not parse: {e}")
    known = {k for k, v in ALLOWED.items() if not v} | {f"{k}.{x}" for k, v in ALLOWED.items() if v for x in v}
    said = [k for k, v in cfg.items() if not isinstance(v, dict)] + [f"{k}.{x}" for k, v in cfg.items() if isinstance(v, dict) for x in v]
    for k in said:
        if k not in known:
            sys.exit(f"unknown key '{k}' in .room/config.toml; allowed: {', '.join(sorted(known))}")
    return cfg


CFG = config(os.path.join(DATA, "config.toml"))
NAME = str(CFG.get("name") or os.path.basename(ROOT))  # page titles, and the PNG cache's folder
TITLE = str(CFG.get("title") or NAME)                  # the room's brand and the overview's heading
PEOPLE = [str(x) for x in CFG.get("people", [])]       # the note owners; the first is the room's owner
