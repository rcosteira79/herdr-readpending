---
module: "n/a"
date: 2026-09-15
problem_type: convention
component: worker
applies_when:
  - "reading a value off a state file on disk and passing it to a C-typed call such as os.kill"
  - "calling a subprocess from inside the auto-clear daemon's poll loop"
  - "adding any new call to the body of cmd_daemon in readpending.py"
  - "parsing a response from another process inside the poll loop, whatever its shape"
  - "widening a try block, or deciding which exception types a poll-path handler catches"
tags: [daemon, poll-loop, exceptions, state-files, restart-loop, parsing, decoding]
---
# Context

`readpending.py` runs a detached poll daemon that wakes once a second, and two manifest
hooks restart it whenever it is not alive. That pairing turns any exception escaping the
poll body into a restart loop rather than a crash: the daemon dies, the next hook starts a
fresh one, the fresh one reads the same bad value and dies the same way. `_spawn_daemon`
sets `stderr=subprocess.DEVNULL`, so none of it is visible to the reader. The badge simply
stops clearing and nothing says why.

Five separate defects of this one shape were found and fixed between 2026-09-14 and
2026-09-15:

- a mark stored as `inf` in `queue.json`, which made `int()` raise `OverflowError`;
- a pid too large for `pid_t` in `overlay.open`, which made `os.kill` raise `OverflowError`;
- a `herdr` binary that would not launch, which made `subprocess.run` raise
  `FileNotFoundError` — `check=False` suppresses a non-zero exit, never a failure to launch;
- a `herdr agent list` response that parsed cleanly and carried the wrong type — `"agents":
  null`, a list of strings, a list holding a null — which made the pane-map comprehension
  raise `TypeError` or `AttributeError`;
- output `subprocess.run(text=True)` could not decode, which raised `UnicodeDecodeError`.

The first two came in through a state file a human can edit and a crash can truncate. The
third came in through the environment. The last two are the ones the first three rules did
not catch, and each names a new shape:

**A guard that stops short of the whole parse is not a guard.** `live_agents` wrapped
`json.loads(...)["result"]["agents"]` in a `try` and then built the pane map on the next
line, outside it. Every exception the `try` listed was already handled; the raise came from
the line the author had decided was safe.

**An implicit conversion raises too.** Nothing in `herdr()` looked like a decode, but
`text=True` decodes stdout and stderr with the locale encoding and `errors='strict'`. The
handler caught `OSError`, and `UnicodeDecodeError` is a `ValueError`, so it went straight
through a function whose docstring promised it never raises.

# Guidance

Treat the poll body as a boundary that must not raise. Two rules follow.

**Values read off disk are untrusted input.** `queue.json`, `daemon.pid` and `overlay.open`
are plain files in a state directory. They survive reboots, they can be hand-edited, and a
crash can leave one half-written. Every value read out of them reaches a C-typed call
eventually, so parse defensively and reject what no OS could have produced — a negative pid
is a process-group probe, not a process, and an oversize one raises rather than returning
`False`.

**A subprocess that cannot launch is the same as a subprocess that failed.** Report it
through the return value the caller already checks, rather than letting it raise. The daemon
already has an exit path for a herdr it cannot reach — five consecutive failures — and that
path only works if the failure comes back as a value.

**A response that parses is not a response you can trust.** Valid JSON says nothing about
types. Put every step that touches the parsed value inside the same `try`, including the one
that only indexes or iterates, and treat a shape the reader cannot use as a server it cannot
reach. Whatever the caller already does with "unreachable" is the right answer.

**Name the exception hierarchy, not the case in front of you.** `OSError` was the right
answer to a missing binary and the wrong answer to a decode. Catching `ValueError` beside it
covers every conversion the same call can perform, including one added later.

# When to Apply

Any new call added to `cmd_daemon`'s loop body, or to anything the loop body reaches. Ask one
question: if this raises, what stops the daemon restarting straight into it? If the answer is
nothing, catch it at the point it is raised and turn it into the failure value the caller
already handles.

Ask a second question of any `try` in the poll path: does it cover the whole operation, or
only the part that looked dangerous? The two shapes above were both missed by a guard the
author had already written.

# Examples

`herdr()` in `readpending.py` wraps `subprocess.run` in `try/except (OSError, ValueError)`,
passes `errors="replace"` so the decode cannot raise in the first place, and returns a
`CompletedProcess` with a non-zero return code — so `live_agents()` reads an unlaunchable
binary and undecodable output alike as "unreachable", which is what its own docstring always
promised.

`live_agents()` builds its pane map inside the `try`, not after it, and catches
`AttributeError` beside `TypeError`, `KeyError` and `json.JSONDecodeError`.

`_pid_alive(pid)` rejects a falsy or negative pid before it calls `os.kill`, and catches
`OverflowError` beside `ProcessLookupError`.

The suite covers all five cases directly: it points `HERDR` at a path that does not exist,
it points `HERDR` at `/bin/sh` and has it print an undecodable byte, it feeds `live_agents`
four wrong-typed responses, and it calls `_pid_alive` with `-1` and with `2 ** 64`.
