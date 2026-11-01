"""TDnet の直近31日分の開示一覧 (時刻つき) を、日付ごとのファイルに貯める。

毎日1回動かす想定。既に取った日は飛ばし、直近数日 (後から開示が足される・訂正される) だけ取り直す。

    uv run python scripts/collect_tdnet.py
    uv run python scripts/collect_tdnet.py --date 2026-10-30
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked, PoliteSession  # noqa: E402
from alchemy_stock.store import now_utc, write_jsonl  # noqa: E402
from alchemy_stock.tdnet import fetch_day  # noqa: E402

WINDOW_DAYS = 31


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("data/tdnet"))
    p.add_argument("--date", type=date.fromisoformat, help="この日だけ取る")
    p.add_argument("--refresh-recent", type=int, default=3, help="直近この日数は取り直す")
    p.add_argument("--interval", type=float, default=3.0)
    args = p.parse_args()

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    days = [args.date] if args.date else [today - timedelta(days=i) for i in range(WINDOW_DAYS)]
    session = PoliteSession(min_interval=args.interval)
    for day in sorted(days):
        path = args.out / f"{day:%Y}" / f"{day.isoformat()}.jsonl"
        if path.exists() and not args.date and (today - day).days >= args.refresh_recent:
            continue
        try:
            rows = fetch_day(session, day)
        except Blocked as e:
            print(f"拒否されたので止めます: {e}", file=sys.stderr)
            return 2
        fetched_at = now_utc()
        write_jsonl(path, [{**r, "fetched_at": fetched_at} for r in rows])
        print(f"{day} {len(rows)}件")
    print(f"読み込み {session.count} 回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
