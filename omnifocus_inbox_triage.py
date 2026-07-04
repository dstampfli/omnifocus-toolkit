#!/usr/bin/env python3
"""OmniFocus Inbox triage: categorize Inbox tasks into existing projects via Claude."""

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel

# ----------------------------- configuration -----------------------------
MODEL = "claude-opus-4-8"          # Anthropic model id used for classification
MOVE_MIN_CONFIDENCE = "high"       # minimum confidence required to move a task
# --------------------------------------------------------------------------

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


class Decision(BaseModel):
    item_id: str
    project_id: Optional[str] = None
    project_name: str = ""
    confidence: Literal["high", "medium", "low"]
    reason: str = ""


class Classification(BaseModel):
    decisions: List[Decision]


def should_move(decision, valid_item_ids, valid_project_ids,
                min_confidence=MOVE_MIN_CONFIDENCE):
    if decision.project_id is None:
        return False
    if decision.item_id not in valid_item_ids:
        return False
    if decision.project_id not in valid_project_ids:
        return False
    return CONFIDENCE_RANK[decision.confidence] >= CONFIDENCE_RANK[min_confidence]


def partition_decisions(decisions, item_ids, project_ids,
                        min_confidence=MOVE_MIN_CONFIDENCE):
    valid_items = set(item_ids)
    valid_projects = set(project_ids)
    to_move: List[Decision] = []
    to_leave: List[Decision] = []
    seen = set()
    for d in decisions:
        if d.item_id in seen:
            continue
        seen.add(d.item_id)
        if should_move(d, valid_items, valid_projects, min_confidence):
            to_move.append(d)
        else:
            to_leave.append(d)
    return to_move, to_leave
