"""ローカルに JSONL で保存する。書き途中で止まっても壊れないよう、一時ファイルから置き換える。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
