# Auto-clear polls for focus; herdr's focus event cannot carry it

Auto-clear-on-focus is driven by a companion poll daemon that reads the focused
pane from `herdr agent list` once a second. It is deliberately *not* driven by the
`pane.focused` manifest hook, even though that hook exists, loads without warning,
and does fire. herdr delivers a plugin focus event only when focus moves between
panes **inside one workspace**; when focus moves **between workspaces** it delivers
nothing — not `pane.focused`, not `tab.focused`, not `workspace.focused`. Agents
here get a workspace each, so ordinary agent switching is a workspace change and
reaches no hook at all.

This is written down because the plugin has already made this mistake once. Commit
6b1fa56 deleted a working poll daemon in favour of the hook, and verified the hook
live before shipping it. The verification passed because it changed focus with
`herdr agent focus` — a socket API call, which *does* emit the event. The keyboard
does not. The feature was dead in normal use for three weeks while the code looked
correct and the manifest looked modern.

Measured on herdr 0.9.0 over one day of real use: 21 agent switches from the
keyboard produced **0** hook runs, while 8 pane moves inside a single workspace
produced 8. A probe plugin subscribed to all three focus events at once to confirm
the boundary.

Considered and rejected:

- **Hook `workspace.focused` or `tab.focused` instead** — herdr delivered neither on
  those 21 switches. No manifest event fixes this, so no event-only design works.
- **Drive clearing from `pane.agent_status_changed`**, which herdr does deliver
  reliably — it fires only when some agent changes status, so a quiet session leaves
  the badge wrong for minutes. It is used only to restart a dead daemon, never to
  clear.
- **Wait for herdr to publish a workspace-level plugin event** — the clean fix, worth
  reporting upstream, but it leaves the feature broken for an unknown time.

Consequence: the plugin runs a background process again, and that process is the
only code that removes a pane from the queue automatically. Both manifest hooks are
kept, reduced to one job — make sure the daemon is alive. Before deleting the daemon
again, reproduce the measurement above with the **keyboard**, not with
`herdr agent focus`.
