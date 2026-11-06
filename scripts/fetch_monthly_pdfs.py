"""月次の売上・営業の知らせ (題名に「月次」「月度…売上」「売上高速報」「営業概況」「既存店」) の PDF を JPX から取り、data/pdf_monthly/<年>/<doc_id>.pdf に置く。
訂正は除く。1秒おきに1件ずつ。すでにあるものは飛ばす。拒否 (403・429) されたら止める。
    uv run python scripts/fetch_monthly_pdfs.py 2024-07-01 2025-10-31
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import PoliteSession  # noqa: E402

OUT = Path("data/pdf_monthly")
KEEP = re.compile(r"月次|月度.*売上|売上高速報|営業概況|既存店")


def main() -> None:
    start, end = sys.argv[1:3]
    rows = []
    for f in Path("data/jpx_listed/disclosures").glob("*.jsonl"):
        for line in open(f):
            x = json.loads(line)
            if start <= x["date"] <= end and KEEP.search(x["title"]) and "訂正" not in x["title"] and x.get("pdf_url"):
                rows.append(x)
    rows.sort(key=lambda x: x["date"])
    print("対象", len(rows), flush=True)
    s = PoliteSession(min_interval=1.0)
    got = 0
    for x in rows:
        path = OUT / x["date"][:4] / f"{x['doc_id']}.pdf"
        if path.exists():
            continue
        r = s.get(x["pdf_url"], allow_404=True)
        if r is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        got += 1
        if got % 200 == 0:
            print(x["date"], got, flush=True)
    print("取った", got, flush=True)


if __name__ == "__main__":
    main()
