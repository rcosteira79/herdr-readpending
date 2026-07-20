#!/usr/bin/env python3
"""Read-pending marker for herdr agents.

State: an ordered list of pane ids (the reading queue) in
HERDR_PLUGIN_STATE_DIR/queue.json. Each queued pane carries a display token
`read` = "<glyph><position>" set via `herdr pane report-metadata`; add `$read`
to [ui.sidebar.agents] rows to see it. Position follows queue order.

herdr has no plugin focus trigger, so auto-clear-on-focus lives in a companion
daemon (readpending.py daemon): a poll loop that clears a pending pane the moment
it gains focus. It is (re)started whenever an agent is marked, and self-exits
once the queue is empty (or the herdr server goes away). The list pane is a
summon-anywhere overlay for viewing/reordering — it does not own auto-clear.

Subcommands:
  toggle       add/remove the focused agent (action, `pane` context)
  open-list    open the overlay list pane (global action)
  ui           the interactive overlay list pane
  daemon       the auto-clear-on-focus watcher (spawned, detached)
"""
import fcntl
import json
import os
import subprocess
import sys

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "rcosteira.readpending")
TOKEN = "read"
GLYPH = "\N{OPEN BOOK}"  # shown before the position number in the badge
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")

STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    "~/.local/state/herdr/readpending"
)
QUEUE = os.path.join(STATE_DIR, "queue.json")
LOCK = os.path.join(STATE_DIR, "queue.lock")
PIDFILE = os.path.join(STATE_DIR, "daemon.pid")
POLL_SECONDS = 1


def herdr(*args):
    """Run the herdr CLI; return CompletedProcess (never raises on non-zero)."""
    return subprocess.run(
        [HERDR, *args], capture_output=True, text=True, check=False
    )


def _load():
    try:
        with open(QUEUE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(queue):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(queue, f)
    os.replace(tmp, QUEUE)


class _Lock:
    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self._f = open(LOCK, "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._f, fcntl.LOCK_UN)
        self._f.close()


def live_agents():
    """pane_id -> agent info dict, for panes that still exist.
    Returns None if the herdr CLI/server can't be reached (distinct from an
    empty session), so callers don't mistake "server down" for "no agents"."""
    res = herdr("agent", "list")
    if res.returncode != 0:
        return None
    try:
        agents = json.loads(res.stdout)["result"]["agents"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return {a["pane_id"]: a for a in agents if a.get("pane_id")}


def _set_badge(pane_id, position):
    herdr(
        "pane", "report-metadata", pane_id,
        "--source", PLUGIN_ID,
        "--token", f"{TOKEN}={GLYPH}{position}",
    )


def _clear_badge(pane_id):
    herdr(
        "pane", "report-metadata", pane_id,
        "--source", PLUGIN_ID,
        "--clear-token", TOKEN,
    )


def _reindex(queue, prune=True):
    """Drop dead panes (if prune), then set each pane's badge to its 1-based
    position. Returns the (possibly pruned) queue. Caller must persist it."""
    if prune:
        agents = live_agents()
        if agents is not None:  # skip pruning if the server is unreachable
            queue = [p for p in queue if p in agents]
    for i, pane_id in enumerate(queue, start=1):
        _set_badge(pane_id, i)
    return queue


# ---- target resolution (for the toggle action) ---------------------------

def _focused_pane_from_context():
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw).get("focused_pane_id")
    except json.JSONDecodeError:
        return None


def _resolve_target():
    for env in ("HERDR_ACTIVE_PANE_ID", "HERDR_PANE_ID"):
        if os.environ.get(env):
            return os.environ[env]
    ctx = _focused_pane_from_context()
    if ctx:
        return ctx
    for pane_id, info in (live_agents() or {}).items():
        if info.get("focused"):
            return pane_id
    return None


# ---- subcommands ----------------------------------------------------------

def cmd_toggle():
    target = _resolve_target()
    if not target:
        print("read-pending: no focused agent pane to toggle", file=sys.stderr)
        return 1
    added = False
    with _Lock():
        queue = _load()
        if target in queue:
            queue.remove(target)
            _clear_badge(target)
            queue = _reindex(queue)
        else:
            queue.append(target)
            queue = _reindex(queue)
            added = True
        _save(queue)
    if added:
        _ensure_daemon()  # watch for focus so it auto-clears
    return 0


def _remove(pane_id):
    """Locked: drop a pane from the queue, clear its badge, renumber."""
    with _Lock():
        queue = _load()
        if pane_id in queue:
            queue.remove(pane_id)
            _clear_badge(pane_id)
            _save(_reindex(queue))
            return True
    return False


# ---- companion daemon (auto-clear-on-focus) -------------------------------

def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    return True


def _read_pid():
    try:
        return int(open(PIDFILE).read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _ensure_daemon():
    """Start the auto-clear daemon if one isn't already running."""
    with _Lock():
        if _pid_alive(_read_pid()):
            return
    script = os.path.abspath(__file__)
    subprocess.Popen(
        [sys.executable, script, "daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=os.path.dirname(script),
        env=os.environ.copy(),
    )


def cmd_daemon():
    import time

    # Single instance: claim the pidfile, or bail if a live daemon owns it.
    with _Lock():
        existing = _read_pid()
        if _pid_alive(existing) and existing != os.getpid():
            return 0
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))

    prev_focus = {}
    empty_polls = 0
    server_fails = 0
    try:
        while True:
            queue = _load()
            if not queue:
                empty_polls += 1
                if empty_polls >= 3:  # nothing pending -> exit, restarted on next mark
                    break
                time.sleep(POLL_SECONDS)
                continue
            empty_polls = 0

            agents = live_agents()
            if agents is None:
                server_fails += 1
                if server_fails >= 5:  # herdr gone -> exit
                    break
                time.sleep(POLL_SECONDS)
                continue
            server_fails = 0

            # Clear a pending pane the moment it transitions unfocused->focused.
            for pid in queue:
                foc = bool(agents.get(pid, {}).get("focused"))
                was = prev_focus.get(pid)
                if was is None:
                    prev_focus[pid] = foc  # first sighting: seed, don't clear
                else:
                    if foc and not was:
                        _remove(pid)
                    prev_focus[pid] = foc
            prev_focus = {k: v for k, v in prev_focus.items() if k in _load()}
            time.sleep(POLL_SECONDS)
    finally:
        with _Lock():
            if _read_pid() == os.getpid():
                try:
                    os.remove(PIDFILE)
                except OSError:
                    pass
    return 0


def cmd_open_list():
    _ensure_daemon()
    res = herdr(
        "plugin", "pane", "open",
        "--plugin", PLUGIN_ID,
        "--entrypoint", "list",
        "--placement", "overlay",
    )
    if res.returncode != 0:
        sys.stderr.write(res.stderr or "read-pending: failed to open list pane\n")
    return res.returncode


# ---- interactive list pane ------------------------------------------------

def _label(info):
    name = (
        info.get("name")
        or info.get("display_agent")
        or info.get("terminal_title_stripped")
        or info.get("terminal_title")
        or info.get("agent")
        or info.get("pane_id")
    )
    return str(name).strip() or info.get("pane_id", "?")


def _cwd_tail(info):
    cwd = info.get("cwd") or ""
    return os.path.basename(cwd.rstrip("/")) if cwd else ""


def _move(queue, index, delta):
    j = index + delta
    if 0 <= j < len(queue):
        queue[index], queue[j] = queue[j], queue[index]
        return j
    return index


def cmd_ui():
    import curses

    if _load():
        _ensure_daemon()  # queue survived a restart the daemon didn't

    def run(stdscr):
        curses.curs_set(0)
        stdscr.timeout(1000)  # refresh cadence (ms); auto-clear is the daemon's job
        sel = 0
        while True:
            raw = live_agents()
            agents = raw if raw is not None else {}
            # Don't prune the display when the server is briefly unreachable.
            queue = _load() if raw is None else [p for p in _load() if p in agents]
            if sel >= len(queue):
                sel = max(0, len(queue) - 1)

            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "READ PENDING"
            hint = "j/k select · J/K reorder · enter jump · x remove · q quit"
            stdscr.addnstr(0, 0, header, w - 1, curses.A_BOLD)
            if h > 1:
                stdscr.addnstr(1, 0, hint, w - 1, curses.A_DIM)
            if not queue:
                if h > 3:
                    stdscr.addnstr(3, 0, "(nothing pending)", w - 1, curses.A_DIM)
            else:
                for i, pane_id in enumerate(queue):
                    row = i + 3
                    if row >= h:
                        break
                    info = agents.get(pane_id, {"pane_id": pane_id})
                    tail = _cwd_tail(info)
                    status = info.get("agent_status", "")
                    line = f"{i + 1:>2}. {_label(info)}"
                    if status:
                        line += f"  [{status}]"
                    if tail:
                        line += f"  ({tail})"
                    attr = curses.A_REVERSE if i == sel else curses.A_NORMAL
                    stdscr.addnstr(row, 0, line.ljust(w - 1), w - 1, attr)
            stdscr.refresh()

            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                return
            if ch == -1:
                continue  # timeout -> refresh
            if ch in (ord("q"), 27):
                return
            if not queue:
                continue
            if ch in (ord("j"), curses.KEY_DOWN):
                sel = min(len(queue) - 1, sel + 1)
            elif ch in (ord("k"), curses.KEY_UP):
                sel = max(0, sel - 1)
            elif ch in (ord("J"),):
                with _Lock():
                    q = _load()
                    q = [p for p in q if p in agents]
                    if sel < len(q):
                        sel = _move(q, sel, +1)
                        _save(_reindex(q, prune=False))
            elif ch in (ord("K"),):
                with _Lock():
                    q = _load()
                    q = [p for p in q if p in agents]
                    if sel < len(q):
                        sel = _move(q, sel, -1)
                        _save(_reindex(q, prune=False))
            elif ch in (ord("x"),):
                _remove(queue[sel])
            elif ch in (curses.KEY_ENTER, 10, 13):
                herdr("agent", "focus", queue[sel])  # daemon clears it on focus
                return  # close the overlay after jumping

    curses.wrapper(run)
    return 0


DISPATCH = {
    "toggle": cmd_toggle,
    "open-list": cmd_open_list,
    "ui": cmd_ui,
    "daemon": cmd_daemon,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in DISPATCH:
        print(f"usage: readpending.py {{{'|'.join(DISPATCH)}}}", file=sys.stderr)
        return 2
    return DISPATCH[sys.argv[1]]() or 0


if __name__ == "__main__":
    sys.exit(main())
