from datetime import date

import pandas as pd

from alchemy_stock.recorder import rules


def test_universe_54_ranks_by_cap_and_filters_close():
    prev = pd.DataFrame({
        "code": ["A", "B", "C", "D"],
        "close_raw": [1000.0, 150.0, 500.0, 300.0],
        "shares": [1e6, 1e8, 1e6, 1e9],
        "common": [True, True, True, False],
    })
    u = rules.universe_54(prev, n_top=2)
    # 時価総額は B 150億 > A 10億 > C 5億 (D は普通株でない)。上位2社のうち B は 200円未満
    assert list(u.code) == ["A"]


def test_disclosed_54_cutoff_only_applies_to_today():
    disc = pd.DataFrame({
        "code": ["A", "B", "C", "D"],
        "date": [date(2026, 11, 6), date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 9)],
        "time": ["15:30", "10:00", "08:30", "15:00"],
    })
    prev, today = date(2026, 11, 6), date(2026, 11, 9)
    assert rules.disclosed_54(disc, prev, today) == {"A", "B", "C", "D"}
    assert rules.disclosed_54(disc, prev, today, before="09:00") == {"A", "B", "C"}


def test_gap_54_adjusts_split_day():
    # 1株を2株にする日: 前日 1,000円 → 寄り 450円 は −10% (分割を直さないと −55% に見える)
    assert abs(rules.gap_54(450, 1000, adj_factor=0.5) - (-0.10)) < 1e-12
    assert rules.is_signal_54(rules.gap_54(440, 1000, adj_factor=0.5))
    assert not rules.is_signal_54(rules.gap_54(460, 1000, adj_factor=0.5))
    assert not rules.is_signal_54(rules.gap_54(950, 1000))


def test_book_blocks_through_exit_day():
    b = rules.Book()
    b.hold("A", date(2026, 11, 13))
    assert b.blocked("A", date(2026, 11, 13))
    assert not b.blocked("A", date(2026, 11, 16))
    assert not b.blocked("B", date(2026, 11, 10))


def test_s16_kind():
    assert rules.s16_kind("立会外分売に関するお知らせ") == "発表"
    assert rules.s16_kind("立会外分売の実施に関するお知らせ") == "実施"
    assert rules.s16_kind("立会外分売の中止に関するお知らせ") == "中止"
    assert rules.s16_kind("立会外分売の実施中止に関するお知らせ") == "実施"  # 検証のまま
    assert rules.s16_kind("（訂正）立会外分売に関するお知らせ") is None
    assert rules.s16_kind("自己株式の取得に関するお知らせ") is None


def test_s16_end_prefers_execution_within_window():
    d = date(2026, 11, 2)
    ends = [(date(2026, 11, 5), "中止"), (date(2026, 11, 10), "実施"), (date(2026, 12, 20), "実施")]
    assert rules.s16_end(d, ends) == (date(2026, 11, 10), "実施")
    assert rules.s16_end(d, [(date(2026, 11, 5), "中止")]) == (date(2026, 11, 5), "中止")
    assert rules.s16_end(d, [(date(2026, 12, 20), "実施")]) is None


def test_forecast_change_order_and_skip():
    # 営業利益が変わっていなければ経常利益で見る
    assert rules.forecast_change({"op": 100, "odp": 120, "np": 80}, {"op": 100, "odp": 100, "np": 80}) == ("odp", 0.2)
    # 前の予想が 0 や空の項目は飛ばす
    assert rules.forecast_change({"op": 50, "odp": 90}, {"op": 0, "odp": 100}) == ("odp", -0.1)
    assert rules.forecast_change({"op": None, "np": 30}, {"op": 10, "np": -60}) == ("np", 1.5)
    assert rules.forecast_change({"op": 100}, {"op": 100}) is None


def test_in_session_49():
    assert rules.in_session_49("09:00") and rules.in_session_49("15:20")
    assert not rules.in_session_49("11:30") and not rules.in_session_49("15:25") and not rules.in_session_49("08:59")


def test_trading_days():
    cal = [date(2026, 11, d) for d in (2, 4, 5, 6, 9, 10)]
    assert rules.nth_trading_day(cal, date(2026, 11, 4), 5) == date(2026, 11, 10)
    assert rules.nth_trading_day(cal, date(2026, 11, 5), 5) is None
    assert rules.next_trading_day(cal, date(2026, 11, 6)) == date(2026, 11, 9)
