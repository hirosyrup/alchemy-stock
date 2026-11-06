"""記録係の売買ルール (docs/RECORDER_DESIGN.md §1)。

検証で使ったルール (54 は scripts/run_stage4.py の gap_trades(54)、S16 は scripts/run_stage3.py の t_s16) を、
その時点で手に入る情報だけで判定する形に書き直したもの。値段や開示の取り方 (J-Quants・TDnet・Saxo) は持たず、
渡された表だけを見る。過去の日付で検証のコードと同じ取引になるかは scripts/check_recorder_rules.py で確かめる。

検証のコードと本番で変わるところ (本番では後から分かることを使えないため):
- 54 の「前日の終値 200円以上」: 検証は後の分割で調整した終値で見ていた。本番はその時点の (調整前の) 終値で見る
- 54 の開示: 検証は日付だけで見ていて、その日の寄った後の開示も数えていた。本番は寄りの前に出たものだけ
- 54 の下げ: 今日が分割の日なら、前日の終値を分割の比率で直してから比べる (検証の調整済みの値段と同じ)
- S16: 検証は「発表から 30暦日以内に実施か中止の開示が出たもの」だけを使っていた。本番は入る時点で分からないので
  全部入り、30暦日を過ぎても出なければ、その次の取引日の始値で買い戻す
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from .prepare import cap_ranks

N_TOP = 1344         # 時価総額の上位 (前の取引日の時点)
GAP_54 = -0.10        # 寄りの下げ
MIN_CLOSE_54 = 200.0  # 前の取引日の終値
HOLD_54 = 5           # 入った日を1日目として、5日目の引けで買い戻す
S16_WINDOW_DAYS = 30  # 発表から実施・中止の開示を待つ暦日


# ---------- 共通 ----------
def nth_trading_day(calendar: list[date], start: date, n: int) -> date | None:
    """start を1日目として n 日目の取引日。暦が足りなければ None。"""
    i = calendar.index(start) + n - 1
    return calendar[i] if i < len(calendar) else None


def next_trading_day(calendar: list[date], day: date) -> date | None:
    """day より後の最初の取引日。"""
    for d in calendar:
        if d > day:
            return d
    return None


class Book:
    """会社ごとの、持っている取引の手放す日。持っている間 (手放す日を含む) の新しい出来事は使わない (検証と同じ)。"""

    def __init__(self) -> None:
        self.exit_day: dict[str, date] = {}

    def blocked(self, code: str, day: date) -> bool:
        return code in self.exit_day and day <= self.exit_day[code]

    def hold(self, code: str, exit_day: date) -> None:
        self.exit_day[code] = exit_day


# ---------- 54: 悪い知らせで −10% 以上下げて寄った大きめの会社を寄りで売り、5日目の引けで買い戻す ----------
def universe_54(prev: pd.DataFrame, n_top: int = N_TOP, close_col: str = "close_raw") -> pd.DataFrame:
    """前の取引日の引けの後に作る、54 の対象。

    prev は1行1社: code、close_raw (前の取引日の終値、調整前)、shares (発行済み − 自己株、その日までの最新の決算短信)、
    common (普通株か)。時価総額 = close_raw × shares の順位が上位 n_top で、close_col の終値が 200円以上の会社を返す。
    close_col は本番では close_raw。検証と同じかを確かめるときだけ、後の分割で調整した終値の列を渡す。
    """
    p = cap_ranks(prev)
    return p[(p.cap_rank <= n_top) & (p[close_col] >= MIN_CLOSE_54)][["code", "close_raw", "cap_rank"]].reset_index(drop=True)


def disclosed_54(disc: pd.DataFrame, prev_day: date, today: date, before: str | None = None) -> set[str]:
    """前の取引日の日付〜今日に開示のあった会社 (土日・祝日の開示も入る)。

    disc は code、date (date)、time ("HH:MM"、分からなければ空)。before ("09:00" など) を渡すと、
    今日の分はその時刻より前のものだけを数える (本番は寄りの前)。
    """
    d = disc[(disc.date >= prev_day) & (disc.date <= today)]
    if before is not None:
        d = d[(d.date < today) | (d.time.fillna("") < before)]
    return set(d.code)


def gap_54(open_raw: float, prev_close_raw: float, adj_factor: float = 1.0) -> float:
    """寄りの値段の、前日の終値からの変化。今日が分割の日なら、adj_factor (1株を2株にするなら 0.5) で前日の終値を直す。"""
    return open_raw / (prev_close_raw * adj_factor) - 1


def is_signal_54(gap: float) -> bool:
    return gap <= GAP_54


# ---------- 49: 取引時間中の業績予想の変更に数秒で入り、次の取引日の終値で手放す ----------
SESSIONS_49 = (("09:00", "11:30"), ("12:30", "15:25"))  # 開示の時刻 (分まで) がこの間 (終わりは含まない)
ENTRY_DELAY_49 = 5  # 開示の時刻 (HH:MM:00) の 5秒後以降の最初の約定で入る


def in_session_49(hhmm: str) -> bool:
    return any(a <= hhmm < b for a, b in SESSIONS_49)


def forecast_change(new: dict, prev: dict) -> tuple[str, float] | None:
    """今期の予想の変化 (検証の stage1_numbers.py の fc と stage4_vault49_events.py の rate と同じ)。

    営業利益 → 経常利益 → 純利益の順に、新しい予想と前の予想が両方あり、前が 0 でなく、値が変わった最初の項目で
    (前との差) ÷ |前| を返す。どれも当たらなければ None。上がれば買い、下がれば売り。
    new は {op, odp, np}、prev は {op, odp, np} (前の晩までの J-Quants の予想の流れの最後の値)。
    """
    for k in ("op", "odp", "np"):
        v, p = new.get(k), prev.get(k)
        if v is not None and p is not None and p != 0 and v != p:
            return k, (v - p) / abs(p)
    return None


# ---------- S16: 立会外分売の発表の次の始値で売り、分売の日の始値で買い戻す ----------
def s16_kind(title: str) -> str | None:
    """開示の題名から、S16 の「発表」「実施」「中止」を見分ける (検証の t_s16 と同じ条件)。

    実施と中止は「訂正」を除かない (検証のまま)。1つの題名が実施と中止の両方に当たるときは実施を優先する。
    """
    if "立会外分売" not in title:
        return None
    if re.search("立会外分売.*実施", title):
        return "実施"
    if "中止" in title:
        return "中止"
    if re.search("実施|終了|中止|訂正", title):
        return None
    return "発表"


def s16_end(ann_day: date, ends: list[tuple[date, str]]) -> tuple[date, str] | None:
    """発表の日と、その会社の実施・中止の開示 (日付、種類) から、終わりの開示を選ぶ。

    発表の日から 30暦日以内 (発表の日を含む) で、実施があれば最初の実施、無ければ最初の中止。
    どちらも無ければ None (本番では 30暦日を過ぎた次の取引日の始値で買い戻す)。
    """
    lo, hi = ann_day, ann_day + timedelta(days=S16_WINDOW_DAYS)
    inwin = sorted((d, k) for d, k in ends if lo <= d <= hi)
    exe = [x for x in inwin if x[1] == "実施"]
    if exe:
        return exe[0]
    return inwin[0] if inwin else None


def s16_timeout_day(ann_day: date) -> date:
    """実施・中止が出なかったときに、買い戻しを決める日 (この日より後の最初の取引日の始値で買い戻す)。"""
    return ann_day + timedelta(days=S16_WINDOW_DAYS)
