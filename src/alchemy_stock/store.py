"""ローカルに JSONL で保存する。書き途中で止まっても壊れないよう、一時ファイルから置き換える。"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(path: Path, mode: str, gz: bool):
    # 株価のように量が多いものは gzip で読み書きする (ファイル名が .gz で終わるもの)
    return gzip.open(path, mode + "t", encoding="utf-8") if gz else path.open(mode, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with _open(tmp, "w", gz=path.name.endswith(".gz")) as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    with _open(path, "r", gz=path.name.endswith(".gz")) as f:
        return [json.loads(line) for line in f if line.strip()]
