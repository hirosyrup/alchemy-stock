"""自社株買いの「取得に係る事項の決定」と「取得状況・取得結果・取得終了」の開示の PDF を JPX から取り、data/pdf_buyback/<年>/<doc_id>.pdf に置く。
ToSTNeT-3・公開買付・端数の買取り・処分・消却・訂正は除く。1秒おきに1件ずつ。すでにあるものは飛ばす。拒否 (403・429) されたら止める。
    uv run python scripts/fetch_buyback_pdfs.py 2016-10-01 2021-10-31
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import PoliteSession  # noqa: E402

OUT = Path("data/pdf_buyback")
KEEP = re.compile(r"自己株式.*(取得.*決定|取得状況|取得結果|取得終了)")
DROP = re.compile(r"ToSTNeT|ＴｏＳＴＮｅＴ|立会外|公開買付|端数|処分|消却|訂正")


def main() -> None:
    start, end = sys.argv[1:3]
    rows = []
    for f in Path("data/jpx_listed/disclosures").glob("*.jsonl"):
        for line in open(f):
            x = json.loads(line)
            if start <= x["date"] <= end and KEEP.search(x["title"]) and not DROP.search(x["title"]) and x.get("pdf_url"):
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
