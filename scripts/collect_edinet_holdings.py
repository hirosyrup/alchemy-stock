"""EDINET の書類一覧から、大量保有報告書 (350)・変更報告書 (360)・それらの訂正 (370 など、府令 060) を日ごとに取り、
data/edinet/holdings/YYYY-MM-DD.json に置く。EDINET は大量保有報告書を 5年で一覧から消すので、消える前に取っておく。
キーは .env の EDINET_API_KEY。すでにある日は飛ばす (毎日動かせば増えた分だけ)。
    uv run python scripts/collect_edinet_holdings.py 2021-11-01 2026-11-03
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import PoliteSession  # noqa: E402

URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
OUT = Path("data/edinet/holdings")


def load_key() -> str:
    for line in open(".env"):
        if line.startswith("EDINET_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(".env に EDINET_API_KEY が無い")


def main() -> None:
    start, end = (date.fromisoformat(a) for a in sys.argv[1:3])
    key, s = load_key(), PoliteSession(min_interval=1.0)
    OUT.mkdir(parents=True, exist_ok=True)
    d, got = start, 0
    while d <= end:
        path = OUT / f"{d}.json"
        if not path.exists():
            j = s.get(URL, params={"date": str(d), "type": 2, "Subscription-Key": key}).json()
            if j["metadata"]["status"] != "200":
                raise SystemExit(f"{d}: {j['metadata']}")
            rows = [x for x in j["results"] if x.get("ordinanceCode") == "060"]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": str(d), "count_all": j["metadata"]["resultset"]["count"], "results": rows}, ensure_ascii=False))
            tmp.replace(path)
            got += 1
            if got % 50 == 0:
                print(d, got, flush=True)
        d += timedelta(days=1)
    print("取った日", got)


if __name__ == "__main__":
    main()
