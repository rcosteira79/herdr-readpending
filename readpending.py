#!/usr/bin/env python3
"""Read-pending marker for herdr agents.

State: an ordered list of pane ids (the reading queue) in
HERDR_PLUGIN_STATE_DIR/queue.json. Each queued pane carries a display token
`read` = "<glyph><position>" set via `herdr pane report-metadata`; add `$read`
to [ui.sidebar.agents] rows to see it. Position follows queue order.

Auto-clear-on-focus is a herdr event hook. The manifest asks for
`pane.focused`, and herdr runs `readpending.py on-focus` naming the pane that
just gained focus. No daemon, no poll loop: the event *is* the transition the
old daemon spent a second at a time looking for.

Spell the event with dots. herdr's API schema lists the same kinds with
underscores, and the manifest turns `pane_focused` down with "unknown event".

The list pane is a summon-anywhere overlay for viewing and reordering. It does
not own auto-clear.

Subcommands:
  toggle       add/remove the focused agent (action, `pane` context)
  open-list    open the overlay list pane (global action)
  ui           the interactive overlay list pane
  on-focus     clear the pane that just gained focus (event hook)
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
    with _Lock():
        queue = _load()
        if target in queue:
            queue.remove(target)
            _clear_badge(target)
        else:
            queue.append(target)
        _save(_reindex(queue))
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


# ---- auto-clear-on-focus (herdr event hook) -------------------------------

def cmd_on_focus():
    """Clear the pane that just gained focus. Run by herdr on `pane.focused`.

    herdr names the pane *gaining* focus, in HERDR_PANE_ID and in the context
    blob's focused_pane_id. Verified on herdr 0.8.2: focusing a new pane fires
    once for it, and closing that pane fires again for the pane that gets focus
    back. So the pane id in hand is the one the reader is now looking at.

    Marking the pane you are already on does not clear it: no focus change
    happened, so no event fires. Leaving and coming back clears it.

    A missed event costs a badge that lingers, and the next focus of that pane
    clears it. That is why this needs no safety poll.
    """
    target = _resolve_target()
    if not target:
        return 0
    _remove(target)
    return 0


def cmd_open_list():
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

    def run(stdscr):
        curses.curs_set(0)
        # Refresh cadence (ms) for the display only. Auto-clear is the
        # `pane.focused` hook's job, whether this pane is open or not.
        stdscr.timeout(1000)
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
                herdr("agent", "focus", queue[sel])  # the focus hook clears it
                return  # close the overlay after jumping

    curses.wrapper(run)
    return 0


DISPATCH = {
    "toggle": cmd_toggle,
    "open-list": cmd_open_list,
    "ui": cmd_ui,
    "on-focus": cmd_on_focus,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in DISPATCH:
        print(f"usage: readpending.py {{{'|'.join(DISPATCH)}}}", file=sys.stderr)
        return 2
    return DISPATCH[sys.argv[1]]() or 0


if __name__ == "__main__":
    sys.exit(main())
