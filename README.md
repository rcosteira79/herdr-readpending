# readpending

Mark [herdr](https://herdr.dev) agents you started reading but haven't finished.
For the case: an agent finishes, you jump to it, skim, get pulled away before
reading it all — flag it as **read pending** so you come back.

![Sidebar agents carrying numbered 📖 read-pending badges](images/badges.png)

## What it does

- **Toggle** read-pending on the focused agent (manual, both directions).
- Marked agents get a numbered badge (`📖1`, `📖2`, …) exposed as the pane
  token `$read`, in the order you marked them. This shows in the sidebar and is
  your always-visible, global view of what's pending and in what order.
- A **summon-anywhere overlay list** to reorder the reading queue and jump to
  an agent.
- **Auto-clear on focus**: when you focus a pending agent you're reading it now,
  so its mark is removed and the rest renumber. Marking an agent while you're
  already focused on it does *not* self-clear (only an unfocused→focused
  transition clears).

### How auto-clear works

herdr delivers a plugin focus event only when focus moves between panes
**inside one workspace**; move focus **between workspaces** and it delivers
nothing — not `pane.focused`, not `tab.focused`, not `workspace.focused`.
Every agent here owns a workspace, so ordinary agent switching is a
workspace change and reaches no hook at all. See
[`docs/adr/0001-poll-for-focus-not-events.md`](docs/adr/0001-poll-for-focus-not-events.md)
for the measurement, and for how this plugin already got this wrong once:
commit `6b1fa56` deleted a working poll daemon in favour of the hook and
verified it live before shipping — but that check moved focus with `herdr
agent focus`, a socket call that *does* emit the event. The keyboard doesn't.
The feature was dead in normal use for three weeks while the code looked
correct.

Auto-clear is therefore a companion daemon, `readpending.py daemon`, spawned
detached the first time it's needed. It polls `herdr agent list` once a
second and tracks each queued pane's focus itself: it **arms** a mark once
it has seen that pane unfocused, and **clears** the mark when the pane comes
back into focus — which is why marking the agent you're already reading does
not self-clear; the daemon has to see you leave first.

Both manifest hooks exist only to keep that daemon alive, never to clear
anything:

```toml
[[events]]
on = "pane.focused"
command = ["python3", "readpending.py", "ensure-daemon"]

[[events]]
on = "pane.agent_status_changed"
command = ["python3", "readpending.py", "ensure-daemon"]
```

`ensure-daemon` restarts the daemon if it isn't already running and does
nothing else. `pane.focused` catches the moment the reader opens an agent
inside a workspace; `pane.agent_status_changed` is the most frequent event
herdr publishes reliably otherwise — a quiet session still gets a wake-up
whenever any agent's status changes.

**How long can a dead daemon stay dead?** Three limits, not one — the hooks
shorten the dead window, they do not close it, and none of this is
self-healing.

**One.** Both hooks fire only on activity. A fully quiet session — no focus
change, no agent status change — has no upper bound on how long a dead
daemon stays dead.

**Two.** The single-instance claim is a pidfile plus `os.kill(pid, 0)`,
which proves only that *some* process holds that pid. If the daemon is
killed abruptly and the OS later hands its pid to an unrelated process, both
hooks keep declining to start a watcher until that process ends.

**Three.** The daemon reaches herdr through `subprocess.run` with no
timeout. A herdr that hangs instead of failing leaves the daemon alive,
stuck in that call, still holding the pidfile — the five-consecutive-
failures exit needs the call to *return* — so the hooks see a live pid and
decline to start a replacement.

When you suspect the daemon is dead, tell the cases apart before you touch
anything: read the pid out of `HERDR_PLUGIN_STATE_DIR/daemon.pid` and run
`ps -p <pid> -o command=` to see whether it is really `readpending.py
daemon`.

- **Case one, no pidfile.** Nothing to clean up. Press the toggle key, or
  touch any agent, and a hook starts a fresh daemon.
- **Case two, the pid belongs to something else.** Delete the pidfile, then
  press the toggle key. Do **not** kill that pid — it is not the daemon.
- **Case three, the pid really is a stuck `readpending.py daemon`.** Kill it
  first, then delete the pidfile if it outlives the process, then press the
  toggle key. Deleting the pidfile alone is wrong here: the stuck daemon is
  still alive, may unblock later, and you'd end up with two watchers on one
  queue.

"Press the toggle key" buys the same thing in every case above: the toggle
starts a watcher whenever it leaves anything pending, so it works on any
agent, marked or not, and opening the read-pending list does the same.
Neither starts anything on an empty queue — correctly, since there is
nothing to watch.

- **Case four, `overlay.open` reused.** A different pidfile, the same reuse
  limit. `HERDR_PLUGIN_STATE_DIR/overlay.open` names the pid of the overlay
  process; if the OS hands that pid to an unrelated process, the daemon
  reads the overlay as still on screen and arms nothing, so no badge ever
  clears. `STATE_DIR` survives a reboot and low pids get re-allocated almost
  immediately after one, so a reboot is the likeliest way in. The tell is
  marks that queue up and never clear, however often the reader leaves and
  returns. Fix it the same shape as case two: read the pid out of
  `HERDR_PLUGIN_STATE_DIR/overlay.open`, run `ps -p <pid> -o command=`, and
  delete the file if it is not a `readpending.py ui`. The plugin never
  cleans this up on its own — the reader only ever deletes a marker by
  opening the overlay again, which overwrites it.

One caveat no pidfile surgery fixes: the daemon writes badges through herdr
while it holds the queue lock, so if a *badge* call is what hangs, the
toggle key blocks too — killing the daemon is the only thing that frees it.

## Install

```sh
herdr plugin install rcosteira79/herdr-readpending
```

Or link a local checkout: `herdr plugin link /path/to/herdr-readpending`.
Re-run `install`/`link` after a `herdr update` — updates drop plugins.

### Config (`~/.config/herdr/config.toml`)

Two edits. `herdr server reload-config` after any change.

**1. Show the badge** — the mark is a `$read` pane token; tokens only render if a
sidebar row references them:

```toml
[ui.sidebar.agents]
rows = [["state_icon", "workspace", "tab", "$read"], ["agent"]]
```

**2. Keybindings**:

```toml
[[keys.command]]
key = "prefix+p"
type = "shell"
command = "herdr plugin action invoke toggle --plugin rcosteira.readpending"
description = "toggle read-pending"

[[keys.command]]
key = "prefix+shift+p"
type = "shell"
command = "herdr plugin pane open --plugin rcosteira.readpending --entrypoint list --placement overlay"
description = "read-pending list"
```

Keep the `description` lines. A `#` comment documents the binding for you, but
herdr never reads it — the help panel on `prefix+?` lists a binding with no
`description` as `custom command`, which tells you nothing about what the key
does.

The toggle is also a `pane`-context action, but herdr does **not** surface plugin
actions in its right-click pane menu — the keybinding is the trigger.

## Overlay list keys

![The summon-anywhere read-pending overlay with the reordering queue](images/overlay.png)

```
j / k or ↓ / ↑   move selection
J / K            move the selected agent later / earlier in the queue
enter            jump to the selected agent and close the overlay
x                remove the selected agent from the queue
q / esc          close
```

Re-polls every second while open, to keep the labels and statuses current.
That is display only: auto-clear is the daemon's job, whether the list is
open or not.

## How it works

- Queue: `HERDR_PLUGIN_STATE_DIR/queue.json` (falls back to
  `~/.local/state/herdr/readpending/`), a list of mark records —
  `{"pane": "<pane id>", "armed": <bool>, "mark": <id>}` — mutated under an
  `flock`. A queue of bare pane ids written by an older version of this
  plugin still loads, as unarmed marks.
- Badge: `herdr pane report-metadata <pane> --source rcosteira.readpending
  --token read=📖<n>`; cleared with `--clear-token read`. Position = 1-based
  index in the queue; every queue change renumbers all badges.
- Auto-clear: the poll daemon (`readpending.py daemon`) is the only code
  that removes a pane from the queue automatically — besides arming and
  clearing marks on focus, it drops a mark whose pane herdr has stopped
  listing. Single instance via `HERDR_PLUGIN_STATE_DIR/daemon.pid`; it exits
  after 3 empty polls or 5 consecutive herdr failures, and either manifest
  hook restarts it.

To change the badge glyph/format, edit `GLYPH` / `_set_badge` in
`readpending.py`.

## Requirements

- herdr ≥ 0.8.2 — the version a manifest event hook was confirmed to load
  on. Both `pane.focused` and `pane.agent_status_changed` stay in the
  manifest, but only to wake the auto-clear daemon; see
  [How auto-clear works](#how-auto-clear-works).
- Python 3 (stdlib only; uses `curses` for the overlay)
- macOS or Linux

## The other herdr plugins

Each installs on its own; they share nothing but an author.

- [**herdr-idle-shell-badge**](https://github.com/rcosteira79/herdr-idle-shell-badge) — Badges idle agents that still have a background shell running, so one that *looks* done but left a process alive isn't mistaken for finished.
- [**herdr-account-switch**](https://github.com/rcosteira79/herdr-account-switch) — Hot-swap Claude Code / Codex logins without re-authenticating, with what is left on each account in the picker.
- [**herdr-autocontinue**](https://github.com/rcosteira79/herdr-autocontinue) — Watch agents for usage-limit walls, badge the countdown to the reset, and re-prompt the agents you armed once the window reopens.
