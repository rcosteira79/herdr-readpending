#!/usr/bin/env python3
"""Read-pending marker for herdr agents.

State: an ordered list of records (the reading queue) in
HERDR_PLUGIN_STATE_DIR/queue.json. Each record holds a pane id, a mark id, and
whether that mark is armed. A queue of bare pane ids written by an older
version of this plugin still loads, as unarmed marks. Each queued pane carries
a display token `read` = "<glyph><position>" set via `herdr pane
report-metadata`; add `$read` to [ui.sidebar.agents] rows to see it. Position
follows queue order.

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
import time

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


def _entry(pane, mark=0, armed=False):
    """One queued mark: the pane it marks, the id that tells this mark from the
    next mark on the same pane, and whether the daemon has seen it unfocused."""
    return {"pane": pane, "mark": int(mark), "armed": bool(armed)}


def _next_mark(queue):
    """A mark id greater than every mark in `queue`. Wall-clock nanoseconds keep
    it rising across a queue that empties and forgets its marks; the floor keeps
    it rising within one queue whatever the clock does. If the queue empties and
    the wall clock then steps backwards, the next mark could still repeat an id
    from before the queue emptied — the floor cannot see marks that are gone."""
    floor = max((e.get("mark", 0) for e in queue), default=0) + 1
    return max(time.time_ns(), floor)


def _load():
    try:
        with open(QUEUE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, str) and item:
            out.append(_entry(item))  # pre-mark format: unarmed, mark 0
        elif isinstance(item, dict) and isinstance(item.get("pane"), str) and item["pane"]:
            raw = item.get("mark", 0)
            mark = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
            out.append(_entry(item["pane"], mark, item.get("armed") is True))
    return out


def _save(queue):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(queue, f)
    os.replace(tmp, QUEUE)


def _pane(entry):
    """The pane id of a queue entry. `_load` normalises every entry it reads
    into a record, so the bare-string fallback here only matters for
    hand-built lists such as the ones the tests construct directly."""
    return entry.get("pane") if isinstance(entry, dict) else entry


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


_UNFETCHED = object()  # _reindex's default: "no agents map was passed in"


def _reindex(queue, prune=True, agents=_UNFETCHED):
    """Drop dead panes (if prune), then set each pane's badge to its 1-based
    position. Returns the (possibly pruned) queue. Caller must persist it.

    Pass `agents` (e.g. already fetched via `live_agents()`) to reuse that
    snapshot instead of calling `live_agents()` in here. A caller holding the
    queue lock must fetch it before taking the lock, so the `herdr agent list`
    subprocess call never runs while the lock is held. `agents=None` means the
    caller found the server unreachable; that skips pruning, same as before."""
    if prune:
        if agents is _UNFETCHED:
            agents = live_agents()
        if agents is not None:  # skip pruning if the server is unreachable
            queue = [e for e in queue if _pane(e) in agents]
    for i, entry in enumerate(queue, start=1):
        _set_badge(_pane(entry), i)
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
    agents = live_agents()
    with _Lock():
        queue = _load()
        kept = [e for e in queue if _pane(e) != target]
        if len(kept) != len(queue):
            _clear_badge(target)
            queue = kept
        else:
            queue.append(_entry(target, _next_mark(queue)))
        _save(_reindex(queue, agents=agents))
    return 0


def _remove(pane_id):
    """Locked: drop a pane from the queue, clear its badge, renumber."""
    agents = live_agents()
    with _Lock():
        queue = _load()
        kept = [e for e in queue if _pane(e) != pane_id]
        if len(kept) != len(queue):
            _clear_badge(pane_id)
            _save(_reindex(kept, agents=agents))
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


def _visible(queue, agents):
    """Queue entries whose pane herdr still knows about."""
    return [e for e in queue if _pane(e) in agents]


def _index_of(queue, pane_id):
    """Where `pane_id` sits in `queue` right now, or None if it is gone."""
    for i, entry in enumerate(queue):
        if _pane(entry) == pane_id:
            return i
    return None


def _move(queue, index, delta):
    j = index + delta
    if 0 <= j < len(queue):
        queue[index], queue[j] = queue[j], queue[index]
        return j
    return index


def _nearest_visible(queue, index, delta, agents):
    """The index nearest to `index` in the direction of `delta` whose pane is
    present in `agents`. None if the direction runs out first."""
    j = index + delta
    while 0 <= j < len(queue):
        if _pane(queue[j]) in agents:
            return j
        j += delta
    return None


def _reorder(queue, index, delta, agents):
    """Swap the entry at `index` with the nearest neighbour in the direction
    of `delta` whose pane `agents` still lists, stepping over any entry
    `agents` doesn't. Mutates `queue` in place and reports whether a swap
    happened; `agents` only chooses which neighbour to swap with; it never
    removes anything from `queue`."""
    target = _nearest_visible(queue, index, delta, agents)
    if target is None:
        return False
    _move(queue, index, target - index)
    return True


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
            queue = _load() if raw is None else _visible(_load(), agents)
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
                for i, entry in enumerate(queue):
                    pane_id = _pane(entry)
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
            elif ch in (ord("J"), ord("K")):
                picked = _pane(queue[sel])
                with _Lock():
                    q = _load()
                    at = _index_of(q, picked)
                    delta = +1 if ch == ord("J") else -1
                    if at is not None and _reorder(q, at, delta, agents):
                        _save(_reindex(q, prune=False))
                seen = _index_of(_visible(q, agents), picked)
                if seen is not None:
                    sel = seen
            elif ch in (ord("x"),):
                _remove(_pane(queue[sel]))
            elif ch in (curses.KEY_ENTER, 10, 13):
                herdr("agent", "focus", _pane(queue[sel]))  # the focus hook clears it
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
