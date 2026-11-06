"""段階4 の金庫 (research/stage4_saxo/PREREG.md の追記): 54・25・56 を 2024-11-01〜2026-10-30 で1回だけ集計する。
    uv run python scripts/build_stage4_vault_db.py
    uv run python scripts/run_stage4_vault.py > data/results/stage4_vault.md
合格: 1日の平均 > 0 (コスト込み)、かつ年換算シャープレシオが検証期間の半分以上 (54: 1.16、25: 0.71、56: 0.57)。範囲は 95% (報告だけ)。
"""
import os
import sys

os.environ["STAGE4_VAULT"] = "1"
sys.path.insert(0, "scripts")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import run_stage4 as r  # noqa: E402

r.ALPHA = 0.025
NEED = {"54": 2.32 / 2, "25": 1.41 / 2, "56": 1.13 / 2}
print("# 段階4 の金庫 (2024-11-01〜2026-10-30): 54・25・56\n")
print("対象・コスト・成績の付け方は段階4 の検証と同じ。範囲は 95% (報告だけ)。25・56 は検証で基準を満たしておらず、例外として進めた")
res = {}
for key, name, fn in (("54", "54 悪い知らせの −10% 以下の窓を寄りで売り、5日", lambda: r.gap_trades(54)),
                      ("25", "25 本決算の驚き (上を買い・下を売り、20日)", r.t25),
                      ("56", "56 開示なしで 1日 +10% 以上上げた翌朝に売り、20日", lambda: r.gap_trades(56))):
    t = r.to_frame(fn())
    lg, tot = r.legs(t)
    t = t.assign(tot=tot)
    t["net"] = t.tot - r.fee(t, r.COST)
    a, b = r.PERIODS["金庫"]
    q = ((t.de >= a) & (t.de <= b) & t.tot.notna()).values
    tt = t[q].reset_index(drop=True)
    ll = lg[lg.k.isin(np.where(q)[0])].copy()
    ll["k"] = ll.k.map({o: n for n, o in enumerate(np.where(q)[0])})
    idx = np.arange(r.pos[r.dates[r.dates >= a][0]], max(r.pos[r.dates[r.dates <= b][-1]], int(tt.ix.max()) if len(tt) else 0) + 1)
    out = {cst: r.book(tt, ll, cst).reindex(idx).fillna(0.0) for cst in (0, r.COST, 0.004)}
    s = out[r.COST]
    lo, hi = r.boot(s)
    tp = r.C[r.B].iloc[idx].pct_change().dropna()
    ok = s.mean() > 0 and r.sr(s) >= NEED[key]
    res[name] = ok
    yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
    print(f"\n## {name}\n")
    print(f"- {len(tt)}件 (年 {len(tt) / yrs:.0f}件)")
    print(f"- 1日の平均 (コスト 0.2%) {s.mean() * 1e4:+.1f} bp、95% 範囲 {lo * 1e4:+.1f} 〜 {hi * 1e4:+.1f} bp、年換算 SR {r.sr(s):+.2f} (必要 {NEED[key]:.2f}、1306 買い持ち {r.sr(tp):+.2f}) → **{'合格' if ok else '不合格'}**")
    print(f"- コスト 0% / 0.4%: 1日 {out[0].mean() * 1e4:+.1f} / {out[0.004].mean() * 1e4:+.1f} bp、SR {r.sr(out[0]):+.2f} / {r.sr(out[0.004]):+.2f}")
    if len(tt):
        print(f"- 1回 (コスト込み): 平均 {tt.net.mean() * 100:+.2f}%・中央 {tt.net.median() * 100:+.2f}%・勝ち {(tt.net > 0).mean():.0%}。持つ数/日 中央 {ll.groupby('i').size().median():.0f}・最大 {ll.groupby('i').size().max()}")
        for sd, nm in ((1, "買い"), (-1, "売り")):
            u = tt[tt.side == sd]
            if len(u) and len(u) < len(tt):
                print(f"  - {nm}: {len(u)}件 平均 {u.net.mean() * 100:+.2f}%・中央 {u.net.median() * 100:+.2f}%")
        v = tt[tt.code.isin(r.SX)]
        print(f"  - 今の Saxo の一覧にある会社: {len(v)}件 平均 {v.net.mean() * 100:+.2f}%・中央 {v.net.median() * 100:+.2f}%")
        print("  - 年ごと (1回の平均、コスト込み): " + "、".join(f"{y} {g.net.mean() * 100:+.2f}% ({len(g)})" for y, g in tt.groupby(tt.de.dt.year)))
        print("  - 件数が多い日: " + "、".join(f"{d:%Y-%m-%d} {n}" for d, n in tt.groupby("de").size().nlargest(3).items()))
        w = tt.nsmallest(5, "net")
        print("  - 一番悪い 5件: " + "、".join(f"{x.code} {x.de:%Y-%m-%d} {x.net * 100:+.1f}%" for x in w.itertuples()))
        sv = s[s.index < len(r.dates)]
        mo = sv.groupby(r.dates[sv.index].to_period("M")).sum()
        print(f"  - 一番悪い月: {mo.idxmin()} {mo.min() * 100:+.1f}% (1日の値の合計)")
print("\n## まとめ\n")
for k, v in res.items():
    print(f"- {k}: {'合格' if v else '不合格'}")
