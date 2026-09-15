---
module: "n/a"
date: 2026-09-15
problem_type: convention
component: worker
applies_when:
  - "reading a value off a state file on disk and passing it to a C-typed call such as os.kill"
  - "calling a subprocess from inside the auto-clear daemon's poll loop"
  - "adding any new call to the body of cmd_daemon in readpending.py"
tags: [daemon, poll-loop, exceptions, state-files, restart-loop]
---
# Context

`readpending.py` runs a detached poll daemon that wakes once a second, and two manifest
hooks restart it whenever it is not alive. That pairing turns any exception escaping the
poll body into a restart loop rather than a crash: the daemon dies, the next hook starts a
fresh one, the fresh one reads the same bad value and dies the same way. `_spawn_daemon`
sets `stderr=subprocess.DEVNULL`, so none of it is visible to the reader. The badge simply
stops clearing and nothing says why.

Three separate defects of this one shape were found and fixed between 2026-09-14 and
2026-09-15:

- a mark stored as `inf` in `queue.json`, which made `int()` raise `OverflowError`;
- a pid too large for `pid_t` in `overlay.open`, which made `os.kill` raise `OverflowError`;
- a `herdr` binary that would not launch, which made `subprocess.run` raise
  `FileNotFoundError` — `check=False` suppresses a non-zero exit, never a failure to launch.

The first two came in through a state file a human can edit and a crash can truncate. The
third came in through the environment.

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

# When to Apply

Any new call added to `cmd_daemon`'s loop body, or to anything the loop body reaches. Ask one
question: if this raises, what stops the daemon restarting straight into it? If the answer is
nothing, catch it at the point it is raised and turn it into the failure value the caller
already handles.

# Examples

`herdr()` in `readpending.py` wraps `subprocess.run` in `try/except OSError` and returns a
`CompletedProcess` with a non-zero return code, so `live_agents()` reads an unlaunchable
binary as "unreachable" — which is what its own docstring always promised.

`_pid_alive(pid)` rejects a falsy or negative pid before it calls `os.kill`, and catches
`OverflowError` beside `ProcessLookupError`.

The suite covers all three cases directly: it points `HERDR` at a path that does not exist,
and it calls `_pid_alive` with `-1` and with `2 ** 64`.
