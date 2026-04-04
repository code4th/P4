from __future__ import annotations

from pathlib import Path


class OpenClawAdapterBoundary:
    """Declares the intended one-way boundary for the first P1 integration."""

    def __init__(self, p1_root: Path) -> None:
        self.p1_root = p1_root

    def report_paths(self) -> dict[str, Path]:
        daily = self.p1_root / "state" / "reports" / "daily"
        return {
            "glance": daily,
            "daily": daily,
            "health": self.p1_root / "state" / "health.json",
            "intervene": self.p1_root / "intervene.js",
        }
