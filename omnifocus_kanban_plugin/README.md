# Kanban Board plug-in (vendored)

A vendored copy of the Omni Automation **Kanban Board** plug-in for OmniFocus
(`com.omni-automation.of.kanban-board`), kept here so the installed copy can be
refreshed from a known state.

- **Upstream:** https://omni-automation.com/omnifocus/plug-in-kanban-board.html
- **This copy:** `version 1.3` (upstream was `1.1`)

## What the plug-in does

It is a tag-based board: a parent tag `Kanban` with one child tag per lane
(`To Do → In Progress → Waiting → Done`). Each action re-tags the selected task
into a lane (`task.removeTags(Kanban.flattenedChildren); task.addTag(lane)`), and
its **Display Board** action (`Setup`) creates any missing lane tags and opens
the built-in Tags perspective focused on them (`omnifocus:///tag/<childIDs>`) —
there is no separate board window. `omnifocus_kanban_board.py` in this repo
serves a drag-and-drop web board over the same lanes.

## History vs. upstream 1.1

- **1.2** added a `Reviewed` lane as the first column, paired with
  `omnifocus_task_reviewer.py` tagging reviewed tasks `Kanban ▸ Reviewed`.
- **1.3** removed it again. The reviewed pile turned out to be reading material
  (shared links, videos, articles), not board work, and it made up ~60% of the
  cards. The reviewer now tags a **top-level** `Reviewed` tag instead, and the
  board shows only tasks pulled onto it by hand. `Reviewed.js`, its `.strings`
  file, and its manifest action were deleted and `Setup.js`'s lane list went
  back to upstream's four.

Functionally this copy is now upstream 1.1 with a higher version number. The
bump matters: Display Board recreates every lane in its list, so a Mac still
running the 1.2 install would resurrect an empty `Kanban ▸ Reviewed` lane.
**Reinstall 1.3 on any Mac that had 1.2.**

## Install

1. Zip the bundle (or use a copy you already have), e.g.
   `cd omnifocus_kanban_plugin && zip -r -X ~/Desktop/of-kanban-board.omnifocusjs.zip of-kanban-board.omnifocusjs -x '*.DS_Store'`
2. Unzip on the target Mac and double-click `of-kanban-board.omnifocusjs`.
   OmniFocus prompts to replace the existing plug-in — confirm.
3. Quit and relaunch OmniFocus if it was open, so the action list refreshes.

The bundle installs to
`~/Library/Containers/com.omnigroup.OmniFocus4/Data/Library/Application Support/Plug-Ins/`.

## Notes

- The `Resources/kanban-*.png` files are decorative assets from upstream; no code
  or the manifest references them.
- The `*.strings` files keep upstream's original key names (`shortLable` /
  `mediumLable` are upstream typos, preserved for consistency).
