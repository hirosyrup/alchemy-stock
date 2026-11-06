"""段階3 の金庫 (research/stage3_supply/PREREG.md): S16 (2025-11-01〜2026-10-30 だけ) と S17 (2024-11-01〜2026-10-30) を1回だけ集計する。
    uv run python scripts/build_stage4_vault_db.py
    uv run python scripts/run_stage3_vault.py > data/results/stage3_vault.md
形・対象・コスト (0.2% + 借りる料金 年 3.9%) は段階3 の宣言のまま。合格: 1日の平均 > 0、かつ年換算シャープレシオが検証期間の半分以上 (S16 3.39 → 1.70、S17 2.14 → 1.07)。
参考 (合否に使わない): 前の取引日の時点で時価総額の上位 1,344 だけ、Saxo の条件 (保有コスト 売り 年1.9%) での成績。
"""
import json
import os
import sys

os.environ["STAGE3_VAULT"] = "1"
sys.path.insert(0, "scripts")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import run_stage3 as r  # noqa: E402

SX = {x["Symbol"].split(":")[0] + "0" for x in json.load(open("data/saxo_list/cfd_tyo_20261105.json"))}
CAP = r.CR * np.nan
sh = r.duckdb.sql("""select Code code, DiscDate::date dd, try_cast(ShOutFY as double) - coalesce(try_cast(TrShFY as double), 0) sh
  from read_csv('data/jquants/fins/summary/**/*.csv.gz', union_by_name=true, all_varchar=true) where DiscDate between '2022-01-01' and '2026-10-30' and ShOutFY is not null""").df()
sh["dd"] = pd.to_datetime(sh.dd)
shp = sh.dropna().groupby(["dd", "code"]).sh.last().unstack()
shp.index = pd.DatetimeIndex(shp.index).as_unit(r.dates.unit)
SH = shp.reindex(shp.index.union(r.dates)).ffill().reindex(r.dates).reindex(columns=r.codes)
RK = (r.CR * SH).rank(axis=1, ascending=False).shift(1)
NEED = {"S16": 3.39 / 2, "S17": 2.14 / 2}
r.ALPHA = 0.025
print("# 段階3 の金庫 (research/stage3_supply/PREREG.md): S16・S17\n")
print("形・対象・コスト (往復 0.2% + 借りる料金 年 3.9%) は段階3 の宣言のまま。範囲は 95% (報告だけ)")
for key, name, fn, (a, b) in (("S16", "S16 立会外分売 (発表の次の始値で売り、分売の日の始値で買い戻す)", r.t_s16, ("2025-11-01", "2026-10-30")),
                              ("S17", "S17 公募増資・売出し (貸借、発表の次の始値で売り、価格の決定の次の始値で買い戻す)", r.t_s17, ("2024-11-01", "2026-10-30"))):
    t = r.clean(fn())
    lg, tot = r.legs(t)
    t = t.assign(tot=tot)
    days = (t.dx - t.de).dt.days
    t["net"] = t.tot - r.COST - days * r.BORROW / 365
    t["net_saxo"] = t.tot - r.COST - days * 0.019 / 365
    q = ((t.de >= a) & (t.de <= b) & t.tot.notna()).values
    tt = t[q].reset_index(drop=True)
    ll = lg[lg.k.isin(np.where(q)[0])].copy()
    ll["k"] = ll.k.map({o: n for n, o in enumerate(np.where(q)[0])})
    idx = np.arange(r.pos[r.dates[r.dates >= a][0]], max(r.pos[r.dates[r.dates <= b][-1]], int(tt.ix.max()) if len(tt) else 0) + 1)
    s = r.book(tt, ll, r.COST).reindex(idx).fillna(0.0)
    lo, hi = r.boot(s)
    tp = r.C[r.B].iloc[idx].pct_change().dropna()
    ok = s.mean() > 0 and r.sr(s.values) >= NEED[key]
    yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
    print(f"\n## {name}\n")
    print(f"- 期間 {a} 〜 {b}: {len(tt)}件 (年 {len(tt) / yrs:.0f}件)、持つ暦日 中央 {days[q].median():.0f}日")
    print(f"- 1日の平均 (コスト込み) {s.mean() * 1e4:+.1f} bp、95% 範囲 {lo * 1e4:+.1f} 〜 {hi * 1e4:+.1f} bp、年換算 SR {r.sr(s.values):+.2f} (必要 {NEED[key]:.2f}、1306 買い持ち {r.sr(tp.values):+.2f}) → **{'合格' if ok else '不合格'}**")
    if len(tt):
        print(f"- 1回 (コスト込み): 平均 {tt.net.mean() * 100:+.2f}%・中央 {tt.net.median() * 100:+.2f}%・勝ち {(tt.net > 0).mean():.0%}")
        print("- 年ごと (1回の平均): " + "、".join(f"{y} {g.net.mean() * 100:+.2f}% ({len(g)})" for y, g in tt.groupby(tt.de.dt.year)))
        print("- 一番悪い 5件: " + "、".join(f"{x.code} {x.de:%Y-%m-%d} {x.net * 100:+.1f}%" for x in tt.nsmallest(5, "net").itertuples()))
        big = np.array([bool(RK[cd].iat[ie] <= 1344) if cd in RK.columns else False for cd, ie in zip(tt.code, tt.ie)])
        u = tt[big]
        print(f"- 参考 (合否に使わない): 時価総額の上位 1,344 だけ {len(u)}件、Saxo の保有コスト (売り 年1.9%) で 1回 平均 {u.net_saxo.mean() * 100:+.2f}%・中央 {u.net_saxo.median() * 100:+.2f}%" if len(u) else "- 参考: 時価総額の上位 1,344 は 0件")
        v = tt[tt.code.isin(SX)]
        print(f"- 参考: 今の Saxo の一覧にある会社 {len(v)}件、1回 平均 {v.net_saxo.mean() * 100:+.2f}%・中央 {v.net_saxo.median() * 100:+.2f}%" if len(v) else "- 参考: 今の Saxo の一覧にある会社は 0件")
