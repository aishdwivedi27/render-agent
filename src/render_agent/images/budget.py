"""Budget guard: refuses a paid call once a dwelling has used its allowance. Called before every
provider.render, and every call (paid or not) is recorded in jobs/<id>/budget.json.

Allowance per dwelling = planned renders for that dwelling (capped by
planning.max_planned_renders_per_dwelling) x (1 + planning.auto_retries_after_failed_check).
Designer-requested regenerations (kind "review") are counted and costed separately, not refused.
An optional round cap (images.max_images_per_round, M5.5) limits paid calls of every kind in the job.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent.errors import PauseJobError
from render_agent.util.fs import atomic_write_json

FILE = "budget.json"
KINDS = {"first", "auto_retry", "review"}


def allowances(planned: dict[str, int], max_per_dwelling: int, auto_retries: int) -> dict[str, int]:
    return {d: min(n, max_per_dwelling) * (1 + auto_retries) for d, n in planned.items()}


class Budget:
    def __init__(self, job_dir: Path, allowance: dict[str, int], round_cap: int | None = None) -> None:
        self.path = job_dir / FILE
        self._lock = threading.Lock()
        data: dict[str, Any] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {"calls": []}
        )
        data["allowance"] = allowance
        data["round_cap"] = round_cap
        self.data = data
        self._save()

    def used(self, dwelling: str, paid_only: bool = True) -> int:
        return sum(
            1
            for c in self.data["calls"]
            if c["dwelling_id"] == dwelling and c["kind"] != "review" and (c["paid"] or not paid_only)
        )

    def paid_total(self) -> int:
        return sum(1 for c in self.data["calls"] if c["paid"])

    def reserve(self, render_id: str, dwelling: str, kind: str, provider: str, paid: bool) -> None:
        """Records the call, or raises PauseJobError(BUDGET_EXHAUSTED) before a paid call over budget."""
        if kind not in KINDS:
            raise ValueError(f"unknown budget kind {kind}")
        with self._lock:
            cap = self.data.get("round_cap")
            if paid and cap is not None and self.paid_total() >= cap:
                raise PauseJobError(
                    code="ROUND_CAP_REACHED",
                    what_happened=(
                        f"This run has used its {cap} paid image calls; {render_id} was not rendered."
                    ),
                    likely_cause="images.max_images_per_round in config.yaml was reached for this round.",
                    what_to_do=(
                        "Finished renders are kept. Raise the cap after approving the cost, then resume."
                    ),
                )
            limit = self.data["allowance"].get(dwelling, 0)
            if paid and kind != "review" and self.used(dwelling) >= limit:
                raise PauseJobError(
                    code="BUDGET_EXHAUSTED",
                    what_happened=(
                        f"{dwelling} has used its {limit} paid image calls; {render_id} was not rendered."
                    ),
                    likely_cause="More renders or retries were attempted than the approved plan allows.",
                    what_to_do="Check budget.json and render_index.json; finished renders are kept.",
                )
            self.data["calls"].append(
                {
                    "render_id": render_id,
                    "dwelling_id": dwelling,
                    "kind": kind,
                    "provider": provider,
                    "paid": paid,
                    "time": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            self._save()

    def summary(self) -> dict[str, Any]:
        return {
            "by_kind": dict(Counter(c["kind"] for c in self.data["calls"] if c["paid"])),
            "paid_total": self.paid_total(),
        }

    def _save(self) -> None:
        atomic_write_json(self.path, self.data)
