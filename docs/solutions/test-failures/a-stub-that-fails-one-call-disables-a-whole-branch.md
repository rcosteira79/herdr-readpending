---
module: "n/a"
date: 2026-09-15
problem_type: test_failure
component: testing
severity: medium
symptoms:
  - "a check passes, and deleting the code it names keeps it passing"
  - "a whole branch of production code has no coverage, and no check reports a gap"
  - "a test double returns a default that the code under test reads as an error or an absence"
root_cause: test_isolation
resolution_type: test_fix
tags: [test-doubles, stubs, coverage, false-green, herdr]
---
# Problem

`test_readpending.py` replaces the `herdr` CLI with `fake_herdr`, which recorded the call and
returned a `Done` object whose `stdout` was the empty string. That is a reasonable default
for the calls the suite mostly makes — `pane report-metadata` returns nothing useful.

`agent list` is not one of those calls. `live_agents()` parses its stdout as JSON, and an
empty string fails that parse, so `live_agents()` returned `None` for every check in the
file. `None` is the suite's signal for "the herdr server is unreachable", and `_reindex`
skips pruning when the server is unreachable — deliberately, so a brief outage does not empty
the queue.

So `_reindex(prune=True)` never pruned anywhere in the suite. The branch had no coverage at
all, and nothing reported that. Worse, one check was reading the wrong thing:

    check("the other pane remains", panes(R._load()) == ["w1:pB"])

after removing `w1:pA` from a two-pane queue. It passed because pruning was skipped. With a
realistic agent map that did not list `w1:pB`, `_remove` would have dropped it and the check
would have failed. The check's name described an outcome; the reason behind the outcome was
not the one a reader would assume.

# What Didn't Work

Reading the suite. The stub is nine lines, the default is obviously right for most calls, and
the failure is three function calls away from it. Nothing in the file's output hints at it:
every line said `ok`.

Counting checks did not help either. The file had 110 of them at the point this was found.

# Solution

Give the stub a switchable state, defaulting to the behaviour the existing checks already
depend on, so adding the path does not change what they assert:

    AGENTS = None          # None means "unreachable", which is what every check saw before

    def fake_herdr(*args):
        CALLS.append(args)
        if args[:2] == ("agent", "list") and AGENTS is not None:
            ...             # the real wire shape live_agents parses
        return Done()

`set_agents(*pane_ids)` and `no_agents()` switch it. The prune branch then gets checks both
ways — a pane dropped, a pane kept with `prune=False`, and an unreachable server skipping the
prune rather than emptying the queue — and the checks that depend on pruning name the panes
herdr lists, so they pass for the reason they claim.

# Why This Works

The default preserves every existing assertion's conditions, so the change adds a path
instead of rewriting the file. That matters: the reason this was deferred once already was
that reworking a stub shared by every section reads as unbounded churn.

# Prevention

Two habits, neither expensive.

**Ask what each default a double returns means to the code under test.** Not "is this a
reasonable value" but "which branch does this value select". An empty string, a `None`, an
empty list and a zero are each the error case for something.

**Mutate the code to confirm a check bites.** Delete the line the check names and re-run. If
it still passes, the check is not testing what its name says. This costs one run per check
and it is the only thing that catches a check passing for the wrong reason — a green suite
cannot report it, by definition.
