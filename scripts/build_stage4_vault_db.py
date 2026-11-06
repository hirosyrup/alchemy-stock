"""段階4 の金庫 (research/stage4_saxo/PREREG.md の追記) の表を data/explore/stage4_vault.duckdb に作る。
2024-06-01〜2026-10-30 (前の 20日などを測るため 2024-06 から)。作り方は build_stage3_db.py と同じ (価格は分割を調整)。
    uv run python scripts/build_stage4_vault_db.py
"""
import duckdb

START, END = "2024-06-01", "2026-10-30"
c = duckdb.connect("data/explore/stage4_vault.duckdb")
files = [f"data/jquants/equities/bars/daily/historical/{y}/*.csv.gz" for y in (2024, 2025, 2026)] + ["data/jquants/equities/bars/daily/live/*.csv.gz"]
c.sql(f"""create or replace table raw as
select distinct Date::date d, Code code, O, H, L, C, Vo, Va, AdjFactor af
from read_csv({files}, union_by_name=true, types={{'Code':'VARCHAR'}})
where Date between '{START}' and '{END}' and C is not null""")
c.sql("create or replace table raw as select * from raw qualify row_number() over (partition by code, d order by Va desc nulls last) = 1")
c.sql("""create or replace table p as
with a as (select *, exp(coalesce(sum(ln(af)) over (partition by code order by d rows between 1 following and unbounded following), 0)) k from raw)
select code, d, O*k o, H*k h, L*k l, C*k c, C c_raw, Vo, Va, row_number() over (partition by code order by d) i from a""")
c.sql("drop table raw")
c.sql(f"""create or replace table m as select distinct Date::date d, Code code, CoName, S33Nm, MktNm, MrgnNm
from read_csv('data/jquants/equities/master/**/*.csv.gz', union_by_name=true, types={{'Code':'VARCHAR'}})
where Date between '{START}' and '{END}'""")
c.sql(f"""create or replace table d as select code, date::date date, category_name, title, doc_id
from read_json('data/jpx_listed/disclosures/*.jsonl', format='newline_delimited', columns={{code:'VARCHAR', date:'VARCHAR', category_name:'VARCHAR', title:'VARCHAR', doc_id:'VARCHAR'}})
where date between '{START}' and '{END}'""")
for t in ("p", "m", "d"):
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
print("p", c.execute("select min(d), max(d), count(distinct code) from p").fetchall())
print("1306", c.execute("select count(*), min(d), max(d) from p where code = '13060'").fetchall())
