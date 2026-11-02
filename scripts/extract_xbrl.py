"""決算短信の XBRL (サマリー) を全部読み、数字を縦長の表にする (PLAN.md §6.1)。

入力は手元のフォルダか gs://バケット/xbrl/ (どちらも <年>/<doc_id>.htm)。出力は3つの gzip の JSONL:
  docs.jsonl.gz   書類ごと (書類の種類・証券コード・決算期末・数字の数・読めなかった理由)
  facts.jsonl.gz  数字ごと (doc_id・項目・期間・区分・期間の始めと終わり・値・単位)
  texts.jsonl.gz  短い文や印ごと (予想の修正の有無など)
どの数字をどう使うかは、検証の宣言文書で決める。

    uv run python scripts/extract_xbrl.py data/xbrl --out data/xbrl_extracted
    uv run python scripts/extract_xbrl.py gs://alchemy-stock-513407-raw/xbrl --out data/xbrl_extracted
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.xbrl import parse  # noqa: E402


def read_files(src: str) -> Iterator[tuple[str, bytes]]:
    """(doc_id, 中身) を順に返す。"""
    if not src.startswith("gs://"):
        for p in sorted(Path(src).glob("*/*.htm")):
            yield p.stem, p.read_bytes()
        return
    from google.cloud import storage

    bucket, _, prefix = src.removeprefix("gs://").partition("/")
    for blob in storage.Client().list_blobs(bucket, prefix=prefix.rstrip("/") + "/"):
        if blob.name.endswith(".htm"):
            yield Path(blob.name).stem, blob.download_as_bytes()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("src", help="XBRL のフォルダか gs://バケット/xbrl")
    p.add_argument("--out", type=Path, default=Path("data/xbrl_extracted"))
    args = p.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    outs = {k: gzip.open(args.out / f"{k}.jsonl.gz.tmp", "wt", encoding="utf-8") for k in ("docs", "facts", "texts")}
    n = errors = 0
    for doc_id, content in read_files(args.src):
        try:
            r = parse(content)
            error = None
        except Exception as e:  # 1件読めなくても止めない。理由を残す
            r, error = {"doc": {}, "facts": [], "texts": []}, repr(e)[:300]
            errors += 1
        outs["docs"].write(json.dumps({"doc_id": doc_id, **r["doc"], "error": error}, ensure_ascii=False) + "\n")
        for k in ("facts", "texts"):
            for row in r[k]:
                outs[k].write(json.dumps({"doc_id": doc_id, **row}, ensure_ascii=False) + "\n")
        n += 1
        if n % 5000 == 0:
            print(f"  {n}件 (読めなかった {errors}件)", flush=True)
    for k, f in outs.items():
        f.close()
        (args.out / f"{k}.jsonl.gz.tmp").replace(args.out / f"{k}.jsonl.gz")
    print(f"{n}件を読んだ (読めなかった {errors}件) → {args.out}")


if __name__ == "__main__":
    main()
