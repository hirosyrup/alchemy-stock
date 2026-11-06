"""記録上の建玉 (注文は出していない「入ったことにした」取引) の帳簿。

手元は data/recorder/positions.json、GCP は Firestore の recorder_positions (1件1文書。朝の 54・S16 と 49 の窓が
別々の job で同時に書くので、ファイル1つにまとめない)。

1件: id, logic (54 / 49 / S16), code, uic, side (buy / sell), entry_day, entry (時刻・値段・Saxo の値段),
exit_rule ({"kind": "close", "day": 日付} = その日の引け、{"kind": "s16", "until": 日付} = 実施・中止の次の始値か期限),
status (open / closed), exit (時刻・値段・理由)。
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import LOCAL, firestore, on_gcp

COLL = "recorder_positions"


class Positions:
    def __init__(self, path: Path | None = None):
        self.gcp = on_gcp() and path is None
        self.path = path or LOCAL / "positions.json"
        self.rows: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.gcp:
            return [d.to_dict() for d in firestore().collection(COLL).stream()]
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def save(self, row: dict | None = None) -> None:
        if self.gcp:
            for r in [row] if row else self.rows:
                firestore().collection(COLL).document(r["id"]).set(json.loads(json.dumps(r, default=str)))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=1, default=str))

    def open_rows(self, logic: str | None = None) -> list[dict]:
        return [r for r in self.rows if r["status"] == "open" and (logic is None or r["logic"] == logic)]

    def blocked(self, logic: str, code: str, day: str) -> bool:
        """同じ会社を持っている間 (手放す日を含む) の新しい出来事は使わない (検証と同じ)。"""
        if self.gcp:
            self.rows = self._load()  # ほかの job が足したものも見る
        for r in self.rows:
            if r["logic"] != logic or r["code"] != code:
                continue
            if r["status"] == "open":
                return True
            if r.get("exit", {}).get("day", "") >= day:
                return True
        return False

    def add(self, row: dict) -> dict:
        row = {"id": f"{row['logic']}-{row['code']}-{row['entry_day']}", "status": "open", **row}
        self.rows.append(row)
        self.save(row)
        return row

    def close(self, pid: str, exit_: dict) -> None:
        for r in self.rows:
            if r["id"] == pid and r["status"] == "open":
                r["status"], r["exit"] = "closed", exit_
                self.save(r)
