"""A local record of what each maintainer's triage runs cost.

The authoritative cap is Otari's: each maintainer is an end user on a service
key with a budget of their own, and a key ceiling caps the whole team. This
ledger is the second layer. It stops a run before it starts when the maintainer
has less left today than one issue may cost, so a flood of spam issues is turned
away without even reaching the gateway, and it gives the dashboard a history
without needing operator access to Otari's usage API.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Allowance:
    maintainer: str
    spent_today: float
    daily_limit: float
    issue_limit: float

    @property
    def remaining(self) -> float:
        return max(0.0, self.daily_limit - self.spent_today)

    @property
    def can_start(self) -> bool:
        return self.remaining >= self.issue_limit


class Ledger:
    def __init__(self, home: Path):
        self.path = Path(home) / "ledger.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": []}
        try:
            return json.loads(self.path.read_text())
        except ValueError:
            return {"runs": []}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def spent_today(self, maintainer: str) -> float:
        day = _today()
        return sum(
            float(run.get("cost_usd", 0.0))
            for run in self._read()["runs"]
            if run.get("maintainer") == maintainer and str(run.get("at", "")).startswith(day)
        )

    def allowance(self, maintainer: str, daily_limit: float, issue_limit: float) -> Allowance:
        return Allowance(maintainer, self.spent_today(maintainer), daily_limit, issue_limit)

    def record(self, maintainer: str, issue_ref: str, cost_usd: float, outcome: str, report_id: str | None = None) -> None:
        with _LOCK:
            data = self._read()
            data["runs"].append(
                {
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "maintainer": maintainer,
                    "issue": issue_ref,
                    "cost_usd": round(cost_usd, 6),
                    "outcome": outcome,
                    "report": report_id,
                }
            )
            self._write(data)

    def runs(self, maintainer: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        runs = self._read()["runs"]
        if maintainer:
            runs = [r for r in runs if r.get("maintainer") == maintainer]
        return list(reversed(runs))[:limit]

    def by_maintainer(self) -> dict[str, float]:
        day = _today()
        totals: dict[str, float] = {}
        for run in self._read()["runs"]:
            if str(run.get("at", "")).startswith(day):
                totals[run["maintainer"]] = totals.get(run["maintainer"], 0.0) + float(run.get("cost_usd", 0.0))
        return totals
