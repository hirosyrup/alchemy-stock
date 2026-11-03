"""ふるい分け d (売買の量) の計算 (PLAN.md §3)。

開示の日より前の直近 WINDOW 営業日の平均売買代金を、J-Quants の日足 (一括ダウンロードの CSV) から出す。
開示の後は売買が急に増えるので、開示の日の分は使わない (as-of で「開示の日より前」の最新の取引日につなぐ)。
"""
from __future__ import annotations

import duckdb

BARS = "data/jquants/equities/bars/daily/**/*.csv.gz"
SCREENED = "data/screened/disclosures.jsonl"
WINDOW = 20
MIN_ADV = 5e7  # 円。1回50万円が普段の1日の売買代金の1%以下 (PLAN.md §3 の仮案、2026-11-02 に採用)


def create_adv(con: duckdb.DuckDBPyConnection, bars: str = BARS) -> None:
    """adv(code, date, adv): その日までの WINDOW 営業日の平均売買代金。"""
    con.sql(f"""
        create table adv as
        select Code as code, Date as date, avg(coalesce(Va, 0)) over w as adv
        from read_csv('{bars}', union_by_name=true)
        window w as (partition by Code order by Date rows between {WINDOW - 1} preceding and current row)
    """)


def create_passed_docs(con: duckdb.DuckDBPyConnection, screened: str = SCREENED, kinds: list[str] | None = None) -> None:
    """passed(doc_id, code, date, kinds, adv): ふるい分け a〜c を通った開示に、開示の日より前の平均売買代金を付ける。

    kinds を渡すと、b (種類) で落としたものも含めて、その種類が付いた開示だけにする (候補以外の種類を後から足すとき)。
    上場した日より前の開示など、前の日の株価が無いものは adv が NULL になる。
    """
    where = "dropped_by is null" if kinds is None else (
        f"(dropped_by is null or dropped_by = 'b.種類') and len(list_intersect(kinds, {list(kinds)})) > 0")
    con.sql(f"""
        create table passed as
        select d.doc_id, d.code, d.date, d.kinds, a.adv
        from (select doc_id, code, date::date as date, kinds from read_json('{screened}') where {where}) d
        asof left join adv a on d.code = a.code and d.date > a.date
    """)
