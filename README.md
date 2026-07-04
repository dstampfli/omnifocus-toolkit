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

## Inbox triage (`omnifocus_inbox_triage.py`)

Reads every OmniFocus Inbox task and uses the Claude API to categorize each one
against your existing **active projects**, then moves confidently-matched tasks
into their project. Low-confidence or unmatched items are left in the Inbox and
reported for manual filing.

Requires an Anthropic API key in addition to the OmniFocus/macOS requirements
above. Install dependencies with `uv sync`.

Configuration lives in a local `.env` file (gitignored). Copy the template and
add your key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

```bash
python omnifocus_inbox_triage.py            # dry-run: classify and report, change nothing
python omnifocus_inbox_triage.py --apply    # classify, then move high-confidence matches
```

`.env` settings (each falls back to a built-in default if omitted):

- `ANTHROPIC_API_KEY` — your Anthropic key (read automatically by the SDK). An
  `ant auth login` profile or an exported env var works too.
- `MODEL` — the classification model id; defaults to `claude-haiku-4-5` for this
  simple task, with `claude-opus-4-8` available as a higher-quality, higher-cost
  alternative.
- `MOVE_MIN_CONFIDENCE` — `high` by default; set to `medium` to also move
  medium-confidence matches.
- `CHUNK_SIZE` — inbox items sent per classification API call; the script
  processes large inboxes in batches so a single call's output never exceeds the
  model's token limit.
