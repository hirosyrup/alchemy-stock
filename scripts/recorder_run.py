"""記録係を手元で1日動かす (docs/RECORDER_DESIGN.md §3)。注文は出さない。取引日の 8:50 ごろに起動し、15:30 ごろに終わる。

    uv run python scripts/recorder_prepare.py         # 先に材料を作る (前の晩か朝)
    SAXO_GCLOUD_ACCOUNT=<個人の Google アカウント> uv run python scripts/recorder_run.py

    uv run python scripts/recorder_run.py --marks 15:00,15:05 --no-morning   # 窓だけ試す

1. 朝 (54・S16): 寄る前に候補を作り、寄った直後の Saxo の値段を記録する
2. 49: 5分刻みの時刻 (09:00〜11:25、12:30〜15:20) の窓で TDnet を見張り、業績予想の変更を記録する
3. 15:24: 引けで手放す建玉の値段を記録する
記録は data/recorder/<日付>/*.jsonl、建玉は data/recorder/positions.json。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alchemy_stock.recorder import morning, rules  # noqa: E402
from alchemy_stock.recorder.positions import Positions  # noqa: E402
from alchemy_stock.recorder.saxo_read import SaxoReader  # noqa: E402
from alchemy_stock.recorder import store  # noqa: E402
from alchemy_stock.recorder.store import JST, DayLog, now_jst  # noqa: E402
from alchemy_stock.recorder.tdnet_live import ListWatcher, marks_49, watch_window  # noqa: E402
from alchemy_stock.recorder.window49 import Window49, load_book  # noqa: E402


def sleep_until(t: datetime) -> None:
    if (w := (t - now_jst()).total_seconds()) > 0:
        time.sleep(w)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=date.fromisoformat, default=now_jst().date())
    p.add_argument("--marks", help="49 の窓の時刻を指定する (例 15:00,15:05)。省略で全部")
    p.add_argument("--no-morning", action="store_true")
    p.add_argument("--no-close", action="store_true")
    p.add_argument("--no-saxo", action="store_true")
    a = p.parse_args()

    prep = store.read_json(f"prep/{a.day.isoformat()}.json")
    if prep is None:
        print(f"prep/{a.day}.json が無い。先に scripts/recorder_prepare.py --day {a.day} を動かす")
        return 1
    cal = [date.fromisoformat(d) for d in prep["calendar"]]
    if a.day not in cal:
        print(f"{a.day} は取引日ではない")
        return 1
    log, pos = DayLog(a.day), Positions()
    saxo = None if a.no_saxo else SaxoReader()
    log.write("run", {"event": "start", "prep_made_at": prep["made_at"], "universe": len(prep["universe"])})

    if not a.no_morning and now_jst().strftime("%H:%M") < "11:30":
        sleep_until(datetime(a.day.year, a.day.month, a.day.day, 8, 57, tzinfo=JST))
        morning.run(prep, log, saxo, pos)

    marks = marks_49(a.day)
    if a.marks:
        want = set(a.marks.split(","))
        marks = [m for m in marks if m.strftime("%H:%M") in want]
    marks = [m for m in marks if m > now_jst()]
    w = ListWatcher(a.day)
    win = Window49(log, prep["universe"], load_book(prep["book"], log), saxo, pos,
                   exit_day=rules.next_trading_day(cal, a.day).isoformat())
    if marks:
        sleep_until(marks[0] - timedelta(seconds=20))
        n = w.baseline()
        print(f"{now_jst():%H:%M:%S} 今日の一覧 {n}件を見た。窓 {len(marks)}回")
        for m in marks:
            res = watch_window(w, m, win.on_new, win.on_error)
            log.write("window", res)
            print(f"{now_jst():%H:%M:%S} 窓 {res['mark']}: 新しい開示 {res['new']}件 (読んだ {res['polls']}回、失敗 {res['errors']}回)")
    win.close()

    if not a.no_close:
        sleep_until(datetime(a.day.year, a.day.month, a.day.day, 15, 24, tzinfo=JST))
        morning.close_snapshot(prep, log, saxo, pos)
    log.write("run", {"event": "end", "open_positions": len(pos.open_rows())})
    print(f"{now_jst():%H:%M:%S} 終わり。持っている記録上の建玉 {len(pos.open_rows())}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
