"""記録係の夜の答え合わせ (docs/RECORDER_DESIGN.md §3)。その日の J-Quants の日足が出た後に動かす。

    uv run python scripts/recorder_reconcile.py               # 今日 (取引日なら)
    uv run python scripts/recorder_reconcile.py --day 2026-11-09

記録 (data/recorder/<日付>/reconcile.jsonl) と表示:
- 54: 検証と同じ条件 (前の取引日〜その日の開示を日付だけで見る、J-Quants の始値、分割は調整の比率で直す) で入っていた会社と、
  記録係の候補 (寄る前の開示) と記録 (Saxo の寄った直後の値段) の違い。寄った後の開示だけで当たっていた会社も数える
- 49: その日の取引時間中の、時価総額の上位 1,344 の決算短信・業績予想の開示のうち、窓で見えなかったもの (5分刻みでない時刻など)。
  J-Quants の財務情報が出ていれば (ふつう翌日)、検証と同じ方法の出来事と、記録係の判定を突き合わせる
- 手放した建玉: J-Quants の終値と、記録した Saxo の値段
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from alchemy_stock.jquants import get_all, load_api_key, make_session  # noqa: E402
from alchemy_stock.recorder import morning, rules  # noqa: E402
from alchemy_stock.recorder.positions import Positions  # noqa: E402
from alchemy_stock.recorder.prepare import ForecastBook, fins_rows  # noqa: E402
from alchemy_stock.recorder.store import DayLog, now_jst  # noqa: E402
from alchemy_stock.recorder.window49 import WANT  # noqa: E402
from recorder_prepare import load_fins  # noqa: E402


def check_54(prep: dict, log: DayLog, bars: pd.DataFrame, disc: pd.DataFrame) -> None:
    day, prev_day = date.fromisoformat(prep["day"]), date.fromisoformat(prep["prev_day"])
    b = bars.set_index("Code")
    uni = {c: u for c, u in prep["universe"].items() if u["close_raw"] >= rules.MIN_CLOSE_54}
    any_time = rules.disclosed_54(disc, prev_day, day)
    before = rules.disclosed_54(disc, prev_day, day, before="09:00")
    live = {r["code"]: r for r in log.read("morning54")}
    n = {"sim": 0, "sim_only_after_open": 0, "live_signal": 0, "both": 0}
    for code in sorted(set(uni) & any_time):
        if code not in b.index or pd.isna(b.at[code, "O"]):
            continue
        af = float(b.at[code, "AdjFactor"] or 1)
        gap = rules.gap_54(float(b.at[code, "O"]), uni[code]["close_raw"], af)
        lv = live.get(code, {})
        rec = {"logic": "54", "code": code, "jq_open": float(b.at[code, "O"]), "jq_close": float(b.at[code, "C"]), "adj_factor": af,
               "gap": gap, "sim_signal": rules.is_signal_54(gap), "pre_open_disclosure": code in before,
               "live_quote": lv.get("quote"), "live_signal": bool(lv.get("signal")), "uic": uni[code]["uic"]}
        if rec["sim_signal"] or rec["live_signal"]:
            log.write("reconcile", rec)
            n["sim"] += rec["sim_signal"]
            n["sim_only_after_open"] += rec["sim_signal"] and not rec["pre_open_disclosure"]
            n["live_signal"] += rec["live_signal"]
            n["both"] += rec["sim_signal"] and rec["live_signal"]
    print(f"54: 検証と同じ条件 {n['sim']}件 (うち寄った後の開示だけ {n['sim_only_after_open']}件)、記録係 {n['live_signal']}件、両方 {n['both']}件")


def check_49_seen(prep: dict, log: DayLog, disc_day: pd.DataFrame) -> None:
    """その日の、上位 1,344 の取引時間中の決算短信・業績予想の開示のうち、窓で見えなかったもの。"""
    seen = {r["doc_id"] for r in log.read("tdnet")}
    t = disc_day[disc_day.code.isin(prep["universe"]) & disc_day.title.str.contains(WANT) & disc_day.time.map(rules.in_session_49)]
    miss = t[~t.doc_id.isin(seen)]
    for r in miss.itertuples():
        log.write("reconcile", {"logic": "49", "kind": "見逃し", "time": r.time, "code": r.code, "title": r.title, "doc_id": r.doc_id,
                                "five_min": r.time[-1] in "05"})
    print(f"49: 対象の開示 {len(t)}件、窓で見えなかった {len(miss)}件 (うち 5分刻みの時刻 {int(miss.time.str[-1].isin(['0', '5']).sum())}件)")


def check_49_events(prep: dict, log: DayLog, jq) -> None:
    """J-Quants の財務情報で、検証と同じ方法の出来事を作り、記録係の判定と突き合わせる (財務情報が出ていれば)。"""
    day = date.fromisoformat(prep["day"])
    fins = load_fins(jq, day)
    rows = fins_rows(fins)
    if not (rows.d == pd.Timestamp(day)).any():
        print(f"49: {day} の財務情報がまだ J-Quants に出ていない (翌日以降にもう一度動かす)")
        return
    book, sim = ForecastBook(), []
    for r in rows[rows.d <= pd.Timestamp(day)].itertuples():
        new = {k: (None if v != v else v) for k, v in {"op": r.op, "odp": r.odp, "np": r.np}.items()}
        if r.is_new and r.d == pd.Timestamp(day) and rules.in_session_49(r.t[:5]) and r.code in prep["universe"]:
            ch = rules.forecast_change(new, book.prev(r.code, r.fy))
            if ch:
                sim.append({"code": r.code, "time": r.t[:5], "item": ch[0], "rate": ch[1]})
        book.update(r.code, r.fy, new)
    live = {(s["code"], s["time"]): s for s in log.read("signal49")}
    simk = {(s["code"], s["time"]): s for s in sim}
    for k in sorted(set(live) | set(simk)):
        s, lv = simk.get(k), live.get(k)
        log.write("reconcile", {"logic": "49", "kind": "判定", "code": k[0], "time": k[1], "sim": s,
                                "live": {x: lv.get(x) for x in ("item", "rate", "side", "seen", "parsed", "decided", "quoted")} if lv else None,
                                "same_side": bool(s and lv and (s["rate"] > 0) == (lv["rate"] > 0))})
    both = set(live) & set(simk)
    print(f"49: 検証と同じ方法 {len(simk)}件、記録係 {len(live)}件、両方 {len(both)}件 "
          f"(向きが同じ {sum((simk[k]['rate'] > 0) == (live[k]['rate'] > 0) for k in both)}件)")


def check_exits(prep: dict, log: DayLog, bars: pd.DataFrame, pos: Positions) -> None:
    b = bars.set_index("Code")
    n = 0
    for p in pos.rows:
        ex = p.get("exit") or {}
        if ex.get("day") != prep["day"] or "jq_close" in ex or p["code"] not in b.index:
            continue
        ex["jq_close"] = float(b.at[p["code"], "C"])
        ex["jq_open"] = float(b.at[p["code"], "O"])
        log.write("reconcile", {"logic": p["logic"], "kind": "手放し", "id": p["id"], "saxo_price": ex.get("price"),
                                "jq_close": ex["jq_close"], "jq_open": ex["jq_open"]})
        n += 1
    pos.save()
    print(f"手放した建玉 {n}件に J-Quants の値段を足した")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--day", type=date.fromisoformat, default=now_jst().date())
    a = p.parse_args()
    path = ROOT / "data/recorder/prep" / f"{a.day.isoformat()}.json"
    if not path.exists():
        print(f"{path} が無い")
        return 1
    prep = json.loads(path.read_text())
    jq = make_session(load_api_key(ROOT / ".env"), min_interval=1.0)
    bars = pd.DataFrame(get_all(jq, "/equities/bars/daily", date=a.day.isoformat()))
    if bars.empty:
        print(f"{a.day} の日足がまだ J-Quants に出ていない")
        return 1
    for col in ("O", "C", "AdjFactor"):
        bars[col] = pd.to_numeric(bars[col], errors="coerce")
    log, pos = DayLog(a.day, ROOT / "data/recorder"), Positions(ROOT / "data/recorder/positions.json")
    prev_day = date.fromisoformat(prep["prev_day"])
    disc = morning.tdnet_rows([d for d in pd.date_range(prev_day, a.day).date])
    check_54(prep, log, bars, disc)
    check_49_seen(prep, log, disc[disc.date == a.day])
    check_49_events(prep, log, jq)
    check_exits(prep, log, bars, pos)
    # 前の取引日の 49 は、その日の財務情報が出た今なら突き合わせられる (まだなら)
    pp = ROOT / "data/recorder/prep" / f"{prev_day.isoformat()}.json"
    plog = DayLog(prev_day, ROOT / "data/recorder")
    if pp.exists() and plog.read("run") and not any(r.get("kind") == "判定" for r in plog.read("reconcile")):
        print(f"(前の取引日 {prev_day})")
        check_49_events(json.loads(pp.read_text()), plog, jq)
    return 0


if __name__ == "__main__":
    sys.exit(main())
