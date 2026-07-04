#!/usr/bin/env python3
"""
OmniFocus -> OmniOutliner sync.

Reads every task in the OmniFocus Inbox tagged TAG_NAME and appends one row
per task under the ANCHOR_ROW ("_Inbox_") in the OmniOutliner document
DOC_NAME ("Training"). Task notes are copied into the row's note field.
Duplicate topics already present under the anchor are skipped, so the
script is safe to re-run.

Requirements:
  - macOS, OmniFocus 3/4 and OmniOutliner 5+ installed
  - The Training document OPEN in OmniOutliner (simplest; or set DOC_PATH)
  - First run will prompt for Automation permissions (System Settings >
    Privacy & Security > Automation) for your terminal app.

Usage:
  python3 of_training_to_oo.py            # copy matching tasks
  python3 of_training_to_oo.py --dry-run  # list what would be copied
  python3 of_training_to_oo.py --complete # copy, then mark tasks complete
                                          # in OmniFocus (copied ones only;
                                          # duplicates/skipped are untouched)
"""

import json
import subprocess
import sys

# ----------------------------- configuration -----------------------------
TAG_NAME = "training"        # case-insensitive match against task tag names
DOC_NAME = "Training"        # OmniOutliner document name (as shown in title bar)
ANCHOR_ROW = "_Inbox_"       # row under which new children are appended
DOC_PATH = ""                # optional: absolute path to .ooutline; if set and
                             # the doc isn't open, the script will open it
# --------------------------------------------------------------------------

JXA_TEMPLATE = r"""
ObjC.import('stdlib');

function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const dryRun = cfg.dryRun;

    // ---------- 1. Pull tagged tasks from the OmniFocus inbox ----------
    const of = Application('OmniFocus');
    of.includeStandardAdditions = true;
    const ofDoc = of.defaultDocument;

    const wanted = cfg.tagName.toLowerCase();
    const items = [];
    const taskRefs = [];   // parallel array of OmniFocus task objects
    const inbox = ofDoc.inboxTasks();

    for (let i = 0; i < inbox.length; i++) {
        const t = inbox[i];
        let tagNames = [];
        try {
            tagNames = t.tags().map(tg => tg.name().toLowerCase());
        } catch (e) { /* task with no tags */ }

        if (tagNames.indexOf(wanted) !== -1) {
            let note = '';
            try { note = t.note() || ''; } catch (e) {}
            items.push({ name: t.name(), note: note });
            taskRefs.push(t);
        }
    }

    if (items.length === 0) {
        return JSON.stringify({ copied: [], skipped: [], message: 'No inbox tasks tagged "' + cfg.tagName + '".' });
    }

    if (dryRun) {
        return JSON.stringify({ copied: items.map(x => x.name), skipped: [], message: 'DRY RUN — nothing written.' });
    }

    // ---------- 2. Locate the OmniOutliner doc and anchor row ----------
    const oo = Application('OmniOutliner');
    oo.includeStandardAdditions = true;

    let target = null;
    const docs = oo.documents();
    for (let i = 0; i < docs.length; i++) {
        if (docs[i].name().replace(/\.ooutline$/, '') === cfg.docName.replace(/\.ooutline$/, '')) {
            target = docs[i];
            break;
        }
    }
    if (!target && cfg.docPath) {
        target = oo.open(Path(cfg.docPath));
    }
    if (!target) {
        throw new Error('OmniOutliner document "' + cfg.docName + '" is not open (and no DOC_PATH set).');
    }

    // "rows" on a document is the flattened list of all rows at any depth.
    const matches = target.rows.whose({ topic: cfg.anchorRow })();
    if (matches.length === 0) {
        throw new Error('Row "' + cfg.anchorRow + '" not found in "' + cfg.docName + '".');
    }
    const anchor = matches[0];

    // Existing child topics, for dedupe / idempotent re-runs.
    const existing = {};
    const kids = anchor.children();
    for (let i = 0; i < kids.length; i++) {
        existing[kids[i].topic()] = true;
    }

    // ---------- 3. Append rows ----------
    const copied = [];
    const skipped = [];
    const toComplete = [];
    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (existing[item.name]) {
            skipped.push(item.name);
            continue;
        }
        const props = { topic: item.name };
        if (item.note) props.note = item.note;
        oo.make({ new: 'row', at: anchor.children.end, withProperties: props });
        copied.push(item.name);
        toComplete.push(taskRefs[i]);
    }

    target.save();

    // ---------- 4. Optionally complete transferred tasks in OmniFocus ----------
    const completed = [];
    if (cfg.complete) {
        for (const t of toComplete) {
            try {
                of.markComplete(t);
                completed.push(t.name());
            } catch (e) { /* leave it in the inbox if completion fails */ }
        }
    }

    return JSON.stringify({ copied: copied, skipped: skipped, completed: completed, message: 'Done.' });
}
"""


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    complete = "--complete" in sys.argv and not dry_run

    cfg = json.dumps({
        "tagName": TAG_NAME,
        "docName": DOC_NAME,
        "anchorRow": ANCHOR_ROW,
        "docPath": DOC_PATH,
        "dryRun": dry_run,
        "complete": complete,
    })

    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", JXA_TEMPLATE, cfg],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("osascript failed:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        print(result.stdout.strip())
        return 0

    print(payload.get("message", ""))
    copied = payload.get("copied", [])
    skipped = payload.get("skipped", [])

    if copied:
        verb = "Would copy" if dry_run else "Copied"
        print(f"\n{verb} {len(copied)} item(s) under {ANCHOR_ROW!r}:")
        for name in copied:
            print(f"  + {name}")
    if skipped:
        print(f"\nSkipped {len(skipped)} duplicate(s) already present:")
        for name in skipped:
            print(f"  = {name}")
    completed = payload.get("completed", [])
    if completed:
        print(f"\nMarked {len(completed)} task(s) complete in OmniFocus:")
        for name in completed:
            print(f"  \u2713 {name}")
    if not copied and not skipped:
        print("Nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
