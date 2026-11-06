"""朝の記録 (54・S16): 寄る前に候補を作り、寄った直後の Saxo の値段を記録する。注文はしない。

- 54: 前の取引日〜今朝 9:00 より前に開示があった、時価総額の上位 1,344・前日の終値 200円以上の会社。
  寄った (Saxo の値段に始値が出た) とき、始値 ÷ 前日の終値 − 1 ≤ −10% なら、その時点の売値 (Bid) で売ったことにする
  (分割の日は、夜の答え合わせで J-Quants の調整の比率を見て外す)。5日目の引けで買い戻す
- S16: 前の取引日〜昨日に「立会外分売」の発表があった会社 (全部の会社)。寄った直後の売値で売ったことにする。
  持っている S16 は、実施・中止の開示が出たら次の取引日の始値 (寄った直後の買値 Ask) で、出なければ発表から 30暦日の次の取引日に買い戻す

寄りの値段は4秒ごとにまとめて取る (Saxo の list の上限は1分に 30回、こちらは 15回)。全部寄ったか 11:30 で終わる。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pandas as pd

from ..http import PoliteSession
from ..tdnet import fetch_day
from . import rules
from .positions import Positions
from .saxo_read import SaxoReader
from .store import JST, DayLog, now_jst

POLL = 4.0


def tdnet_rows(days: list[date]) -> pd.DataFrame:
    s = PoliteSession(min_interval=1.0)
    rows = [r for d in days for r in fetch_day(s, d)]
    df = pd.DataFrame(rows, columns=["date", "time", "code", "title", "doc_id"])
    df["date"] = pd.to_datetime(df.date).dt.date
    return df


def candidates(prep: dict, disc: pd.DataFrame, pos: Positions) -> tuple[list[dict], list[dict], list[dict]]:
    day, prev_day = date.fromisoformat(prep["day"]), date.fromisoformat(prep["prev_day"])
    uni = {c: u for c, u in prep["universe"].items() if u["close_raw"] >= rules.MIN_CLOSE_54}
    hit = rules.disclosed_54(disc, prev_day, day, before="09:00")
    c54 = [{"logic": "54", "code": c, "uic": uni[c]["uic"], "prev_close": uni[c]["close_raw"], "cap_rank": uni[c]["cap_rank"],
            "blocked": pos.blocked("54", c, day.isoformat())} for c in sorted(set(uni) & hit)]
    kind = disc.title.map(rules.s16_kind)
    ann = disc[(kind == "発表") & (disc.date >= prev_day) & (disc.date < day)]
    s16 = [{"logic": "S16", "code": c, "uic": prep["saxo"].get(c), "ann_day": d.isoformat(),
            "blocked": pos.blocked("S16", c, day.isoformat())} for c, d in ann[["code", "date"]].drop_duplicates().itertuples(index=False)]
    ends = disc[kind.isin(["実施", "中止"])]
    exits = []
    for p in pos.open_rows("S16"):
        e = ends[(ends.code == p["code"]) & (ends.date >= date.fromisoformat(p["ann_day"])) & (ends.date < day)]
        until = date.fromisoformat(p["exit_rule"]["until"])
        if len(e) or until < day:
            exits.append({**p, "reason": e.title.iloc[0] if len(e) else "期限 (30暦日)"})
    return c54, s16, exits


def run(prep: dict, log: DayLog, saxo: SaxoReader | None, pos: Positions) -> None:
    day, prev_day = date.fromisoformat(prep["day"]), date.fromisoformat(prep["prev_day"])
    cal = [date.fromisoformat(d) for d in prep["calendar"]]
    start = min([prev_day] + [date.fromisoformat(p["ann_day"]) for p in pos.open_rows("S16")])  # 持っている S16 の実施・中止も探す
    disc = tdnet_rows([start + timedelta(days=i) for i in range((day - start).days + 1)])
    c54, s16, exits = candidates(prep, disc, pos)
    for c in c54 + s16:
        log.write("candidates", c)
    for x in exits:
        log.write("exit_due", {"id": x["id"], "code": x["code"], "reason": x["reason"]})
    want = {c["uic"] for c in c54 + s16 + exits if c.get("uic") and not c.get("blocked")}
    print(f"{now_jst():%H:%M:%S} 54 の候補 {len(c54)}社、S16 の入り {len(s16)}社・出 {len(exits)}社、Saxo で見る {len(want)}銘柄")
    if not saxo or not want:
        return
    open_at = datetime(day.year, day.month, day.day, 9, 0, tzinfo=JST)
    if (w := (open_at - now_jst()).total_seconds()) > 0:
        time.sleep(w)
    first: dict[int, dict] = {}
    stop = open_at.replace(hour=11, minute=30)
    while want - set(first) and now_jst() < stop:
        t0 = time.monotonic()
        try:
            q = saxo.quotes(sorted(want - set(first)))
        except Exception as e:  # noqa: BLE001
            log.write("errors", {"where": "morning_quotes", "error": f"{type(e).__name__}: {e}"[:300]})
            q = {}
        at = now_jst()
        if q and all(v["price_type"] == "NoAccess" for v in q.values()):
            log.write("errors", {"where": "morning_quotes", "error": "NoAccess (東証のデータが API に来ていない)"})
            return
        for uic, v in q.items():
            if v.get("open"):
                first[uic] = {**v, "at": at.isoformat(timespec="milliseconds")}
        time.sleep(max(0.0, POLL - (time.monotonic() - t0)))
    for c in c54:
        v = first.get(c["uic"])
        if not v or c["blocked"]:
            log.write("morning54", {**c, "opened": bool(v), "quote": v})
            continue
        gap = v["open"] / c["prev_close"] - 1
        log.write("morning54", {**c, "opened": True, "quote": v, "gap": gap, "signal": rules.is_signal_54(gap)})
        if rules.is_signal_54(gap):
            pos.add({"logic": "54", "code": c["code"], "uic": c["uic"], "side": "sell", "entry_day": day.isoformat(),
                     "entry": {"at": v["at"], "price": v["bid"], "open": v["open"], "gap": gap},
                     "exit_rule": {"kind": "close", "day": rules.nth_trading_day(cal, day, rules.HOLD_54).isoformat()}})
    for c in s16:
        v = first.get(c["uic"]) if c["uic"] else None
        log.write("morning_s16", {**c, "quote": v})
        if not c["blocked"]:
            pos.add({"logic": "S16", "code": c["code"], "uic": c["uic"], "side": "sell", "entry_day": day.isoformat(),
                     "ann_day": c["ann_day"], "entry": {"at": v["at"] if v else None, "price": v["bid"] if v else None},
                     "exit_rule": {"kind": "s16", "until": rules.s16_timeout_day(date.fromisoformat(c["ann_day"])).isoformat()}})
    for x in exits:
        v = first.get(x["uic"]) if x.get("uic") else None
        pos.close(x["id"], {"day": day.isoformat(), "at": v["at"] if v else None, "price": v["ask"] if v else None, "reason": x["reason"]})


def close_snapshot(prep: dict, log: DayLog, saxo: SaxoReader | None, pos: Positions) -> None:
    """引けで手放す日の建玉 (54 の5日目、49 の次の日) の、引け前 (15:24) の値段を記録して閉じる。終値は夜の答え合わせで足す。"""
    day = prep["day"]
    due = [p for p in pos.open_rows() if p["exit_rule"]["kind"] == "close" and p["exit_rule"]["day"] <= day]
    q = {}
    if saxo and due:
        try:
            q = saxo.quotes(sorted({p["uic"] for p in due if p.get("uic")}))
        except Exception as e:  # noqa: BLE001
            log.write("errors", {"where": "close_quotes", "error": f"{type(e).__name__}: {e}"[:300]})
    at = now_jst().isoformat(timespec="milliseconds")
    for p in due:
        v = q.get(p.get("uic"))
        price = (v or {}).get("ask" if p["side"] == "sell" else "bid")
        log.write("close_snapshot", {"id": p["id"], "quote": v})
        pos.close(p["id"], {"day": day, "at": at, "price": price, "reason": "引け前の値段 (終値は夜に足す)"})
