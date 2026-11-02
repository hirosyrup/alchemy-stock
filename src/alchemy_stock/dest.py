"""取ったものの置き場所。手元のフォルダと GCS のバケットを同じ使い方で扱う。

名前は置き場所の中の相対パス (例: pdf/2025/<doc_id>.pdf、tdnet/2026/2026-11-02.jsonl)。
"""
from __future__ import annotations

from pathlib import Path


class LocalDest:
    def __init__(self, root: Path):
        self.root = root

    def exists(self, name: str) -> bool:
        return (self.root / name).exists()

    def save(self, name: str, content: bytes, content_type: str | None = None) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)


class GcsDest:
    """GCS のバケットに置く。prefixes の下に何があるかは最初に1回だけ一覧を取って覚えておく。"""

    def __init__(self, url: str, prefixes: tuple[str, ...]):
        from google.cloud import storage

        self.bucket = storage.Client().bucket(url.removeprefix("gs://").strip("/"))
        self.names = {b.name for prefix in prefixes for b in self.bucket.list_blobs(prefix=prefix)}

    def exists(self, name: str) -> bool:
        return name in self.names

    def save(self, name: str, content: bytes, content_type: str | None = None) -> None:
        self.bucket.blob(name).upload_from_string(content, content_type=content_type)
        self.names.add(name)


def open_dest(where: str, prefixes: tuple[str, ...]) -> LocalDest | GcsDest:
    """where が gs:// ならバケット、それ以外は手元のフォルダ。"""
    return GcsDest(where, prefixes) if where.startswith("gs://") else LocalDest(Path(where))
