"""49 の「前の予想」と判定 (src/alchemy_stock/recorder/prepare.py の ForecastBook、rules.forecast_change) を
J-Quants の財務情報で過去に回し、検証の 49 の出来事の一覧 (scripts/stage4_vault49_events.py) と同じになるかを確かめる。

    uv run python scripts/check_recorder_49.py <stage4_vault49_events.py が書いた vault49_events.pkl>

本番と同じく、会社・会計年度ごとの予想を古い順に足していき、取引時間中の決算短信 (通期以外)・修正が出たら、
その時点の帳簿と比べる。対象は前の取引日の時点で時価総額の上位 1,344 (run_stage4.py の金庫の設定の順位)。
見るのは出来事の一覧 (会社・日・時刻・変化) だけで、損益は計算しない。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["STAGE4_VAULT"] = "1"
os.chdir(ROOT)

import run_stage4 as r4  # noqa: E402
from alchemy_stock.recorder import rules  # noqa: E402
from alchemy_stock.recorder.prepare import ForecastBook, fins_rows  # noqa: E402

A, B = pd.Timestamp("2025-11-01"), pd.Timestamp("2026-10-30")

fins = duckdb.sql("""select Code, DiscDate, DiscTime, DiscNo, DocType, CurFYEn, NxtFYEn, FOP, FOdP, FNP, NxFOP, NxFOdP, NxFNp
  from read_csv('data/jquants/fins/summary/**/*.csv.gz', union_by_name=true, all_varchar=true)""").df()
rows = fins_rows(fins)
book, ev = ForecastBook(), []
for r in rows.itertuples():
    new = {"op": r.op, "odp": r.odp, "np": r.np}
    if r.is_new and A <= r.d <= B and rules.in_session_49(r.t[:5]):
        ch = rules.forecast_change({k: (None if v != v else v) for k, v in new.items()}, book.prev(r.code, r.fy))
        if ch:
            ev.append((r.code, r.d, r.t, ch[0], ch[1]))
    book.update(r.code, r.fy, new)
ev = pd.DataFrame(ev, columns=["code", "d", "t", "item", "rate"]).drop_duplicates(["code", "d", "t"])
i = r4.dates.searchsorted(ev.d)
ev["big"] = [r4.big(c, k) if k < len(r4.dates) and r4.dates[k] == d else False for c, k, d in zip(ev.code, i, ev.d)]
mine = ev[ev.big]

ref = pd.read_pickle(sys.argv[1])
a = set(zip(ref.code, ref.d, ref.t.astype(str)))
b = set(zip(mine.code, mine.d, mine.t))
print(f"# 49 の出来事の突き合わせ ({A:%Y-%m-%d}〜{B:%Y-%m-%d})\n")
print(f"- 検証の一覧 {len(a)}件 / 記録係 {len(b)}件、一致 {len(a & b)}件、検証だけ {len(a - b)}件、記録係だけ {len(b - a)}件")
both = ref.merge(mine, on=["code", "d"], suffixes=("", "_m"))
both = both[both.t.astype(str) == both.t_m]
side = (both.rate > 0) == (both.rate_m > 0)
print(f"- 一致したものの向き (上げ・下げ) が同じ: {int(side.sum())} / {len(both)}件、変化の差の最大 {float((both.rate - both.rate_m).abs().max()):.2e}")
for x in sorted(a - b)[:8]:
    print(f"  - 検証だけ: {x[0]} {x[1]:%Y-%m-%d} {x[2]}")
for x in sorted(b - a)[:8]:
    print(f"  - 記録係だけ: {x[0]} {x[1]:%Y-%m-%d} {x[2]}")
