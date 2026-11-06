"""記録の書き出し。手元の段階は data/recorder/<日付>/<種類>.jsonl に1行ずつ足す (GCP に移すときに Firestore に替える)。

株価を含むので、data/ の下 (Git に入らない) にだけ置く。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(JST)


class DayLog:
    def __init__(self, day: date, root: Path = Path("data/recorder")):
        self.dir = root / day.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, rec: dict) -> None:
        rec = {"logged_at": now_jst().isoformat(timespec="milliseconds"), **rec}
        with open(self.dir / f"{kind}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def read(self, kind: str) -> list[dict]:
        p = self.dir / f"{kind}.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
