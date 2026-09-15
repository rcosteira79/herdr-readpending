#!/usr/bin/env python3
"""Checks for auto-clear-on-focus.

Run with `python3 test_readpending.py`. Standard library only, same as the
plugin. The state directory is a temporary one and the herdr CLI is replaced, so
nothing here touches a real queue or a real pane.
"""
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

print("\nthe queue file loads as mark records")
os.makedirs(R.STATE_DIR, exist_ok=True)
with open(R.QUEUE, "w") as f:
    json.dump(["w1:pA", "w1:pB"], f)
loaded = R._load()
check("a pre-mark queue loads as two records",
      panes(loaded) == ["w1:pA", "w1:pB"], str(loaded))
check("every mark is unarmed", all(e["armed"] is False for e in loaded), str(loaded))
check("every mark is 0", all(e["mark"] == 0 for e in loaded), str(loaded))

first, second = R._load(), R._load()
check("a pre-mark entry keeps the same mark across loads", first == second, str((first, second)))

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
