"""49 の金庫 (PREREG 段階4): 2025-11-01〜2026-10-30 の取引時間中の業績予想の変更、前の取引日の時点で時価総額の上位 1,344 (普通株)。"""
import sys
sys.path.insert(0, "scripts"); sys.path.insert(0, "src")
import duckdb, pandas as pd, numpy as np
from stage1_numbers import build
S = sys.argv[1]
con = duckdb.connect(); build(con)
ev = con.execute("""
  select code, d, t, case when vop is not null and p_op not in (0) and vop <> p_op then (vop - p_op) / abs(p_op)
                          when vodp is not null and p_odp not in (0) and vodp <> p_odp then (vodp - p_odp) / abs(p_odp)
                          when vnp is not null and p_np not in (0) and vnp <> p_np then (vnp - p_np) / abs(p_np) end rate
  from fc where is_new and d between '2025-11-01' and '2026-10-30'
    and ((t >= '09:00:00' and t < '11:30:00') or (t >= '12:30:00' and t < '15:25:00'))""").df()
ev = ev.dropna(subset=["rate"]).drop_duplicates(["code", "d", "t"]); ev["d"] = pd.to_datetime(ev.d)
F = ["data/jquants/equities/bars/daily/historical/2025/*.csv.gz", "data/jquants/equities/bars/daily/historical/2026/*.csv.gz", "data/jquants/equities/bars/daily/live/*.csv.gz"]
px = duckdb.sql(f"select distinct Code code, Date::date d, C::double c, O::double o from read_csv({F}, union_by_name=true, all_varchar=true) where Date >= '2025-06-01'").df(); px["d"] = pd.to_datetime(px.d)
sh = duckdb.sql("""select Code code, DiscDate::date dd, try_cast(ShOutFY as double) - coalesce(try_cast(TrShFY as double), 0) sh
  from read_csv('data/jquants/fins/summary/**/*.csv.gz', union_by_name=true, all_varchar=true) where DiscDate between '2024-01-01' and '2026-10-30' and ShOutFY is not null""").df()
sh["dd"] = pd.to_datetime(sh.dd); sh = sh.dropna()
mm = duckdb.sql("""select Code code, max_by(S33Nm, Date) s33, max_by(MktNm, Date) mkt from read_csv('data/jquants/equities/master/**/*.csv.gz', union_by_name=true, all_varchar=true) where Date >= '2025-01-01' group by 1""").df()
common = set(mm[(mm.s33.notna()) & (mm.s33 != "その他") & (mm.mkt != "TOKYO PRO MARKET")].code)
px = pd.merge_asof(px.sort_values("d"), sh.sort_values("dd").rename(columns={"dd": "d"}), on="d", by="code").sort_values(["code", "d"])
px["cap"] = np.where(px.code.isin(common), px.c * px.sh, np.nan)
px["capp"] = px.groupby("code").cap.shift(1); px["rk"] = px.groupby("d").capp.rank(ascending=False)
ev = ev.merge(px[["code", "d", "rk"]], on=["code", "d"], how="left")
ev = ev[(ev.rk <= 1344) & ev.code.isin(common)].copy()
print(len(ev), "件", "上げ", int((ev.rate > 0).sum()), "下げ", int((ev.rate < 0).sum()), "月ごと", ev.groupby(ev.d.dt.to_period("M")).size().to_dict())
ev.to_pickle(f"{S}/vault49_events.pkl")
def hm(t, dd):
    s = int(t[:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8]) + dd; return f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"
cond = " or ".join(f"(Code in ('{r.code}', '13060') and Date='{r.d:%Y-%m-%d}' and Time between '{hm(r.t,-120)}' and '{hm(r.t,600)}')" for r in ev.itertuples())
codes = sorted(set(ev.code) | {"13060"})
sql = f"select Code, Date, Time, Price, TradingVolume from `alchemy-stock-513407.market.trades` where Date between '2025-11-01' and '2026-10-30' and Code in ({','.join(repr(c) for c in codes)}) and ({cond})"
open(f"{S}/vault49_ticks.sql", "w").write(sql)
