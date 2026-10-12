"""ZoikoStream Guide evaluation harness (ZST-AI-EVAL-PUB-GUIDE-001).

Runs versioned test cases through the REAL Guide pipeline (session -> boundary -> routing ->
retrieval -> model -> validator -> persistence) and scores them with deterministic assertions
first, retrieval metrics second, and release gates last. It is tooling, not application code:
nothing in app/ imports it.

    cd server
    venv/Scripts/python -m guide_eval                         # all suites, mock model
    venv/Scripts/python -m guide_eval --provider groq --pace 8 --suite G1_product --limit 10
    venv/Scripts/python -m guide_eval --list                  # dataset inventory

Reports land in guide_eval/reports/ (JSON + Markdown); that folder is git-ignored.
"""

HARNESS_VERSION = "1.0.0"
