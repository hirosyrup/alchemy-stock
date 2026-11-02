"""開示の PDF (と、あれば XBRL) を東証上場会社情報サービスから取ってくる。

XBRL は、決算短信の1ページ目 (サマリー) を数字に印を付けた HTML (インライン XBRL) にしたもの。

doc_id を含む JSONL を渡すと、その開示だけを取る。JSONL に pdf_url / xbrl_url があればそれを使い、
無ければ手元の東証上場会社情報サービスの一覧から探す。JSONL は gs:// でもよい。
保存先 (--dest) は手元のフォルダか gs://バケット で、どちらも pdf/YYYY/<doc_id>.pdf、xbrl/YYYY/<doc_id>.htm に置く。
既にあるものは飛ばすので、止まっても同じコマンドで続きから再開できる。

    uv run python scripts/fetch_documents.py data/prep/sample_v1.jsonl --xbrl
    python scripts/fetch_documents.py gs://alchemy-stock-513407-raw/targets/documents_v1.jsonl \
        --dest gs://alchemy-stock-513407-raw --xbrl --interval 1 --workers 3     # Cloud Run (cloud/fetch_documents.sh)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.store import read_jsonl  # noqa: E402

DATA = Path("data")


class LocalDest:
    def __init__(self, root: Path):
        self.root = root

    def exists(self, name: str) -> bool:
        return (self.root / name).exists()

    def save(self, name: str, content: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)


class GcsDest:
    """GCS のバケットに置く。何があるかは最初に1回だけ一覧を取って覚えておく。"""

    def __init__(self, url: str):
        from google.cloud import storage

        self.bucket = storage.Client().bucket(url.removeprefix("gs://").strip("/"))
        self.names = {b.name for prefix in ("pdf/", "xbrl/") for b in self.bucket.list_blobs(prefix=prefix)}

    def exists(self, name: str) -> bool:
        return name in self.names

    def save(self, name: str, content: bytes) -> None:
        content_type = "application/pdf" if name.endswith(".pdf") else "text/html"
        self.bucket.blob(name).upload_from_string(content, content_type=content_type)
        self.names.add(name)


def read_targets(src: str) -> list[dict]:
    if not src.startswith("gs://"):
        return read_jsonl(Path(src))
    from google.cloud import storage

    bucket, _, name = src.removeprefix("gs://").partition("/")
    text = storage.Client().bucket(bucket).blob(name).download_as_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def with_urls(targets: list[dict]) -> dict[str, dict]:
    """doc_id → pdf_url / xbrl_url / date。無ければ手元の一覧から探す。"""
    if all("pdf_url" in r for r in targets):
        return {r["doc_id"]: r for r in targets}
    wanted = {r["doc_id"] for r in targets}
    urls: dict[str, dict] = {}
    for f in sorted((DATA / "jpx_listed" / "disclosures").glob("*.jsonl")):
        for r in read_jsonl(f):
            if r["doc_id"] in wanted:
                urls.setdefault(r["doc_id"], r)
    return urls


class Progress:
    def __init__(self, total: int, every: int):
        self.total, self.every = total, every
        self.lock = threading.Lock()
        self.done = self.missing = self.failed = self.bytes = self.files = 0

    def add(self, size: int | None, failed: bool = False) -> None:
        with self.lock:
            self.files += 1
            if failed:
                self.failed += 1
            elif size is None:
                self.missing += 1
            else:
                self.done += 1
                self.bytes += size
            if self.files % self.every == 0:
                print(f"  {self.files}/{self.total} ファイル ({self.bytes / 1e6:.1f}MB)", flush=True)


def worker(jobs: list[tuple[str, str]], dest, interval: float, progress: Progress, stop: threading.Event) -> None:
    """1本分。自分の分を1件ずつ、interval 秒以上あけて取る。どれかが拒否されたら (stop) すぐやめる。"""
    session = PoliteSession(min_interval=interval)
    for url, name in jobs:
        if stop.is_set():
            return
        try:
            res = session.get(url, allow_404=True)
        except Blocked as e:
            print(f"拒否されたので全部止めます: {e}", flush=True)
            stop.set()
            return
        except Exception as e:  # やり直しても通らなかったもの。飛ばして、次に動かした時に取り直す
            print(f"  取れなかった: {url} {e!r}", flush=True)
            progress.add(None, failed=True)
            continue
        if res is None:
            print(f"  見つからない: {url}", flush=True)
            progress.add(None)
            continue
        dest.save(name, res.content)
        progress.add(len(res.content))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", help="doc_id を含む JSONL (gs:// も可)")
    p.add_argument("--dest", default=str(DATA), help="保存先のフォルダか gs://バケット")
    p.add_argument("--xbrl", action="store_true", help="XBRL があれば一緒に取る")
    p.add_argument("--interval", type=float, default=3.0, help="1本あたりの読み込みの間隔 (秒)")
    p.add_argument("--workers", type=int, default=1, help="同時に取る本数。全体では1秒に workers / interval 回まで")
    p.add_argument("--progress-every", type=int, default=50)
    args = p.parse_args()

    targets = read_targets(args.targets)
    urls = with_urls(targets)
    dest = GcsDest(args.dest) if args.dest.startswith("gs://") else LocalDest(Path(args.dest))
    jobs = []
    for doc_id, r in sorted(urls.items()):
        jobs.append((r["pdf_url"], f"pdf/{r['date'][:4]}/{doc_id}.pdf"))
        if args.xbrl and r.get("xbrl_url"):
            jobs.append((r["xbrl_url"], f"xbrl/{r['date'][:4]}/{doc_id}{Path(r['xbrl_url']).suffix}"))
    todo = [(url, name) for url, name in jobs if not dest.exists(name)]
    print(f"対象 {len(targets)}件 (URL が分かったもの {len(urls)}件)、ファイル {len(jobs)}件のうち未取得 {len(todo)}件、{args.workers}本で取る", flush=True)

    progress = Progress(len(todo), args.progress_every)
    stop = threading.Event()
    threads = [
        threading.Thread(target=worker, args=(todo[i :: args.workers], dest, args.interval, progress, stop))
        for i in range(args.workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"新しく保存 {progress.done}件 / 見つからない {progress.missing}件 / 取れなかった {progress.failed}件 / {progress.bytes / 1e6:.1f}MB")
    if stop.is_set():
        sys.exit(2)


if __name__ == "__main__":
    main()
