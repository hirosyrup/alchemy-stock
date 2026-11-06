"""記録係の Cloud Run の job の入口 (docs/RECORDER_DESIGN.md §5)。Cloud Scheduler から、何をするかを引数で指定して起動する。

    python scripts/recorder_job.py prepare     # 07:30 その日 (取引日でなければ次の取引日) の材料を作る
    python scripts/recorder_job.py morning     # 08:56 54・S16 の候補と寄りの値段
    python scripts/recorder_job.py window      # 5分刻みの時刻の1分前 49 の窓 (次の 5分刻みの時刻を見張る。窓の時刻でなければすぐ終わる)
    python scripts/recorder_job.py close       # 15:23 引けで手放す建玉の値段
    python scripts/recorder_job.py reconcile   # 18:00 答え合わせ

    python scripts/recorder_job.py window --mark 15:00   # 時刻を指定して試す
    RECORDER_GCS=gs://alchemy-stock-513407-raw/recorder uv run python scripts/recorder_job.py upload-fins   # 手元から1回だけ

取引日でない日は、材料 (prep) が無いので何もせずに終わる。記録の置き場は環境変数 RECORDER_GCS で決まる (store.py)。
"""
from __future__ import annotations

import argparse
import gzip
import io
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from alchemy_stock.recorder import morning, rules, store  # noqa: E402
from alchemy_stock.recorder.positions import Positions  # noqa: E402
from alchemy_stock.recorder.saxo_read import SaxoReader  # noqa: E402
from alchemy_stock.recorder.store import JST, DayLog, now_jst  # noqa: E402
from alchemy_stock.recorder.tdnet_live import ListWatcher, marks_49, watch_window  # noqa: E402
from alchemy_stock.recorder.window49 import Window49, load_book  # noqa: E402


def today_prep(day: date) -> dict | None:
    prep = store.read_json(f"prep/{day.isoformat()}.json")
    if prep is None:
        print(f"{day} の材料が無い (取引日でないか、準備が失敗した)")
    return prep


def at(day: date, hhmm: str) -> datetime:
    return datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[3:]), tzinfo=JST)


def run_window(day: date, mark_s: str | None) -> int:
    now = now_jst()
    if mark_s:
        mark = at(day, mark_s)
    else:  # 次の 5分刻みの時刻 (起動が遅れても、その時刻を過ぎて 20秒までは同じ時刻を見張る)
        base = (now - timedelta(seconds=20)).replace(second=0, microsecond=0)
        mark = base + timedelta(minutes=5 - base.minute % 5)
    if mark not in marks_49(day) and not mark_s:
        print(f"{mark:%H:%M} は 49 の窓の時刻ではない")
        return 0
    prep = today_prep(day)
    if prep is None:
        return 0
    cal = [date.fromisoformat(d) for d in prep["calendar"]]
    log, pos = DayLog(day), Positions()
    win = Window49(log, prep["universe"], load_book(prep["book"], log), SaxoReader(), pos,
                   exit_day=rules.next_trading_day(cal, day).isoformat())
    w = ListWatcher(day)
    n = w.baseline()
    started = now_jst()
    res = watch_window(w, mark, win.on_new, win.on_error)
    win.close()
    log.write("window", {**res, "baseline": n, "started": started.isoformat(timespec="milliseconds"),
                         "lead_s": round((mark - started).total_seconds(), 1)})
    print(f"窓 {res['mark']}: 起動は {(mark - started).total_seconds():.0f}秒前、新しい開示 {res['new']}件 (読んだ {res['polls']}回、失敗 {res['errors']}回)")
    return 0


def upload_fins() -> int:
    import duckdb

    from recorder_prepare import FINS_BASE, FINS_COLS

    df = duckdb.sql(f"""select {', '.join(FINS_COLS)} from read_csv('{ROOT}/data/jquants/fins/summary/**/*.csv.gz',
        union_by_name=true, all_varchar=true)""").df()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        df.to_csv(io.TextIOWrapper(f, encoding="utf-8"), index=False)
    store.write_bytes(FINS_BASE, buf.getvalue())
    print(f"{FINS_BASE}: {len(df)}行、{len(buf.getvalue()) // 1024}KB (〜{df.DiscDate.max()})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("task", choices=["prepare", "morning", "window", "close", "reconcile", "upload-fins"])
    p.add_argument("--mark")
    p.add_argument("--day", type=date.fromisoformat)
    a = p.parse_args()
    day = a.day or now_jst().date()

    if a.task == "upload-fins":
        return upload_fins()
    if a.task == "prepare":
        import recorder_prepare

        return recorder_prepare.main(["--day", a.day.isoformat()] if a.day else [])
    if a.task == "window":
        return run_window(day, a.mark)
    prep = today_prep(day)
    if prep is None:
        return 0
    if a.task == "morning":
        log = DayLog(day)
        log.write("run", {"event": "morning", "prep_made_at": prep["made_at"]})
        morning.run(prep, log, SaxoReader(), Positions())
    elif a.task == "close":
        if (w := (at(day, "15:24") - now_jst()).total_seconds()) > 0:
            time.sleep(w)
        morning.close_snapshot(prep, DayLog(day), SaxoReader(), Positions())
    elif a.task == "reconcile":
        import recorder_reconcile

        return recorder_reconcile.main(["--day", day.isoformat()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
