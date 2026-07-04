# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A macOS automation utility that syncs tagged OmniFocus Inbox tasks into an OmniOutliner document. The real logic lives in `omnifocus_omnioutliner_sync.py`; `main.py` is the unused `uv`-generated placeholder entry point.

## Commands

```bash
python3 omnifocus_omnioutliner_sync.py            # copy matching tasks into OmniOutliner
python3 omnifocus_omnioutliner_sync.py --dry-run  # list what would be copied, write nothing
python3 omnifocus_omnioutliner_sync.py --complete # copy, then mark copied tasks complete in OmniFocus
```

No dependencies (stdlib only), no build step, no tests. `--complete` is ignored when combined with `--dry-run`.

## Architecture

The script is a thin Python wrapper around a single JXA (JavaScript for Automation) program run via `osascript -l JavaScript`:

- **Python side** (`main`): parses CLI flags, serializes config (tag/doc/anchor names + flags) to JSON, invokes `osascript`, then parses the JSON result the JXA prints and formats a human-readable summary.
- **JXA side** (`JXA_TEMPLATE`): the actual work, in four stages — (1) pull OmniFocus inbox tasks whose tags include `TAG_NAME`, (2) find the target OmniOutliner document and the `ANCHOR_ROW` beneath which rows are appended, (3) append one row per task (topic = task name, note = task note), skipping topics already present so re-runs are idempotent, (4) optionally mark copied tasks complete in OmniFocus.

Config is compile-time constants at the top of the file (`TAG_NAME`, `DOC_NAME`, `ANCHOR_ROW`, `DOC_PATH`), not CLI args. All cross-process communication is JSON strings over `osascript`'s argv/stdout.

## Runtime requirements

- macOS with OmniFocus 3/4 and OmniOutliner 5+ installed.
- The target OmniOutliner document must be **open** (or `DOC_PATH` set to a `.ooutline` file the script will open).
- First run prompts for Automation permissions (System Settings → Privacy & Security → Automation) for the terminal app — required for `osascript` to control both apps.
