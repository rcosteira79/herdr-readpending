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
check("the focused pane is gone", "w1:pA" not in left, str(left))
check("the other pane stays", left == ["w1:pB"], str(left))
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
check("the queue is untouched", left == ["w1:pA"], str(left))
check("no badge was cleared",
      not any("--clear-token" in a for a in CALLS), str(CALLS))

print("\nan event naming no pane is ignored")
left = focus_event(None, ["w1:pA"])
check("the queue is untouched", left == ["w1:pA"], str(left))
check("it exits cleanly", True)

print("\nthe context blob is used when HERDR_PANE_ID is absent")
del CALLS[:]
R._save(["w1:pA"])
os.environ.pop("HERDR_PANE_ID", None)
os.environ["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps({"focused_pane_id": "w1:pA"})
R.cmd_on_focus()
check("the pane named in the context is cleared", R._load() == [], str(R._load()))
os.environ.pop("HERDR_PLUGIN_CONTEXT_JSON", None)

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
