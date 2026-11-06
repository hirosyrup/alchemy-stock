"""49 の金庫 (PREREG 段階4) を1回だけ集計する。5秒後以降の最初の約定で入り、次の取引日の終値で手放す。1306 は開示の分の1分足の終値 → 次の取引日の終値。
1日ごとのかご (入った日: 入った値段 → その日の終値、次の日: 前日の終値 → 終値)、コスト 往復 0.2% + 保有コスト (買い 年4.0%、売り 年1.9%)。範囲は 40営業日のブロック 1万回、95%。"""
import sys, bisect, json
import numpy as np, pandas as pd, duckdb
S = sys.argv[1]
ev = pd.read_pickle(f"{S}/vault49_events.pkl").sort_values(["d", "t"]).reset_index(drop=True)
tk = pd.read_csv(f"{S}/vault49_ticks.csv", dtype={"Code": str})
tk["sec"] = tk.Time.str.slice(0, 2).astype(int) * 3600 + tk.Time.str.slice(3, 5).astype(int) * 60 + tk.Time.str.slice(6).astype(float)
tk = tk.sort_values(["Code", "Date", "sec"]); G = {k: (g.sec.values, g.Price.values) for k, g in tk.groupby(["Code", "Date"])}
F = ["data/jquants/equities/bars/daily/historical/2025/*.csv.gz", "data/jquants/equities/bars/daily/historical/2026/*.csv.gz", "data/jquants/equities/bars/daily/live/*.csv.gz"]
dl = duckdb.sql(f"select distinct Code code, Date::date d, O::double o, C::double c, AdjFactor::double af from read_csv({F}, union_by_name=true, all_varchar=true) where Date >= '2025-10-01'").df()
dl["d"] = pd.to_datetime(dl.d); dl = dl.drop_duplicates(["code", "d"]).sort_values(["code", "d"])
dates = pd.DatetimeIndex(sorted(dl[dl.code == "13060"].d)); pos = {d: i for i, d in enumerate(dates)}
Cd = {(c, d): (cl, op, af) for c, d, cl, op, af in zip(dl.code, dl.d, dl.c, dl.o, dl.af)}
MF = ["data/jquants/equities/bars/minute/historical/2025/*.csv.gz", "data/jquants/equities/bars/minute/historical/2026/*.csv.gz", "data/jquants/equities/bars/minute/live/*.csv.gz"]
m = duckdb.sql(f"select distinct Date::date d, Time t, C::double c from read_csv({MF}, union_by_name=true, all_varchar=true) where Code = '13060' and Date >= '2025-11-01'").df()
m["d"] = pd.to_datetime(m.d); m["sec"] = m.t.str.slice(0, 2).astype(int) * 3600 + m.t.str.slice(3, 5).astype(int) * 60
MB = {d: (g.sec.values, g.c.values) for d, g in m.sort_values(["d", "sec"]).groupby("d")}
SX = {x["Symbol"].split(":")[0] + "0" for x in json.load(open("data/saxo_list/cfd_tyo_20261105.json"))}
COST, FIN = 0.002, {1: 0.040, -1: 0.019}
rows, legs, drop = [], [], {"約定なし": 0, "次の日の値段なし": 0, "1306 の分足なし": 0, "同じ会社を持っている": 0}
held = {}
for r in ev.itertuples():
    sg = 1 if r.rate > 0 else -1; k = (r.code, r.d.strftime("%Y-%m-%d"))
    i = pos.get(r.d)
    if i is None or i + 1 >= len(dates): drop["次の日の値段なし"] += 1; continue
    if held.get(r.code, -1) >= i: drop["同じ会社を持っている"] += 1; continue
    d1 = dates[i + 1]
    t = int(r.t[:2]) * 3600 + int(r.t[3:5]) * 60 + int(r.t[6:8])
    if k not in G: drop["約定なし"] += 1; continue
    sec, px = G[k]
    def at(s):
        j = bisect.bisect_left(sec, t + s)
        return (px[j], sec[j]) if j < len(sec) and sec[j] <= t + s + 600 else (None, None)
    pe, te = at(5)
    c0, c1 = Cd.get((r.code, r.d)), Cd.get((r.code, d1)); b0d, b1d = Cd.get(("13060", r.d)), Cd.get(("13060", d1))
    mb = MB.get(r.d)
    if pe is None: drop["約定なし"] += 1; continue
    if not (c0 and c1 and b0d and b1d): drop["次の日の値段なし"] += 1; continue
    if mb is None or bisect.bisect_right(mb[0], t) < 1: drop["1306 の分足なし"] += 1; continue
    bm = mb[1][bisect.bisect_right(mb[0], t) - 1]
    cl1 = c1[0] / (c1[2] if c1[2] and c1[2] > 0 else 1.0); bc1 = b1d[0] / (b1d[2] if b1d[2] and b1d[2] > 0 else 1.0)
    v0 = sg * ((c0[0] / pe - 1) - (b0d[0] / bm - 1)); v1 = sg * ((cl1 / c0[0] - 1) - (bc1 / b0d[0] - 1))
    tot = sg * ((cl1 / pe - 1) - (bc1 / bm - 1))
    fee = COST + (d1 - r.d).days * FIN[sg] / 365
    ref = {}
    for s in (3, 10):
        p2, _ = at(s)
        if p2: ref[s] = sg * ((cl1 / p2 - 1) - (bc1 / bm - 1)) - fee
    no = c1[1] / (c1[2] if c1[2] and c1[2] > 0 else 1.0); bno = b1d[1] / (b1d[2] if b1d[2] and b1d[2] > 0 else 1.0)
    rows.append(dict(code=r.code, d=r.d, t=r.t, side=sg, rate=r.rate, lag=te - t, gross=tot, net=tot - fee, r3=ref.get(3), r10=ref.get(10),
                     nopen=sg * ((no / pe - 1) - (bno / bm - 1)) - fee, sx=r.code in SX))
    k_ = len(rows) - 1
    legs += [(k_, i, v0 - fee), (k_, i + 1, v1)]
    held[r.code] = i + 1
x = pd.DataFrame(rows); L = pd.DataFrame(legs, columns=["k", "i", "v"])
a, b = pos[dates[dates >= "2025-11-01"][0]], pos[dates[dates <= "2026-10-30"][-1]]
idx = np.arange(a, b + 2)
s = L.groupby("i").v.mean().reindex(idx).fillna(0.0)
rng = np.random.default_rng(0); arr = s.values; n = len(arr); bl = 40; nb = int(np.ceil(n / bl))
st = rng.integers(0, n - bl + 1, size=(10000, nb)); mu = arr[(st[:, :, None] + np.arange(bl)).reshape(10000, -1)[:, :n]].mean(axis=1)
lo, hi = np.quantile(mu, [0.025, 0.975])
sr = lambda v: v.mean() / v.std(ddof=1) * np.sqrt(245)
bc = pd.Series([Cd[("13060", d)][0] / (Cd[("13060", d)][2] or 1) for d in dates[a:b + 1]]).pct_change().dropna()
ok = lo > 0 and sr(s) > sr(bc)
print("# 49 の金庫 (2025-11-01〜2026-10-30) の集計 (research/stage4_saxo/PREREG.md)\n")
print(f"出来事 {len(ev)}件 → 使った {len(x)}件。使えなかった: {drop}")
print(f"- 1日の平均 (コスト込み) {s.mean()*1e4:+.1f} bp、95% 範囲 {lo*1e4:+.1f} 〜 {hi*1e4:+.1f} bp、年換算 SR {sr(s):+.2f} (1306 買い持ち {sr(bc):+.2f}) → **{'合格' if ok else '不合格'}**")
print(f"- 1回 (コスト込み): 平均 {x.net.mean()*100:+.2f}%・中央 {x.net.median()*100:+.2f}%・勝ち {(x.net>0).mean():.0%} (コスト前 平均 {x.gross.mean()*100:+.2f}%)。入るまでの秒 中央 {x.lag.median():.1f}")
for sd, nm in ((1, "買い (上方)"), (-1, "売り (下方)")):
    u = x[x.side == sd]; print(f"  - {nm}: {len(u)}件 平均 {u.net.mean()*100:+.2f}%・中央 {u.net.median()*100:+.2f}%・勝ち {(u.net>0).mean():.0%}")
u = x[x.rate.abs() >= .1]; print(f"  - 予想の変化 ±10% 以上: {len(u)}件 平均 {u.net.mean()*100:+.2f}%・中央 {u.net.median()*100:+.2f}%")
u = x[x.sx]; print(f"  - 今の Saxo の一覧にある会社: {len(u)}件 平均 {u.net.mean()*100:+.2f}%・中央 {u.net.median()*100:+.2f}%")
u = x[x.t >= "15:00:00"]; print(f"  - 15:00 台: {len(u)}件 平均 {u.net.mean()*100:+.2f}%・中央 {u.net.median()*100:+.2f}%")
print(f"- 参考 (合否に使わない): 3秒後 平均 {x.r3.mean()*100:+.2f}%・中央 {x.r3.median()*100:+.2f}%、10秒後 {x.r10.mean()*100:+.2f}%・{x.r10.median()*100:+.2f}%、5秒後 → 次の日の始値 {x.nopen.mean()*100:+.2f}%・{x.nopen.median()*100:+.2f}%")
print("- 月ごと (1回の平均、件数): " + "、".join(f"{p} {g.net.mean()*100:+.2f}% ({len(g)})" for p, g in x.groupby(x.d.dt.to_period("M"))))
print("- 一番悪い 5件: " + "、".join(f"{r.code} {r.d:%Y-%m-%d} {r.t[:5]} {r.net*100:+.1f}%" for r in x.nsmallest(5, "net").itertuples()))
mo = s[s.index < len(dates)].groupby(dates[s.index[s.index < len(dates)]].to_period("M")).sum(); print(f"- 一番悪い月: {mo.idxmin()} {mo.min()*100:+.1f}% (1日の値の合計)")
print(f"- 同じ日に持つ数: 中央 {L.groupby('i').size().median():.0f}・最大 {L.groupby('i').size().max()}")
x.to_pickle(f"{S}/vault49_scored.pkl")
