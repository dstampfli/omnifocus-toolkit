# Kanban Board plug-in (modified)

A modified copy of the Omni Automation **Kanban Board** plug-in for OmniFocus
(`com.omni-automation.of.kanban-board`). Vendored here for safekeeping.

- **Upstream:** https://omni-automation.com/omnifocus/plug-in-kanban-board.html
- **This copy:** `version 1.2` (upstream was `1.1`)

## What the plug-in does

It is a tag-based board: a parent tag `Kanban` with one child tag per lane. Each
action re-tags the selected task into a lane
(`task.removeTags(Kanban.flattenedChildren); task.addTag(lane)`), and its
**Display Board** action (`Setup`) creates any missing lane tags and opens the
built-in Tags perspective focused on them (`omnifocus:///tag/<childIDs>`) — there
is no separate board window.

## What was changed vs. upstream 1.1

A **`Reviewed`** lane was added, positioned first (the progression is
`Reviewed → To Do → In Progress → Waiting → Done`):

- `Resources/Reviewed.js` — new action, mirrors `ToDo.js`, tags into `Kanban ▸ Reviewed`.
- `Resources/en.lproj/Reviewed.strings` — its `"Reviewed"` label.
- `manifest.json` — new `Reviewed` action with the `checkmark.seal` SF Symbol,
  inserted before `ToDo`; `version` bumped `1.1 → 1.2`.
- `Setup.js` — `tagTitles` is now `["Reviewed", "To Do", "In Progress", "Waiting", "Done"]`,
  so Display Board creates/orders/shows the `Reviewed` lane first.

This pairs with `omnifocus_task_reviewer.py`, which tags reviewed tasks
`Kanban ▸ Reviewed` automatically. The reviewer's read stage skips any task
carrying **any** `Kanban` lane tag, so a task stays skipped as it moves
`Reviewed → To Do → In Progress → Done`.

## Install

1. Zip the bundle (or use a copy you already have), e.g.
   `cd omnifocus_kanban_plugin && zip -r -X ~/Desktop/of-kanban-board.omnifocusjs.zip of-kanban-board.omnifocusjs -x '*.DS_Store'`
2. Unzip on the target Mac and double-click `of-kanban-board.omnifocusjs`.
   OmniFocus prompts to replace the existing plug-in — confirm.
3. Quit and relaunch OmniFocus if it was open, so the new action registers.

The bundle installs to
`~/Library/Containers/com.omnigroup.OmniFocus4/Data/Library/Application Support/Plug-Ins/`.

## Notes

- The `Resources/kanban-*.png` files are decorative assets from upstream; no code
  or the manifest references them, so there is no `kanban-reviewed.png` (the
  action's icon is the `checkmark.seal` SF Symbol named in `manifest.json`).
- The `*.strings` files keep upstream's original key names (`shortLable` /
  `mediumLable` are upstream typos, preserved for consistency).
