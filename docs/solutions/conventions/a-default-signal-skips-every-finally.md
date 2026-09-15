---
module: "n/a"
date: 2026-09-15
problem_type: convention
component: worker
applies_when:
  - "writing a Python process whose cleanup lives in a finally block or a context manager"
  - "creating a pidfile, a lock file, or any marker file another process reads to decide something"
  - "adding a long-lived process that a session manager, a shell, or a shutdown will terminate"
tags: [signals, sigterm, sighup, cleanup, pidfile, marker-files, shutdown]
---
# Context

Python installs no handler for `SIGTERM` or `SIGHUP`. The default disposition ends the
process immediately, without unwinding the stack — so no `finally` runs, no context manager
exits, and no `atexit` handler fires. Only `SIGINT` differs, because Python maps it to
`KeyboardInterrupt`, which is an ordinary exception.

Both long-lived processes in `readpending.py` clean up in a `finally`. `cmd_daemon` removes
its pidfile. `cmd_ui` removes the overlay marker. Both were written on the assumption that
only an abrupt kill would skip that cleanup, and `SIGKILL` was named in the code and the
README as the case the design accepts.

That assumption was wrong about which signal does the skipping. `SIGTERM` is not an abrupt
kill — it is how a process ordinarily dies. A shutdown or reboot sends it to everything. A
session manager sends it when it closes a pane. A `kill` with no flag sends it.

The consequence is not a lost file; it is a file that outlives the process. The daemon's
pidfile survives the shutdown, still naming a pid. The kernel re-issues pids from a low
water mark on the next boot, so that number is likely to belong to something on the next
session. `_ensure_daemon` checks `os.kill(pid, 0)`, finds a live process, and declines to
spawn — for that process's whole lifetime. Auto-clear is dead and nothing says so.

# Guidance

**Decide which signals your cleanup has to survive, and install handlers for them.** A
`finally` block is a promise about the exception path, not about signals. The two are
unrelated, and only one of them is the ordinary case.

**The handler's whole job is to raise.** `sys.exit(0)` raises `SystemExit`, which unwinds
normally, so every existing `finally` runs with no change. Do not move cleanup into the
handler: a signal handler runs between bytecodes, at a moment you did not choose, and code
that takes a lock or writes a file there can deadlock or interleave.

**Install the handler before you create the thing it cleans up.** Otherwise there is a
window where the file is on disk and the signal still skips the removal — small, but it is
the same failure the handler exists to prevent.

**Never assume the handler is enough.** `SIGKILL` cannot be caught, and a power loss catches
nothing. A file another process reads must still be safe to find stale: name the writing pid
in it, and have the reader treat a dead pid as no file at all. The handler makes the stale
case rare; the liveness check is what makes it harmless.

# When to Apply

Any process that outlives one command: a daemon, a watcher, a curses UI, a long-running
build step. Ask what happens to its files at the next shutdown, and whether the answer is
worse than the file simply being absent. A leftover lock or pidfile that suppresses future
work is worse; a leftover cache file is not.

# Examples

`_exit_on_signal(*signums)` in `readpending.py` installs one handler that calls `sys.exit(0)`
and tolerates a platform with no such signal. `cmd_daemon` calls it before it writes the
pidfile; `cmd_ui` calls it before it writes the overlay marker.

The suite reads the disposition that is in force inside each process — `signal.getsignal`,
captured from a stub the process calls while running — rather than signalling the test
runner. An unhandled `SIGTERM` would kill the runner outright, so the obvious check would
take the whole suite down instead of failing a line.

`_overlay_open()` is the other half, and it still matters: the marker names the pid that
wrote it, so a marker whose writer is gone reads as closed whatever the reason.
See `../architecture-patterns/marker-file-suppression.md`.
