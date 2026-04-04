from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(slots=True)
class ReportWriter:
    root: Path

    def daily_dir(self) -> Path:
        path = self.root / "state" / "reports" / "daily"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def health_path(self) -> Path:
        path = self.root / "state" / "health.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_glance(
        self,
        *,
        status: str,
        main_points: list[str],
        recommended_interventions: list[str],
        track_summary: dict[str, Any],
        approval_pending: list[dict[str, Any]] | None = None,
        date: str | None = None,
    ) -> Path:
        payload = {
            "status": status,
            "mainPoints": main_points,
            "recommendedInterventions": recommended_interventions,
            "trackSummary": track_summary,
            "tuningSummary": {
                "approvalPending": approval_pending or [],
            },
            "generatedAt": datetime.now(UTC).isoformat(),
        }
        return self._write_report("glance", payload, date=date)

    def write_daily(
        self,
        *,
        status: str,
        summary: str,
        sections: list[dict[str, Any]],
        proposals: list[dict[str, Any]] | None = None,
        date: str | None = None,
    ) -> Path:
        payload = {
            "status": status,
            "summary": summary,
            "sections": sections,
            "proposals": proposals or [],
            "generatedAt": datetime.now(UTC).isoformat(),
        }
        return self._write_report("daily", payload, date=date)

    def write_health(
        self,
        *,
        status: str,
        approval_pending: list[dict[str, Any]] | None = None,
        notes: list[str] | None = None,
    ) -> Path:
        payload = {
            "status": status,
            "approvalPending": approval_pending or [],
            "notes": notes or [],
            "generatedAt": datetime.now(UTC).isoformat(),
        }
        path = self.health_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        now = time.time_ns()
        os.utime(path, ns=(now, now))
        return path

    def _write_report(self, kind: str, payload: dict[str, Any], date: str | None = None) -> Path:
        stamp = date or _today()
        path = self.daily_dir() / f"{stamp}-{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        now = time.time_ns()
        os.utime(path, ns=(now, now))
        return path
