"""GCS の開示の PDF を、手元で文字 (Markdown) にする。PDF はメモリに読むだけでディスクには残さない。

対象の一覧 (data/targets/*.jsonl の形: doc_id・date・kinds) から、--kinds の種類が付いたものを、
pymupdf4llm で全ページ文字にして data/text/<年>/<doc_id>.md に置く。文字の無いページは文字認識 (OCR) する。
--first に書類の番号の一覧 (1行1つ) を渡すと、それを先にやる。すでにあるもの・GCS にまだ無いものは飛ばす
(取り終わってからもう一度動かせば、残りだけをやる)。

    uv run --group extract python scripts/transcribe_local.py data/targets/documents_v1.jsonl data/targets/documents_v2_story.jsonl \
        --kinds 事業の知らせ 資産の売り買い 提携 買収・事業の売り買い --workers 4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

BUCKET = "alchemy-stock-513407-raw"
OUT = Path("data/text")

_bucket = None


def _init() -> None:
    global _bucket
    from google.cloud import storage
    _bucket = storage.Client(project="alchemy-stock-513407").bucket(BUCKET)


def transcribe(job: tuple[str, str]) -> tuple[str, str]:
    """(doc_id, 年) を文字にする。返り値は (doc_id, 結果: ok / missing / error:...)。"""
    import pymupdf
    import pymupdf4llm

    doc_id, year = job
    out = OUT / year / f"{doc_id}.md"
    blob = _bucket.blob(f"pdf/{year}/{doc_id}.pdf")
    try:
        data = blob.download_as_bytes()
    except Exception:  # GCS にまだ無い
        return doc_id, "missing"
    try:
        with pymupdf.open(stream=data, filetype="pdf") as d:
            text = pymupdf4llm.to_markdown(d)
    except Exception as e:  # 壊れた PDF など
        return doc_id, f"error:{type(e).__name__}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return doc_id, "ok"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", nargs="+", type=Path)
    p.add_argument("--kinds", nargs="+", required=True)
    p.add_argument("--first", type=Path, help="先にやる書類の番号の一覧 (1行1つ)")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    kinds = set(args.kinds)
    jobs = {}
    for path in args.targets:
        for line in path.read_text().splitlines():
            t = json.loads(line)
            if kinds & set(t["kinds"]):
                jobs[t["doc_id"]] = t["date"][:4]
    first = set(args.first.read_text().split()) if args.first else set()
    todo = sorted(jobs.items(), key=lambda kv: (kv[0] not in first, kv[1], kv[0]))
    todo = [j for j in todo if not (OUT / j[1] / f"{j[0]}.md").exists()]
    print(f"対象 {len(jobs):,}件、まだ文字になっていない {len(todo):,}件 (先にやる {len(first & {d for d, _ in todo}):,}件)", flush=True)

    counts: dict[str, int] = {}
    start = time.time()
    with Pool(args.workers, initializer=_init) as pool:
        for i, (_, result) in enumerate(pool.imap(transcribe, todo, chunksize=4), 1):
            key = result.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            if i % 500 == 0 or i == len(todo):
                print(f"{i:,}/{len(todo):,} {counts} {time.time() - start:.0f}秒", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
