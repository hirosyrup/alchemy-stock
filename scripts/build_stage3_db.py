"""段階3 (research/stage3_supply/PREREG.md) の表を data/explore/stage3.duckdb に作る。
学習期間と検証期間 (2016-10〜2024-10-31) だけ。2024-11 以降 (日足の金庫) は入れない。
価格は分割を調整 (その日より後の AdjFactor の積、2024-10-31 までの分だけ)。
    uv run python scripts/build_stage3_db.py
"""
import duckdb

END = "2024-10-31"
c = duckdb.connect("data/explore/stage3.duckdb")
files = [f"data/jquants/equities/bars/daily/historical/{y}/*.csv.gz" for y in range(2016, 2025)]
c.sql(f"""create or replace table raw as
select Date::date d, Code code, O, H, L, C, Vo, Va, AdjFactor af
from read_csv({files}, union_by_name=true, types={{'Code':'VARCHAR'}})
where Date between '2016-10-01' and '{END}' and C is not null""")
c.sql("""create or replace table p as
with a as (select *, exp(coalesce(sum(ln(af)) over (partition by code order by d rows between 1 following and unbounded following), 0)) k from raw)
select code, d, O*k o, H*k h, L*k l, C*k c, C c_raw, Vo, Va, row_number() over (partition by code order by d) i from a""")
c.sql("drop table raw")
c.sql(f"""create or replace table tpx as select Date::date d, O o, C c from read_csv('data/jquants/indices/bars/daily/topix/historical/*/*.csv.gz')
where Date between '2016-10-01' and '{END}' order by d""")
c.sql("create or replace table cal as select d, row_number() over (order by d) t from tpx")
c.sql(f"""create or replace table m as select Date::date d, Code code, CoName, S33Nm, MktNm, MrgnNm
from read_csv('data/jquants/equities/master/historical/*/*.csv.gz', union_by_name=true, types={{'Code':'VARCHAR'}})
where Date between '2016-10-01' and '{END}'""")
c.sql(f"""create or replace table fins as select Code code, DiscDate::date disc, month(CurFYEn::date) fm
from read_csv('data/jquants/fins/summary/historical/*/*.csv.gz', union_by_name=true, types={{'Code':'VARCHAR'}})
where DiscDate between '2016-10-01' and '{END}' and CurFYEn is not null""")
c.sql(f"""create or replace table d as select code, date::date date, category_name, title, doc_id
from read_json('data/jpx_listed/disclosures/*.jsonl', format='newline_delimited', columns={{code:'VARCHAR', date:'VARCHAR', category_name:'VARCHAR', title:'VARCHAR', doc_id:'VARCHAR'}})
where date between '2016-10-01' and '{END}'""")
for t in ("p", "tpx", "cal", "m", "fins", "d"):
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
print("p", c.execute("select min(d), max(d), count(distinct code) from p").fetchall())
print("1306", c.execute("select count(*), min(d), max(d) from p where code = '13060'").fetchall())
print("d", c.execute("select min(date), max(date) from d").fetchall())
