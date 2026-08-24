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

herdr fires a plugin hook on focus, so auto-clear is declarative. The manifest
asks for it:

```toml
[[events]]
on = "pane.focused"
command = ["python3", "readpending.py", "on-focus"]
```

herdr runs that on every focus change and names the pane that **gained** focus,
in `HERDR_PANE_ID` and in the context blob's `focused_pane_id`. The command drops
that pane from the queue if it is in it. No process runs between focus changes.

Spell the event with dots. herdr's API schema lists the same kinds with
underscores, and the manifest turns `pane_focused` down with `unknown event`.
That spelling is why earlier versions of this plugin said no focus trigger
existed and shipped a polling daemon instead. Verified on herdr 0.8.2, which is
why `min_herdr_version` says 0.8.2.

A missed event costs a badge that lingers, and the next focus of that pane
clears it — so there is no safety poll and no daemon to supervise.

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
That is display only: auto-clear is the focus hook's job, whether the list is
open or not.

## How it works

- Queue: `HERDR_PLUGIN_STATE_DIR/queue.json` (falls back to
  `~/.local/state/herdr/readpending/`), an ordered list of pane ids mutated
  under an `flock`.
- Badge: `herdr pane report-metadata <pane> --source rcosteira.readpending
  --token read=📖<n>`; cleared with `--clear-token read`. Position = 1-based
  index in the queue; every queue change renumbers all badges.
- Auto-clear: herdr's `pane.focused` hook runs the `on-focus` subcommand, which
  calls the shared remove path for the pane that gained focus.
- Dead panes (closed) are pruned on the next toggle or list refresh.

To change the badge glyph/format, edit `GLYPH` / `_set_badge` in
`readpending.py`.

## Requirements

- herdr ≥ 0.7.4
- Python 3 (stdlib only; uses `curses` for the overlay)
- macOS or Linux

## The other herdr plugins

Each installs on its own; they share nothing but an author.

- [**herdr-idle-shell-badge**](https://github.com/rcosteira79/herdr-idle-shell-badge) — Badges idle agents that still have a background shell running, so one that *looks* done but left a process alive isn't mistaken for finished.
- [**herdr-account-switch**](https://github.com/rcosteira79/herdr-account-switch) — Hot-swap Claude Code / Codex logins without re-authenticating, with what is left on each account in the picker.
- [**herdr-autocontinue**](https://github.com/rcosteira79/herdr-autocontinue) — Watch agents for usage-limit walls, badge the countdown to the reset, and re-prompt the agents you armed once the window reopens.
