# training-inbox

Sync tagged tasks from your **OmniFocus** Inbox into an **OmniOutliner** document.

`omnifocus_omnioutliner_sync.py` reads every task in the OmniFocus Inbox tagged
`training` and appends one row per task under the `_Inbox_` row in the OmniOutliner
document named `Training`. Task notes are copied into each row's note field. Topics
already present under the anchor are skipped, so the script is safe to re-run.

## Requirements

- macOS with OmniFocus 3/4 and OmniOutliner 5+ installed
- The `Training` document **open** in OmniOutliner (simplest), or set `DOC_PATH`
  in the script to the absolute path of a `.ooutline` file it should open
- Python 3.9+ (standard library only — no dependencies to install)
- On first run, macOS prompts for Automation permissions (System Settings →
  Privacy & Security → Automation) for your terminal app

## Usage

```bash
python3 omnifocus_omnioutliner_sync.py            # copy matching tasks into OmniOutliner
python3 omnifocus_omnioutliner_sync.py --dry-run  # list what would be copied, write nothing
python3 omnifocus_omnioutliner_sync.py --complete # copy, then mark copied tasks complete in OmniFocus
```

`--complete` marks only the tasks that were actually copied; duplicates and skipped
tasks are left untouched. It is ignored when combined with `--dry-run`.

## Configuration

Edit the constants at the top of `omnifocus_omnioutliner_sync.py`:

| Constant     | Default      | Meaning                                                        |
| ------------ | ------------ | -------------------------------------------------------------- |
| `TAG_NAME`   | `training`   | Case-insensitive tag name that selects inbox tasks             |
| `DOC_NAME`   | `Training`   | OmniOutliner document name (as shown in its title bar)         |
| `ANCHOR_ROW` | `_Inbox_`    | Row under which new child rows are appended                    |
| `DOC_PATH`   | `""`         | Optional absolute path to a `.ooutline` file to open if closed |

## How it works

The script is a thin Python wrapper around a JXA (JavaScript for Automation)
program executed via `osascript`. The Python side handles CLI flags and formats
output; the JXA side pulls the tagged inbox tasks, locates the anchor row, appends
the new rows (deduping against existing topics), and optionally completes the
copied tasks in OmniFocus. All communication between the two happens as JSON.
