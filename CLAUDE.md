# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two independent macOS automation utilities for OmniFocus, plus their shared scaffolding:

- `omnifocus_omnioutliner_sync.py` syncs tagged OmniFocus Inbox tasks into an OmniOutliner document.
- `omnifocus_inbox_triage.py` reads every *open* OmniFocus Inbox task and uses the Claude API to classify each one against the user's existing active projects, moving high-confidence matches into their project. Each item's note and any PDF/image attachments are sent to a vision-capable model as part of classification.
- `omnifocus_task_reviewer.py` reviews not-yet-reviewed tasks in named OmniFocus project(s), enriching each in place — fetching any linked page via the model's web_fetch and reading its attachments to produce a clearer title and an appended note summary — then marks it done with a tag (default `reviewed`) so re-runs skip it. Reuses `omnifocus_common`'s helpers.
- `omnifocus_common.py` holds task-enrichment helpers (`clean_note`, `media_type_for`, `attachment_block`, `build_task_content`, `run_jxa`, `fetch_attachment_b64`, `_positive_int_env`) shared by the triage and reviewer scripts.

`main.py` is the unused `uv`-generated placeholder entry point.

## Commands

```bash
python3 omnifocus_omnioutliner_sync.py            # copy matching tasks into OmniOutliner
python3 omnifocus_omnioutliner_sync.py --dry-run  # list what would be copied, write nothing
python3 omnifocus_omnioutliner_sync.py --complete # copy, then mark copied tasks complete in OmniFocus
```

No dependencies (stdlib only), no build step, no tests. `--complete` is ignored when combined with `--dry-run`.

```bash
python3 omnifocus_inbox_triage.py            # dry-run: classify Inbox tasks and report, change nothing
python3 omnifocus_inbox_triage.py --apply    # classify, then move high-confidence matches into their project

uv sync                                      # install the anthropic/pydantic dependencies
uv run pytest                                # run the unit tests
```

`omnifocus_inbox_triage.py` follows a dry-run-by-default / `--apply`-to-write safety model, mirroring `--dry-run` above but inverted (dry-run is the implicit default rather than an opt-in flag).

```bash
python3 omnifocus_task_reviewer.py "Training"          # dry-run: show proposed enrichments
python3 omnifocus_task_reviewer.py "Training" "Tech"   # multiple projects
python3 omnifocus_task_reviewer.py "Training" --apply  # write: rename, append summary, tag reviewed
```

`omnifocus_task_reviewer.py` follows the same dry-run-by-default / `--apply`-to-write model as the triage tool.

## Architecture

`omnifocus_omnioutliner_sync.py` is a thin Python wrapper around a single JXA (JavaScript for Automation) program run via `osascript -l JavaScript`:

- **Python side** (`main`): parses CLI flags, serializes config (tag/doc/anchor names + flags) to JSON, invokes `osascript`, then parses the JSON result the JXA prints and formats a human-readable summary.
- **JXA side** (`JXA_TEMPLATE`): the actual work, in four stages — (1) pull OmniFocus inbox tasks whose tags include `TAG_NAME`, (2) find the target OmniOutliner document and the `ANCHOR_ROW` beneath which rows are appended, (3) append one row per task (topic = task name, note = task note), skipping topics already present so re-runs are idempotent, (4) optionally mark copied tasks complete in OmniFocus.

Config is compile-time constants at the top of the file (`TAG_NAME`, `DOC_NAME`, `ANCHOR_ROW`, `DOC_PATH`), not CLI args. All cross-process communication is JSON strings over `osascript`'s argv/stdout.

`omnifocus_inbox_triage.py` shares the same embedded-JXA-over-`osascript` pattern but inserts a Claude API classification stage between an OmniFocus read and an OmniFocus write, giving it three stages instead of one:

1. **Read** (`READ_JXA` / `read_omnifocus`): JXA pulls every *incomplete* Inbox task (completed tasks are skipped — `inboxTasks()` includes finished items) and every project (with id, name, folder path, status, and its OmniFocus note as `description`); the Python side filters to active projects.
2. **Classify** (`classify`): the items and projects are sent to the Claude API via `client.messages.parse(..., output_format=Classification)`, using structured output (Pydantic models `Decision`/`Classification`) so the model returns one typed decision per Inbox item instead of free text to parse. `build_user_message` sends an ordered list of content blocks — a leading text block with the slimmed project taxonomy (id, name, folderPath, `description`), then per item a text header plus a vision block per in-scope attachment (built via `omnifocus_common.build_task_content`). The project note is the primary signal for what belongs where, taking precedence over the project name. Attachment bytes are extracted via `omnifocus_common.fetch_attachment_b64`, which goes through OmniFocus's OmniJS `evaluateJavascript` bridge because plain JXA cannot read attachment contents; the default model is now `claude-sonnet-5`, which must be vision-capable to read them. Classification is batched: `classify_in_batches` splits items via the size-aware `batch_items_by_size` helper, which flushes a batch once it hits `CHUNK_SIZE` items *or* its in-scope attachment bytes would exceed `MAX_BATCH_ATTACHMENT_BYTES`, calls `classify` once per batch, and concatenates the resulting `decisions` lists — keeping each call's output and attachment payload under the model's limits.
3. **Apply** (`WRITE_JXA` / `apply_moves`): for decisions that clear `MOVE_MIN_CONFIDENCE`, a second JXA program moves each task into its matched project — run only when `--apply` is passed. The move goes through OmniFocus's **Omni Automation (OmniJS) bridge** (`evaluateJavascript` + `moveTasks(task, project.ending)`), matching tasks/projects by identifier. Note: setting `assignedContainer` from JXA is *not* enough — it only marks a pending assignment and does not relocate the task (yet reports success), which is why the OmniJS bridge is used. Only whitelisted ids (already checked against the real OmniFocus id sets) are embedded into the OmniJS source string — never task names, notes, or other free text.

Config (`MODEL`, `MOVE_MIN_CONFIDENCE`, `CHUNK_SIZE`, plus the attachment/note caps `MAX_ATTACHMENT_BYTES`, `MAX_BATCH_ATTACHMENT_BYTES`, `MAX_NOTE_CHARS`) is read and validated at import by `_load_config()` — a bad `.env` value (non-numeric/non-positive `CHUNK_SIZE` or byte/char cap, an unrecognized `MOVE_MIN_CONFIDENCE`) exits with a clear message instead of a raw traceback, and `MOVE_MIN_CONFIDENCE` is case-normalized. `load_dotenv()` runs first so a gitignored `.env` can supply these plus `ANTHROPIC_API_KEY` (which the `anthropic` SDK reads from the environment directly — no code passes it). `.env.example` is the committed template. All cross-process communication is JSON over `osascript`'s argv/stdout. Unlike the sync script, the pure decision/parsing/prompt-building/reporting logic (everything except the two JXA calls, `fetch_attachment_b64`, and the live API call) is unit-tested under `tests/` — this includes `batch_items_by_size`, but not `classify_in_batches`, which is thin I/O orchestration over the untested `classify`. `omnifocus_common.py`'s pure helpers (`clean_note`, `media_type_for`, `attachment_block`, `build_task_content`) are likewise unit-tested; `run_jxa` and `fetch_attachment_b64` are the I/O boundary and are not.

`omnifocus_task_reviewer.py` reuses the same embedded-JXA-over-`osascript` pattern for a three-stage pipeline scoped to named projects instead of the Inbox: **read** (`READ_TASKS_JXA`) walks each named project's tasks entirely in OmniJS — skipping completed, dropped, and already-tagged (`REVIEW_TAG`) tasks — and returns each task's id, name, note, and attachment metadata; **review** (`review_task`) makes one `client.beta.messages.parse(..., output_format=Enrichment)` call per task with the `web_fetch` beta tool (capped at `WEB_FETCH_MAX_USES` uses) enabled, so the model can follow a URL in the note before producing a typed `Enrichment` (`new_title`, `summary`) — failures are caught per task (`review_tasks`) so one bad task never aborts the run; **apply** (`WRITE_JXA` / `apply_enrichments`), run only under `--apply`, sets `name`/`note` via plain JXA argv (the model's free text never enters an OmniJS source string) and then calls the OmniJS bridge solely with the whitelisted task ids and the `REVIEW_TAG` string to add the tag. `MAX_ATTACHMENT_BYTES`/`MAX_NOTE_CHARS` are shared with the triage tool's config; `REVIEW_TAG` and `WEB_FETCH_MAX_USES` are reviewer-specific, validated the same way (`_positive_int_env`, now defined in `omnifocus_common.py` and imported by both `omnifocus_inbox_triage.py` and `omnifocus_task_reviewer.py`).

## Runtime requirements

- macOS with OmniFocus 3/4 and OmniOutliner 5+ installed.
- The target OmniOutliner document must be **open** (or `DOC_PATH` set to a `.ooutline` file the script will open) — only relevant to `omnifocus_omnioutliner_sync.py`.
- First run prompts for Automation permissions (System Settings → Privacy & Security → Automation) for the terminal app — required for `osascript` to control OmniFocus (and OmniOutliner, for the sync script).
- `omnifocus_inbox_triage.py` additionally requires the `anthropic` and `python-dotenv` packages (installed via `uv sync`) and an Anthropic API key. Provide the key via a local `.env` (copy `.env.example`), an exported `ANTHROPIC_API_KEY`, or an active `ant auth login` profile.

## Agent skills

### Issue tracker

Issues and PRDs are tracked as local markdown files under `.scratch/<feature>/` (no remote tracker). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
