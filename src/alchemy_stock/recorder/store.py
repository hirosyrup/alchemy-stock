"""記録の置き場。手元のファイルと GCP (Firestore・GCS) を同じ使い方で扱う。

- 日ごとの記録 (DayLog): 手元は data/recorder/<日付>/<種類>.jsonl、GCP は Firestore の recorder_days/<日付>/<種類>
- 建玉の帳簿 (positions.Positions): 手元は data/recorder/positions.json、GCP は Firestore の recorder_positions
- ファイル (準備の材料・財務情報の蓄え・Saxo の一覧): 手元は data/recorder/ の下、GCP は GCS の RECORDER_GCS の下

環境変数 RECORDER_GCS (例 gs://alchemy-stock-513407-raw/recorder) があれば GCP を使う。
株価を含むので、手元では data/ の下 (Git に入らない)、GCP では自分のプロジェクトの中だけに置く。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOCAL = Path(__file__).resolve().parents[3] / "data/recorder"


def now_jst() -> datetime:
    return datetime.now(JST)


def on_gcp() -> bool:
    return bool(os.environ.get("RECORDER_GCS"))


_fs = None


def firestore():
    global _fs
    if _fs is None:
        from google.cloud import firestore as f

        _fs = f.Client()
    return _fs


# ---------- ファイル ----------
def _gcs():
    from google.cloud import storage

    bucket, _, prefix = os.environ["RECORDER_GCS"].removeprefix("gs://").partition("/")
    return storage.Client().bucket(bucket), prefix.strip("/")


def read_bytes(name: str) -> bytes | None:
    if on_gcp():
        b, prefix = _gcs()
        blob = b.blob(f"{prefix}/{name}")
        return blob.download_as_bytes() if blob.exists() else None
    p = LOCAL / name
    return p.read_bytes() if p.exists() else None


def write_bytes(name: str, content: bytes) -> None:
    if on_gcp():
        b, prefix = _gcs()
        b.blob(f"{prefix}/{name}").upload_from_string(content)
        return
    p = LOCAL / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def list_names(prefix: str) -> list[str]:
    if on_gcp():
        b, root = _gcs()
        return sorted(x.name.removeprefix(f"{root}/") for x in b.list_blobs(prefix=f"{root}/{prefix}"))
    base = LOCAL / prefix
    return sorted(str(p.relative_to(LOCAL)) for p in base.parent.glob(base.name + "*")) if base.parent.exists() else []


def read_json(name: str):
    b = read_bytes(name)
    return json.loads(b) if b is not None else None


def write_json(name: str, obj) -> None:
    write_bytes(name, json.dumps(obj, ensure_ascii=False, default=str).encode())


# ---------- 日ごとの記録 ----------
class DayLog:
    def __init__(self, day: date, root: Path | None = None):
        self.day = day.isoformat()
        self.gcp = on_gcp() and root is None
        if not self.gcp:
            self.dir = (root or LOCAL) / self.day
            self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, rec: dict) -> None:
        rec = {"logged_at": now_jst().isoformat(timespec="milliseconds"), **rec}
        if self.gcp:
            firestore().collection("recorder_days").document(self.day).collection(kind).add(json.loads(json.dumps(rec, default=str)))
            return
        with open(self.dir / f"{kind}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def read(self, kind: str) -> list[dict]:
        if self.gcp:
            docs = firestore().collection("recorder_days").document(self.day).collection(kind).stream()
            return sorted((d.to_dict() for d in docs), key=lambda r: r.get("logged_at", ""))
        p = self.dir / f"{kind}.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
