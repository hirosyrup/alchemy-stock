"""TDnet の直近31日分の開示一覧 (時刻つき) を、日付ごとのファイルに貯める。

毎日1回動かす想定 (Cloud Run のジョブ、cloud/collect_tdnet.sh)。既に取った日は飛ばし、
直近数日 (後から開示が足される・訂正される) だけ取り直す。保存先は手元のフォルダか gs://バケット。

    uv run python scripts/collect_tdnet.py
    uv run python scripts/collect_tdnet.py --date 2026-10-30
    python scripts/collect_tdnet.py --dest gs://alchemy-stock-513407-raw    # Cloud Run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.dest import open_dest  # noqa: E402
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.store import now_utc  # noqa: E402
from alchemy_stock.tdnet import fetch_day  # noqa: E402

WINDOW_DAYS = 31


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dest", default="data", help="保存先のフォルダか gs://バケット (その下の tdnet/YYYY/ に置く)")
    p.add_argument("--date", type=date.fromisoformat, help="この日だけ取る")
    p.add_argument("--refresh-recent", type=int, default=3, help="直近この日数は取り直す")
    p.add_argument("--interval", type=float, default=3.0)
    args = p.parse_args()

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    days = [args.date] if args.date else [today - timedelta(days=i) for i in range(WINDOW_DAYS)]
    dest = open_dest(args.dest, prefixes=("tdnet/",))
    session = PoliteSession(min_interval=args.interval)
    for day in sorted(days):
        name = f"tdnet/{day:%Y}/{day.isoformat()}.jsonl"
        if dest.exists(name) and not args.date and (today - day).days >= args.refresh_recent:
            continue
        try:
            rows = fetch_day(session, day)
        except Blocked as e:
            print(f"拒否されたので止めます: {e}", file=sys.stderr)
            return 2
        fetched_at = now_utc()
        body = "".join(json.dumps({**r, "fetched_at": fetched_at}, ensure_ascii=False) + "\n" for r in rows)
        dest.save(name, body.encode("utf-8"), "application/json")
        print(f"{day} {len(rows)}件", flush=True)
    print(f"読み込み {session.count} 回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
