"""取引時間中に TDnet の一覧を見張る (docs/RECORDER_DESIGN.md §3 の 5分刻みの窓)。

一覧は新しい順に 100件ずつのページ (alchemy_stock.tdnet)。1ページ目を読み、全部が新しければ次のページも読む
(15:30 の 242件のような集中時も取りこぼさない)。新しい行には、一覧で見えた時刻を付けて返す。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable

import requests

from ..http import USER_AGENT
from ..tdnet import list_url, parse_list_page
from .store import JST, now_jst


def row_key(r: dict) -> str:
    return r["doc_id"] or f"{r['time']}|{r['code']}|{r['title']}"


class ListWatcher:
    def __init__(self, day: date, timeout: float = 5.0):
        self.day = day
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.seen: set[str] = set()

    def fetch_page(self, page: int) -> tuple[list[dict], float]:
        r = self.session.get(list_url(self.day, page), timeout=self.timeout)
        t = time.time()
        if r.status_code == 404:
            return [], t
        r.raise_for_status()
        r.encoding = "utf-8"
        return parse_list_page(r.text, self.day)[0], t

    def baseline(self) -> int:
        """今ある行を全部「見た」ことにする (窓の前に出ていたものは新しい開示として扱わない)。"""
        for page in range(1, 30):
            rows, _ = self.fetch_page(page)
            self.seen |= {row_key(r) for r in rows}
            if len(rows) < 100:
                break
        return len(self.seen)

    def poll(self) -> list[tuple[dict, datetime]]:
        new: list[tuple[dict, datetime]] = []
        for page in range(1, 30):
            rows, t = self.fetch_page(page)
            fresh = [r for r in rows if row_key(r) not in self.seen]
            new += [(r, datetime.fromtimestamp(t, JST)) for r in fresh]
            if not rows or len(fresh) < len(rows):
                break
        for r, _ in new:
            self.seen.add(row_key(r))
        return new


def watch_window(w: ListWatcher, mark: datetime, on_new: Callable[[dict, datetime], None], on_error: Callable[[str], None],
                 after_found: float = 10.0, give_up: float = 60.0, interval: float = 1.0, lead: float = 3.0) -> dict:
    """mark (5分刻みの時刻) の開示を見張る。

    mark の lead 秒前から interval 秒ごとに読む。mark の時刻の開示が出たら after_found 秒後に、
    出なければ mark から give_up 秒後に終わる。終わり方と読んだ回数を返す。
    """
    hhmm = mark.strftime("%H:%M")
    wait = (mark - timedelta(seconds=lead) - now_jst()).total_seconds()
    if wait > 0:
        time.sleep(wait)
    found_at, polls, errors, n_new = None, 0, 0, 0
    while True:
        now = now_jst()
        if found_at and (now - found_at).total_seconds() >= after_found:
            break
        if not found_at and now >= mark + timedelta(seconds=give_up):
            break
        t0 = time.monotonic()
        try:
            new = w.poll()
            polls += 1
        except requests.RequestException as e:
            errors += 1
            on_error(f"{type(e).__name__}")
            new = []
        for r, seen_at in new:
            n_new += 1
            on_new(r, seen_at)
            if r["time"] == hhmm and not found_at:
                found_at = now_jst()
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))
    return {"mark": hhmm, "found": found_at is not None, "polls": polls, "errors": errors, "new": n_new,
            "ended": now_jst().isoformat(timespec="milliseconds")}


def marks_49(day: date) -> list[datetime]:
    """49 の窓の時刻: 09:00〜11:25 と 12:30〜15:20 の 5分刻み (65回)。"""
    out = []
    for h0, m0, h1, m1 in ((9, 0, 11, 25), (12, 30, 15, 20)):
        t = datetime(day.year, day.month, day.day, h0, m0, tzinfo=JST)
        end = t.replace(hour=h1, minute=m1)
        while t <= end:
            out.append(t)
            t += timedelta(minutes=5)
    return out
