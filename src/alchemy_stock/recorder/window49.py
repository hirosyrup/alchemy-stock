"""49 の窓の中の処理: 新しい開示を見て、業績予想の変更なら XBRL を読み、判定と値段と秒数を記録する。注文はしない。

記録の種類 (store.DayLog):
- tdnet: 窓で見えた開示 (全部)。開示の時刻 (HH:MM:00) から一覧で見えるまでの秒
- forecast: 読んだ今期の予想 (同じ日の後の窓で「前の予想」として使う)
- signal49: 判定 (向き・変化の項目と率・前の予想・今の予想) と、XBRL を取り終えた・読み終えた・判定した・値段を取った秒、Saxo の値段
- skip49: 対象の会社の業績予想らしい開示を、使わなかった理由 (予想なし・年度が無い・変化なし など)
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

from ..http import USER_AGENT
from . import rules
from .forecast import read_zip
from .positions import Positions
from .prepare import ForecastBook
from .saxo_read import SaxoReader
from .store import JST, DayLog, now_jst

WANT = re.compile(r"業績予想|決算短信|予想の修正")


def secs(t: datetime, hhmm: str) -> float:
    base = t.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]), second=0, microsecond=0)
    return round((t - base).total_seconds(), 3)


class Window49:
    def __init__(self, log: DayLog, universe: dict[str, dict], book: ForecastBook, saxo: SaxoReader | None,
                 pos: Positions, exit_day: str, workers: int = 4):
        self.log, self.universe, self.book, self.saxo = log, universe, book, saxo
        self.pos, self.exit_day = pos, exit_day  # 49 は次の取引日の引けで手放す
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT

    def on_new(self, r: dict, seen_at: datetime) -> None:
        rec = {"time": r["time"], "code": r["code"], "title": r["title"], "doc_id": r["doc_id"],
               "seen": secs(seen_at, r["time"]), "has_xbrl": bool(r["xbrl_url"])}
        self.log.write("tdnet", rec)
        u = self.universe.get(r["code"])
        if u and r["xbrl_url"] and WANT.search(r["title"]) and rules.in_session_49(r["time"]):
            self.pool.submit(self._handle, r, seen_at, u)

    def on_error(self, msg: str) -> None:
        self.log.write("errors", {"where": "tdnet_list", "error": msg})

    def _handle(self, r: dict, seen_at: datetime, u: dict) -> None:
        hhmm, base = r["time"], {"time": r["time"], "code": r["code"], "title": r["title"], "doc_id": r["doc_id"],
                                 "seen": secs(seen_at, r["time"])}
        try:
            z = self.http.get(r["xbrl_url"], timeout=10)
            z.raise_for_status()
            t_zip = now_jst()
            fc = read_zip(z.content)
            t_parse = now_jst()
        except Exception as e:  # noqa: BLE001
            self.log.write("errors", {**base, "where": "xbrl", "error": f"{type(e).__name__}: {e}"[:300]})
            return
        timing = {"zip_done": secs(t_zip, hhmm), "parsed": secs(t_parse, hhmm), "kb": len(z.content) // 1024}
        if not fc or not fc.get("fy_end"):
            self.log.write("skip49", {**base, **timing, "reason": "予想なし", "file": (fc or {}).get("file")})
            return
        new = {k: fc[k] for k in ("op", "odp", "np")}
        with self.lock:
            prev = self.book.prev(r["code"], fc["fy_end"])
            ch = rules.forecast_change(new, prev)
            self.book.update(r["code"], fc["fy_end"], new)
            self.log.write("forecast", {**base, "fy": fc["fy_end"], **new, "file": fc["file"]})
        t_dec = now_jst()
        timing["decided"] = secs(t_dec, hhmm)
        info = {"fy": fc["fy_end"], "new": new, "prev": prev, "xbrl_prev": {k: fc.get(f"prev_{k}") for k in ("op", "odp", "np")},
                "cap_rank": u.get("cap_rank"), "uic": u.get("uic"), "file": fc["file"]}
        if not ch:
            self.log.write("skip49", {**base, **timing, **info, "reason": "変化なし・前の予想なし"})
            return
        item, rate = ch
        entry_at = t_dec.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]), second=0, microsecond=0) + timedelta(seconds=rules.ENTRY_DELAY_49)
        wait = (entry_at - now_jst()).total_seconds()
        if wait > 0:
            time.sleep(wait)
        quote, qerr = None, None
        if self.saxo and u.get("uic"):
            try:
                quote = self.saxo.quotes([u["uic"]]).get(u["uic"])
            except Exception as e:  # noqa: BLE001
                qerr = f"{type(e).__name__}: {e}"[:300]
        t_q = now_jst()
        side = "buy" if rate > 0 else "sell"
        with self.lock:
            day = t_q.date().isoformat()
            blocked = self.pos.blocked("49", r["code"], day)
            self.log.write("signal49", {**base, **timing, **info, "item": item, "rate": rate, "side": side,
                                        "quoted": secs(t_q, hhmm), "quote": quote, "quote_error": qerr,
                                        "in_saxo": u.get("uic") is not None, "blocked": blocked})
            if not blocked:
                price = (quote or {}).get("ask" if side == "buy" else "bid")
                self.pos.add({"logic": "49", "code": r["code"], "uic": u.get("uic"), "side": side, "entry_day": day,
                              "entry": {"at": t_q.isoformat(timespec="milliseconds"), "price": price, "disc_time": hhmm, "rate": rate},
                              "exit_rule": {"kind": "close", "day": self.exit_day}})

    def close(self) -> None:
        self.pool.shutdown(wait=True)


def load_book(prep_book: dict, log: DayLog) -> ForecastBook:
    """前の晩の帳簿に、その日にそれまでの窓で読んだ予想を足す (検証の「前の予想」には同じ日の前の開示も入る)。"""
    b = ForecastBook()
    for code, fys in prep_book.items():
        for fy, v in fys.items():
            b.update(code, fy, v)
    for rec in log.read("forecast"):
        b.update(rec["code"], rec["fy"], rec)
    return b


__all__ = ["Window49", "load_book", "JST"]
