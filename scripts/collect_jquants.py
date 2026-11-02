"""J-Quants から、日ごとの全銘柄の日足 (bars) と銘柄の一覧 (master) を、日付ごとのファイルに貯める。

平日を1日ずつ取る (休みの日は空のファイルになる)。既に取った日は飛ばすので、止まっても同じコマンドで続きから再開できる。
間隔の目安: スタンダード 1秒 (上限 120回/分)、無料 13秒 (上限 5回/分)。

    uv run python scripts/collect_jquants.py bars                       # 10年前から今日まで
    uv run python scripts/collect_jquants.py master --start 2025-06-02 --end 2025-06-06 --interval 13
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.http import Blocked  # noqa: E402
from alchemy_stock.jquants import daily_bars, load_api_key, make_session, master  # noqa: E402
from alchemy_stock.store import now_utc, write_jsonl  # noqa: E402

KINDS = {"bars": daily_bars, "master": master}


def main() -> int:
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("kind", choices=list(KINDS))
    p.add_argument("--start", type=date.fromisoformat, default=today - timedelta(days=3653))
    p.add_argument("--end", type=date.fromisoformat, default=today)
    p.add_argument("--out", type=Path, default=Path("data/jquants"))
    p.add_argument("--interval", type=float, default=1.0)
    args = p.parse_args()

    fetch = KINDS[args.kind]
    session = make_session(load_api_key(), args.interval)
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    n = 0
    for day in days:
        if day.weekday() >= 5:
            continue
        path = args.out / args.kind / f"{day:%Y}" / f"{day.isoformat()}.jsonl.gz"
        if path.exists():
            continue
        try:
            rows = fetch(session, day.isoformat())
        except Blocked as e:
            print(f"回数の上限か拒否なので止めます: {e}", file=sys.stderr)
            return 2
        except requests.HTTPError as e:
            # プランの範囲外の日付などはここに来る。中身を見せて止める
            print(f"{day} で止めます: {e} {e.response.text[:300]}", file=sys.stderr)
            return 1
        fetched_at = now_utc()
        write_jsonl(path, [{**r, "fetched_at": fetched_at} for r in rows])
        n += 1
        if n % 50 == 0 or len(days) < 50:
            print(f"{day} {len(rows):,}件 (ここまで {n}日・読み込み {session.count}回)")
    print(f"{args.kind}: {n}日分を取得、読み込み {session.count}回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
