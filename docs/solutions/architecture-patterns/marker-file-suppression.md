---
module: "n/a"
date: 2026-09-15
problem_type: architecture_pattern
component: worker
applies_when:
  - "using a file's existence to suppress a background process's behaviour while a foreground process runs"
  - "changing the order of work inside cmd_ui in readpending.py"
  - "adding a second writer to a marker file that is read without a lock"
tags: [marker-file, suppression, races, atomic-write, pid]
related_components:
  - conventions/guard-every-raise-in-the-poll-loop
---
# Context

The read-pending overlay takes focus itself, so while it is on screen herdr reports every
agent unfocused. Left alone, the poll daemon would arm every queued mark off that, then clear
the mark on whichever agent the reader returned to — checking the queue would silently change
it. The fix is a marker file, `overlay.open` in the state directory, that the daemon reads
before it decides whether to arm.

The marker is read without taking the queue lock, because the read is one `stat` and the lock
is held across herdr subprocesses. That makes it cheap and it makes it racy, and three
distinct defects in the protocol were found and fixed on 2026-09-15 before it was correct.

# Guidance

Four rules, each of which was learned by getting it wrong.

**Claim the marker first, before the process does anything else.** The foreground process
already holds focus from the instant it is spawned, so every line that runs before the write
runs with suppression off. It is tempting to write the marker next to the thing it protects —
immediately above the curses call — and that is wrong by however long the setup takes. It is
worse than it looks when the setup *starts the background process*: that was the case here,
and it made the documented recovery for "my marks aren't clearing" the very action that lost
a mark.

**Name the writer's pid in it, and treat a dead pid as absent.** A `finally` does not run on
SIGKILL, and a state directory survives a reboot, so a bare existence check means one abnormal
exit suppresses the behaviour permanently — with no message and a recovery that means deleting
a file no document mentions.

**Write it atomically.** `open(path, "w")` truncates at open and flushes at close, so a
concurrent reader can see a zero-length file and conclude the writer is dead. Write a
temporary file beside it and `os.replace` it into position, exactly as the queue write does.

**Never let the reader delete.** A reader that removes what it judges stale will eventually
remove a live marker a fresh writer has just put there, because a read and an unlink are two
syscalls. The delete buys nothing anyway — a dead pid reads as absent on every later call. Let
the writer's atomic replace do the tidying.

# When to Apply

Any file whose *existence* gates behaviour across two processes without a lock between them.
The four rules compose: pid-stamping without atomic writing still loses to a truncation
window, and atomic writing without dropping the read-side delete still loses to a fresh
writer.

# Examples

`_overlay_open()`, `_set_overlay_marker()` and `cmd_ui()` in `readpending.py`. The suite
asserts the ordering rather than assuming it: the spawn stub records whether the marker was on
disk at the moment the daemon was started, and the check fails on the old ordering.

Two residual limits are recorded rather than solved, both in the plan folder's open questions:
a pid the OS has reused reads as alive, and two overlays open at once defeat a marker that
holds a single pid.
