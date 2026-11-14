"""Review player disputes from the repository (a thin wrapper around app.tools.disputes).

    uv run python scripts/disputes/review.py list --status in_review
    uv run python scripts/disputes/review.py escalate <dispute_id>
    uv run python scripts/disputes/review.py rescore <dispute_id> --technique doubt --spans "a|b"
    uv run python scripts/disputes/review.py reject <dispute_id>
    uv run python scripts/disputes/review.py export-dev --out data/dev/disputes_confirmed.jsonl

``scripts/`` is not part of the Docker image; inside the container run the same code with
``docker exec <container> python -m app.tools.disputes <subcommand>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.tools.disputes import main

if __name__ == "__main__":
    raise SystemExit(main())
