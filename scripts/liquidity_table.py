"""ふるい分け d (売買の量) の基準を決めるための表を出す (PLAN.md §3)。

候補の種類の出来事 (会社×日) ごとに、開示の日より前の直近20営業日の平均売買代金を J-Quants の日足から計算し、
基準をいくらにすると1年あたり何件・何社残るかを数える。値動き (その後の株価) は見ない。

    uv run python scripts/liquidity_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.liquidity import create_adv, create_passed_docs  # noqa: E402
from alchemy_stock.screening import CANDIDATE, GROUP_OF  # noqa: E402

THRESHOLDS = [0, 1e7, 3e7, 5e7, 1e8, 3e8, 1e9]  # 円。5e7 は採用した基準 (liquidity.MIN_ADV)
PERIODS = [("2017-01-01", "2021-10-31", "2017-01〜2021-10 (上場廃止の会社が一部抜けている)"), ("2021-11-01", "2025-12-31", "2021-11〜2025-12")]


def main() -> None:
    con = duckdb.connect()
    create_adv(con)
    create_passed_docs(con)
    # 開示を出来事 (会社×日) にまとめる。決算短信以外の候補の種類を含むかも残す
    others = [k for k, g in GROUP_OF.items() if g == CANDIDATE and k != "決算短信"]
    con.sql(f"""
        create table joined as
        select code, date, any_value(adv) as adv, bool_or(len(list_intersect(kinds, {others})) > 0) as has_non_tanshin
        from passed group by code, date
    """)
    n, missing = con.execute("select count(*), count(*) filter (where adv is null) from joined").fetchone()
    print(f"候補の出来事 {n:,}件のうち、前の日の株価が見つからない {missing:,}件 ({missing / n:.1%})\n")

    for lo, hi, label in PERIODS:
        years = con.execute(f"select (date '{hi}' - date '{lo}' + 1) / 365.25").fetchone()[0]
        print(f"### {label} ({years:.1f}年)\n")
        print("| 20日平均の売買代金 | 出来事/年 (全部) | うち短信以外を含む/年 | 社数 |")
        print("|---|---|---|---|")
        for t in THRESHOLDS:
            all_, non, cos = con.execute(f"""
                select count(*), count(*) filter (where has_non_tanshin), count(distinct code)
                from joined where date between '{lo}' and '{hi}' and adv >= {t}
            """).fetchone()
            name = "株価がある全部" if t == 0 else f"{t / 1e8:g}億円以上"
            print(f"| {name} | {all_ / years:,.0f} | {non / years:,.0f} | {cos:,} |")
        print()


if __name__ == "__main__":
    main()
