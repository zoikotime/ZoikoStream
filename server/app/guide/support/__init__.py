"""Deterministic viewer-support engine (ZST-CONV-PUB-GUIDE-001 §10–15).

The state machine — not the model — owns branching, completion and escalation. The model may
later rephrase a step, but it can never add, skip or reorder one (PG-AT-14, GG-07).
"""
