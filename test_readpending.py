#!/usr/bin/env python3
"""Checks for auto-clear-on-focus.

Run with `python3 test_readpending.py`. Standard library only, same as the
plugin. The state directory is a temporary one and the herdr CLI is replaced, so
nothing here touches a real queue or a real pane.
"""
import curses
import io
import json
import os
import shutil
import sys
import tempfile

STATE = tempfile.mkdtemp(prefix="readpending-test-")
os.environ["HERDR_PLUGIN_STATE_DIR"] = STATE
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readpending as R  # noqa: E402

FAILED = []
CALLS = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name
          + (" — " + detail if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def panes(q):
    return [R._pane(e) for e in q]


class Done:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_herdr(*args):
    """Record herdr calls instead of making them."""
    CALLS.append(args)
    return Done()


R.herdr = fake_herdr


def focus_event(pane_id, queue):
    """One `pane.focused` hook run, shaped the way herdr shapes it."""
    del CALLS[:]
    R._save(queue)
    for key in ("HERDR_ACTIVE_PANE_ID", "HERDR_PANE_ID", "HERDR_PLUGIN_CONTEXT_JSON"):
        os.environ.pop(key, None)
    if pane_id is not None:
        os.environ["HERDR_PANE_ID"] = pane_id
    R.cmd_on_focus()
    return R._load()


print("\nfocusing a pending pane clears it")
left = focus_event("w1:pA", ["w1:pA", "w1:pB"])
check("the focused pane is gone", "w1:pA" not in panes(left), str(left))
check("the other pane stays", panes(left) == ["w1:pB"], str(left))
check("its badge was cleared",
      any(a[:2] == ("pane", "report-metadata") and "--clear-token" in a for a in CALLS),
      str(CALLS))

print("\nthe rest of the queue is renumbered")
R._save(["w1:pA", "w1:pB", "w1:pC"])
os.environ["HERDR_PANE_ID"] = "w1:pA"
del CALLS[:]
R.cmd_on_focus()
badges = [a for a in CALLS if "--token" in a]
check("two badges rewritten", len(badges) == 2, str(badges))
check("they read 1 and 2",
      all(any("=%s%d" % (R.GLYPH, n) in part for part in a) for n, a in enumerate(badges, 1)),
      str(badges))

print("\nfocusing a pane that is not pending changes nothing")
left = focus_event("w1:pZ", ["w1:pA"])
check("the queue is untouched", panes(left) == ["w1:pA"], str(left))
check("no badge was cleared",
      not any("--clear-token" in a for a in CALLS), str(CALLS))

print("\nan event naming no pane is ignored")
left = focus_event(None, ["w1:pA"])
check("the queue is untouched", panes(left) == ["w1:pA"], str(left))
check("it exits cleanly", True)

print("\nthe context blob is used when HERDR_PANE_ID is absent")
del CALLS[:]
R._save(["w1:pA"])
os.environ.pop("HERDR_PANE_ID", None)
os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps({"focused_pane_id": "w1:pA"})
R.cmd_on_focus()
check("the pane named in the context is cleared", panes(R._load()) == [], str(R._load()))
os.environ.pop("HERDR_PLUGIN_CONTEXT_JSON", None)

print("\nthe queue is read through one accessor")
R._save([{"pane": "w1:pA"}, {"pane": "w1:pB"}])
del CALLS[:]
R._reindex(R._load(), prune=False)
badges = [a for a in CALLS if "--token" in a]
check("two badges rewritten", len(badges) == 2, str(badges))
check("they read 1 and 2",
      all(any("=%s%d" % (R.GLYPH, n) in part for part in a) for n, a in enumerate(badges, 1)),
      str(badges))
R._save([{"pane": "w1:pA"}, {"pane": "w1:pB"}])
check("remove of a queued pane returns True", R._remove("w1:pA") is True)
check("the other pane remains", panes(R._load()) == ["w1:pB"], str(R._load()))
check("remove of an absent pane returns False", R._remove("w1:pZ") is False)
check("the queue is unchanged", panes(R._load()) == ["w1:pB"], str(R._load()))
visible = R._visible([{"pane": "w1:pA"}, {"pane": "w1:pB"}], {"w1:pB": {}})
check("_visible keeps only panes herdr still knows about",
      panes(visible) == ["w1:pB"], str(visible))
check("_index_of finds the pane's current slot",
      R._index_of([{"pane": "w1:pA"}, {"pane": "w1:pB"}], "w1:pB") == 1)
check("_index_of returns None when the pane is gone",
      R._index_of([{"pane": "w1:pA"}], "w1:pZ") is None)
q = [{"pane": "w1:pA"}, {"pane": "w1:pB"}]
moved = R._move(q, 0, +1)
check("_move returns the new index", moved == 1, str(moved))
check("_move swaps the entries in place", panes(q) == ["w1:pB", "w1:pA"], str(q))

q = [{"pane": "w1:pA"}]
check("a move with no room to go reports nothing moved",
      R._reorder(q, 0, -1, {"w1:pA": {}}) is False, str(q))
check("the queue is unchanged", panes(q) == ["w1:pA"], str(q))

q = [{"pane": "w1:pA"}, {"pane": "w1:pB"}, {"pane": "w1:pC"}]
agents = {"w1:pA": {}, "w1:pC": {}}  # w1:pB is queued but herdr no longer lists it
check("a move steps over a pane herdr no longer lists",
      R._reorder(q, 0, +1, agents) is True, str(q))
check("the moved entry lands next to the visible neighbour",
      panes(q) == ["w1:pC", "w1:pB", "w1:pA"], str(q))

print("\nthe queue file loads as mark records")
os.makedirs(R.STATE_DIR, exist_ok=True)
with open(R.QUEUE, "w") as f:
    json.dump(["w1:pA", "w1:pB"], f)
loaded = R._load()
check("a pre-mark queue loads as two records",
      panes(loaded) == ["w1:pA", "w1:pB"], str(loaded))
check("every mark is unarmed", all(e["armed"] is False for e in loaded), str(loaded))
check("every mark is 0", all(e["mark"] == 0 for e in loaded), str(loaded))

R._save(R._load())
loaded = R._load()
check("a pre-mark entry keeps mark 0 through a load-save-load round trip",
      all(e["mark"] == 0 for e in loaded), str(loaded))

R._save([R._entry("w1:pA", 7, True)])
loaded = R._load()
check("a saved mark round-trips its id and armed state",
      len(loaded) == 1 and loaded[0]["mark"] == 7 and loaded[0]["armed"] is True,
      str(loaded))

with open(R.QUEUE, "w") as f:
    json.dump(["w1:pA", 3, {"armed": True}, {"pane": "w1:pB", "mark": "x"}], f)
loaded = R._load()
check("the bare int and the paneless record are dropped",
      panes(loaded) == ["w1:pA", "w1:pB"], str(loaded))
check("an unreadable mark falls back to 0",
      loaded[1]["mark"] == 0, str(loaded))

with open(R.QUEUE, "w") as f:
    json.dump([{"pane": "w1:pA", "armed": "no"}], f)
loaded = R._load()
check("a junk armed value does not arm the mark",
      len(loaded) == 1 and loaded[0]["armed"] is False, str(loaded))

with open(R.QUEUE, "w") as f:
    json.dump([{"pane": "w1:pA", "mark": 1e999}], f)
loaded = R._load()
check("an out-of-range mark does not raise and falls back to 0",
      len(loaded) == 1 and loaded[0]["mark"] == 0, str(loaded))

with open(R.QUEUE, "w") as f:
    json.dump([{"pane": "w1:pA", "mark": True}], f)
loaded = R._load()
check("a bool mark is not read as an int",
      len(loaded) == 1 and loaded[0]["mark"] == 0, str(loaded))

check("_next_mark tolerates a hand-built entry with no mark key",
      R._next_mark([{"pane": "w1:pA"}]) > 0)

print("\na fresh mark is unarmed and its id rises")
for key in ("HERDR_ACTIVE_PANE_ID", "HERDR_PLUGIN_CONTEXT_JSON"):
    os.environ.pop(key, None)
os.environ["HERDR_PANE_ID"] = "w1:pA"

R._save([])
R.cmd_toggle()
loaded = R._load()
check("a fresh mark is queued", len(loaded) == 1, str(loaded))
check("it is unarmed", loaded and loaded[0]["armed"] is False, str(loaded))
check("its mark is positive", loaded and loaded[0]["mark"] > 0, str(loaded))

R._save([R._entry("w1:pB", 2 ** 62)])
R.cmd_toggle()
loaded = R._load()
new = next((e for e in loaded if e["pane"] == "w1:pA"), None)
check("a mark below the floor is bumped above it",
      new is not None and new["mark"] > 2 ** 62, str(loaded))

del CALLS[:]
R.cmd_toggle()
loaded = R._load()
check("the already-queued pane is gone, not duplicated",
      panes(loaded) == ["w1:pB"], str(loaded))
check("its badge was cleared",
      any(a[:2] == ("pane", "report-metadata") and "--clear-token" in a for a in CALLS),
      str(CALLS))

print("\nthe daemon arms a mark before it clears it")
sample = R._sample_focus([R._entry("w1:pA", 5), R._entry("w1:pB", 6)],
                         {"w1:pA": {"focused": True}})
check("the sample carries the mark, alive and focused as herdr just reported it",
      sample.get("w1:pA") == (5, True, True), str(sample))
check("a pane herdr no longer lists is sampled as gone",
      sample.get("w1:pB") == (6, False, False), str(sample))

R._clear_overlay_marker()
R._save([R._entry("w1:pA", 5)])
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, True, False)})
loaded = R._load()
check("an unfocused mark clears nothing yet", cleared == [], str(cleared))
check("it stays queued", panes(loaded) == ["w1:pA"], str(loaded))
check("it is now armed", loaded and loaded[0]["armed"] is True, str(loaded))

R._save([R._entry("w1:pA", 5, True), R._entry("w1:pB", 6)])
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, True, True), "w1:pB": (6, True, True)})
loaded = R._load()
check("an armed mark seen focused is reported cleared", cleared == ["w1:pA"], str(cleared))
check("it is gone from the queue", panes(loaded) == ["w1:pB"], str(loaded))
check("its badge was cleared",
      any(a[:2] == ("pane", "report-metadata") and "--clear-token" in a for a in CALLS),
      str(CALLS))
badges = [a for a in CALLS if "--token" in a]
check("the mark left behind is renumbered to 1",
      len(badges) == 1 and any("=%s1" % R.GLYPH in part for part in badges[0]),
      str(badges))

R._save([R._entry("w1:pA", 5)])
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, True, True)})
loaded = R._load()
check("a focused mark that was never armed is not cleared", cleared == [], str(cleared))
check("it stays queued and unarmed",
      panes(loaded) == ["w1:pA"] and loaded[0]["armed"] is False, str(loaded))
check("an unchanged queue writes no badge at all", CALLS == [], str(CALLS))

R._save([R._entry("w1:pA", 9)])
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, True, False)})
loaded = R._load()
check("a sample older than the mark on that pane arms nothing",
      panes(loaded) == ["w1:pA"] and loaded[0]["armed"] is False, str(loaded))
check("and clears nothing", cleared == [] and CALLS == [], str(CALLS))

R._save([R._entry("w1:pA", 5)])
del CALLS[:]
cleared = R._apply_focus_sample({})
loaded = R._load()
check("a mark missing from the sample is untouched",
      panes(loaded) == ["w1:pA"] and loaded[0]["armed"] is False, str(loaded))
check("and nothing is cleared for it", cleared == [] and CALLS == [], str(CALLS))

R._save([R._entry("w1:pA", 5, True)])
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, False, False)})
loaded = R._load()
check("a mark whose pane herdr stopped listing is dropped",
      panes(loaded) == [], str(loaded))
check("no badge is cleared on a pane that is gone",
      not any("--clear-token" in a for a in CALLS), str(CALLS))
check("nothing is reported cleared for it", cleared == [], str(cleared))

R._save([R._entry("w1:pA", 5)])
R._set_overlay_marker()
del CALLS[:]
cleared = R._apply_focus_sample({"w1:pA": (5, True, False)})
loaded = R._load()
check("the overlay marker stops an unfocused mark being armed",
      panes(loaded) == ["w1:pA"] and loaded[0]["armed"] is False, str(loaded))
check("and writes no badge while the overlay is on screen",
      cleared == [] and CALLS == [], str(CALLS))

R._save([R._entry("w1:pA", 5, True)])
cleared = R._apply_focus_sample({"w1:pA": (5, True, True)})
check("an already-armed mark still clears while the overlay is on screen",
      cleared == ["w1:pA"] and panes(R._load()) == [], str(cleared))

R._save([R._entry("w1:pA", 5, True)])
R._apply_focus_sample({"w1:pA": (5, False, False)})
check("a closed pane is still dropped while the overlay is on screen",
      panes(R._load()) == [], str(R._load()))

R._clear_overlay_marker()
R._save([R._entry("w1:pA", 5)])
R._apply_focus_sample({"w1:pA": (5, True, False)})
loaded = R._load()
check("with the overlay gone the same sample arms the mark",
      loaded and loaded[0]["armed"] is True, str(loaded))

SEEN = []


def fake_wrapper(fn):
    """Stand in for curses.wrapper: record whether the marker is on disk while
    the overlay is 'on screen', without touching a terminal."""
    SEEN.append(os.path.exists(R.OVERLAY_MARKER))


def angry_wrapper(fn):
    raise RuntimeError("the overlay blew up")


curses.wrapper = fake_wrapper
R.cmd_ui()
check("the overlay marker exists while the list is on screen", SEEN == [True], str(SEEN))
check("the overlay marker is gone once the list exits",
      not os.path.exists(R.OVERLAY_MARKER))

curses.wrapper = angry_wrapper
try:
    R.cmd_ui()
    raised = False
except RuntimeError:
    raised = True
check("a crash in the overlay is not swallowed", raised)
check("a crash in the overlay leaves no stale marker behind",
      not os.path.exists(R.OVERLAY_MARKER))

print("\nthe daemon is gone")
check("no daemon subcommand", "daemon" not in R.DISPATCH, str(list(R.DISPATCH)))
check("on-focus is dispatchable", "on-focus" in R.DISPATCH, str(list(R.DISPATCH)))
src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "readpending.py"), encoding="utf-8").read()
check("nothing spawns a background process", "Popen" not in src)
check("no poll interval is left behind", "POLL_SECONDS" not in src)

shutil.rmtree(STATE, ignore_errors=True)
print("\n%s — %d of the checks failed"
      % ("FAILED" if FAILED else "PASSED", len(FAILED)))
sys.exit(1 if FAILED else 0)
