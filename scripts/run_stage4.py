"""段階4 の日足 8候補 (research/stage4_saxo/PREREG.md): 学習 2016-11〜2021-10 と 検証 2021-11〜2024-10 を1回だけ集計する。

    uv run python scripts/run_stage4.py > data/results/stage4_saxo.md

data/explore/stage3.duckdb (2024-10-31 まで) だけを使う。出る日が 2024-10-31 より後になる取引は使わない (金庫の値段を読まない)。
"""
from __future__ import annotations

import json
import re

import duckdb
import numpy as np
import pandas as pd

DB = "data/explore/stage3.duckdb"
PERIODS = {"学習": ("2016-11-01", "2021-10-31"), "検証": ("2021-11-01", "2024-10-31")}
NCAND = 8
ALPHA = 0.05 / NCAND / 2  # 99.375% 範囲
COST = 0.002
FIN = {1: 0.040, -1: 0.019}  # 保有コスト (年率): 買い / 売り
B = "13060"
N_TOP = 1344
rng = np.random.default_rng(0)

c = duckdb.connect(DB, read_only=True)
px = c.execute("select code, d, o, c, c_raw, Va from p").df()
px["d"] = pd.to_datetime(px.d)
dates = pd.DatetimeIndex(sorted(px.d.unique()))
pos = {d: i for i, d in enumerate(dates)}
O = px.pivot(index="d", columns="code", values="o").reindex(dates)
C = px.pivot(index="d", columns="code", values="c").reindex(dates)
CR = px.pivot(index="d", columns="code", values="c_raw").reindex(dates)
VA = px.pivot(index="d", columns="code", values="Va").reindex(dates)
ADV = VA.rolling(20, min_periods=10).mean().shift(1)
codes = O.columns

# 普通株 (ETF・REIT など「その他」と PRO 市場を除く)
mm = c.execute("select code, d, S33Nm, MktNm from m").df()
mm["d"] = pd.to_datetime(mm.d)
mm = mm.sort_values("d").groupby("code").last()
common = set(mm[(mm.S33Nm.notna()) & (mm.S33Nm != "その他") & (mm.MktNm != "TOKYO PRO MARKET")].index)

# 時価総額 = 調整前の終値 × (発行済み − 自己株)、株式数はその日より前の最新の決算短信。前の取引日の順位
sh = duckdb.sql("""select Code code, DiscDate::date dd, try_cast(ShOutFY as double) - coalesce(try_cast(TrShFY as double), 0) sh
  from read_csv('data/jquants/fins/summary/historical/*/*.csv.gz', union_by_name=true, all_varchar=true)
  where DiscDate between '2014-01-01' and '2024-10-31' and ShOutFY is not null""").df()
sh["dd"] = pd.to_datetime(sh.dd)
sh = sh.dropna().sort_values("dd")
shp = sh.groupby(["dd", "code"]).sh.last().unstack()
shp.index = pd.DatetimeIndex(shp.index).as_unit(dates.unit)
SH = shp.reindex(shp.index.union(dates)).ffill().reindex(dates).reindex(columns=codes)
CAP = CR * SH
CAP[[x for x in codes if x not in common]] = np.nan
RK = CAP.rank(axis=1, ascending=False).shift(1)  # 前の取引日の時点の順位
BIG = RK <= N_TOP

# 開示の題名 (日付だけ)
dd = c.execute("select code, date::date D, title from d").df()
dd["D"] = pd.to_datetime(dd.D)
disc = set(zip(dd.code, dd.D))


def i_after(day):
    """その日より後の最初の取引日の位置"""
    return int(dates.searchsorted(day, side="right"))


def big(code, i):
    return code in BIG.columns and 0 <= i < len(dates) and bool(BIG[code].iat[i])


def has_disc(code, i):
    """前の取引日の日付〜その日に開示があるか"""
    return any((code, x) in disc for x in pd.date_range(dates[i - 1], dates[i]))


# ---------- 出来事 → 取引 (code, side, ie, te, ix, tx)  te/tx は "o" (始値) か "c" (終値) ----------
def fins_df():
    f = duckdb.sql("""select Code code, DiscDate::date dd, CurPerType per, CurFYEn::date fye,
       coalesce(try_cast(OP as double), try_cast(OdP as double)) op, coalesce(try_cast(FOP as double), try_cast(FOdP as double)) fop
      from read_csv('data/jquants/fins/summary/historical/*/*.csv.gz', union_by_name=true, all_varchar=true)
      where DocType like '%FinancialStatements%' and DiscDate between '2014-01-01' and '2024-10-31'""").df()
    f["dd"] = pd.to_datetime(f.dd)
    f["fye"] = pd.to_datetime(f.fye)
    return f


def quintile_trades(ev, col, top_side):
    """月ごとに5つの組 (10件以上の月)。一番上の組に top_side、一番下に −top_side。20日持つ"""
    ev = ev.dropna(subset=[col]).copy()
    ev["mo"] = ev.D.dt.to_period("M")
    ev = ev.groupby("mo").filter(lambda g: len(g) >= 10)
    ev["q"] = ev.groupby("mo")[col].transform(lambda v: pd.qcut(v.rank(method="first"), 5, labels=False))
    rows = []
    for r in ev[ev.q.isin([0, 4])].itertuples():
        side = top_side if r.q == 4 else -top_side
        rows.append((r.code, side, r.i1, "o", r.i1 + 19, "c"))
    return rows


def ev_filter(f):
    """出来事の日 D の次の取引日 i1、前の取引日の時点で上位 1,344、前20日の売買代金の平均 1億円以上"""
    f = f.copy()
    f["i1"] = [i_after(x) for x in f.D]
    keep = [(cd in codes) and i1 < len(dates) and big(cd, i1) and (ADV[cd].iat[i1] >= 1e8 if np.isfinite(ADV[cd].iat[i1]) else False)
            for cd, i1 in zip(f.code, f.i1)]
    return f[keep]


def t25():
    f = fins_df()
    fy = f[f.per == "FY"].sort_values("dd")
    fp = f[f.fop.notna()][["code", "fye", "dd", "fop"]].rename(columns={"fop": "fop_prev"}).sort_values("dd")
    fy = pd.merge_asof(fy.drop(columns="fop"), fp, on="dd", by=["code", "fye"], direction="backward", allow_exact_matches=False)
    fy = fy[(fy.dd >= "2016-11-01") & fy.op.notna()].rename(columns={"dd": "D"})
    fy = ev_filter(fy)
    fy["cap"] = [CAP[cd].iat[i1 - 1] for cd, i1 in zip(fy.code, fy.i1)]
    fy["sup"] = (fy.op - fy.fop_prev) / fy.cap
    return quintile_trades(fy, "sup", +1)


def t50():
    f = fins_df()
    f = f.sort_values("dd").drop_duplicates(["code", "fye", "per"], keep="last")
    qv = f.set_index(["code", "fye", "per"]).op
    q = f[f.per.isin(["1Q", "2Q", "3Q"]) & (f.dd >= "2016-11-01") & (f.fop > 0) & f.op.notna()].copy()
    q["op_py"] = [qv.get((cd, fe - pd.DateOffset(years=1), pr), np.nan) for cd, fe, pr in zip(q.code, q.fye, q.per)]
    q["fy_py"] = [qv.get((cd, fe - pd.DateOffset(years=1), "FY"), np.nan) for cd, fe in zip(q.code, q.fye)]
    q = q[(q.fy_py > 0) & q.op_py.notna()].rename(columns={"dd": "D"})
    q = ev_filter(q)
    q["sig"] = q.op / q.fop - q.op_py / q.fy_py
    return quintile_trades(q, "sig", -1)


def gap_trades(kind):
    gap = O / C.shift(1) - 1
    mv = (C / C.shift(1) - 1).sub(C[B] / C[B].shift(1) - 1, axis=0)
    rows = []
    for code in codes:
        if code == B or code not in common:
            continue
        x = (gap if kind in (54, 55) else mv)[code]
        cond = {54: x <= -0.10, 55: x >= 0.10, 56: x >= 0.10}[kind]
        cond = cond & BIG[code] & (C[code].shift(1) >= 200)
        for i in np.where(cond.fillna(False).values)[0]:
            if i < 1:
                continue
            h = has_disc(code, i)
            if kind == 54 and h:
                rows.append((code, -1, i, "o", i + 4, "c"))
            elif kind == 55 and not h:
                rows.append((code, -1, i, "o", i, "c"))
            elif kind == 56 and not h:
                rows.append((code, -1, i + 1, "o", i + 20, "c"))
    return rows


def t43():
    a = dd[dd.title.str.contains("提出期限.*延長|第三者委員会|特別調査委員会|不適切|決算発表.*延期") & ~dd.title.str.contains("訂正")]
    a = a[~(a.title.str.contains("決算発表.*延期") & (a.D >= "2020-03-01") & (a.D <= "2020-06-30"))].sort_values("D")
    a = a[a.D >= "2016-11-01"]
    keep, last = [], {}
    for r in a.itertuples():
        if r.code in last and (r.D - last[r.code]).days <= 180:
            continue
        last[r.code] = r.D
        keep.append(r.Index)
    rows = []
    for r in a.loc[keep].itertuples():
        i1 = i_after(r.D)
        if r.code in codes and i1 < len(dates) and big(r.code, i1):
            rows.append((r.code, -1, i1, "o", i1 + 19, "c"))
    return rows


def t41():
    a = dd[dd.title.str.contains("立会外分売") & dd.title.str.contains("実施") & ~dd.title.str.contains("訂正|中止")]
    rows = []
    for r in a[a.D >= "2016-10-31"].drop_duplicates(["code", "D"]).itertuples():
        i = i_after(r.D)  # 分売の日
        if r.code in codes and i < len(dates) and big(r.code, i):
            rows.append((r.code, +1, i, "o", i + 4, "c"))
    return rows


def t42():
    X = "ToSTNeT|ＴｏＳＴＮｅＴ|立会外|公開買付|端数|訂正|処分|消却"
    dec = dd[dd.title.str.contains("自己株式.*取得.*決定") & ~dd.title.str.contains(X)][["code", "D"]].drop_duplicates()
    end = dd[dd.title.str.contains("取得終了|取得結果") & dd.title.str.contains("自己株式") & ~dd.title.str.contains(X)][["code", "D"]].drop_duplicates()
    eg = {k: np.sort(v.D.values) for k, v in end.groupby("code")}
    rows = []
    for r in dec[dec.D >= "2016-10-28"].itertuples():
        e = eg.get(r.code)
        if e is None:
            continue
        e = e[(e > np.datetime64(r.D)) & (e <= np.datetime64(r.D + pd.Timedelta(days=400)))]
        if not len(e):
            continue
        ie = i_after(r.D) + 1
        ix = i_after(pd.Timestamp(e[0]))
        if r.code in codes and ix > ie and ie < len(dates) and big(r.code, ie):
            rows.append((r.code, +1, ie, "o", ix, "o"))
    return rows


def to_frame(rows):
    t = pd.DataFrame(rows, columns=["code", "side", "ie", "te", "ix", "tx"])
    t = t[t.code.isin(common) & (t.ie < len(dates)) & (t.ix < len(dates)) & (t.ix >= t.ie)]  # 出る日が 2024-10-31 より後のものは使わない
    keep, last = [], {}
    for r in t.sort_values(["ie", "code"]).itertuples():  # 同じ会社を持っている間の新しい出来事は使わない
        if r.code in last and r.ie <= last[r.code]:
            continue
        last[r.code] = r.ix
        keep.append(r.Index)
    t = t.loc[keep].sort_values("ie").reset_index(drop=True)
    t["de"], t["dx"] = dates[t.ie], dates[t.ix]
    return t


# ---------- 値動き (1306 との差、side の向き) ----------
def legs(t):
    jb = codes.get_loc(B)
    Ov, Cv = O.values, C.values
    rows, tot = [], []
    for k, r in enumerate(t.itertuples()):
        j = codes.get_loc(r.code)
        s0 = Ov[r.ie, j] if r.te == "o" else Cv[r.ie, j]
        b0 = Ov[r.ie, jb] if r.te == "o" else Cv[r.ie, jb]
        if not (np.isfinite(s0) and np.isfinite(b0)):
            tot.append(np.nan)
            continue
        ps, pb = s0, b0
        for i in range(r.ie, r.ix + 1):
            last = i == r.ix
            if i == r.ie and r.te == "c":
                rows.append((k, i, 0.0))
                continue
            if last and r.tx == "o":
                s1, b1 = Ov[i, j], Ov[i, jb]
            else:
                s1, b1 = Cv[i, j], Cv[i, jb]
            if not np.isfinite(s1):
                if last:
                    nxt = C.iloc[i:, j].dropna()
                    if not len(nxt):
                        break
                    s1 = nxt.iloc[0]
                else:
                    rows.append((k, i, 0.0))
                    continue
            rows.append((k, i, r.side * ((s1 / ps - 1) - (b1 / pb - 1))))
            ps, pb = s1, b1
        tot.append(r.side * ((ps / s0 - 1) - (pb / b0 - 1)))
    return pd.DataFrame(rows, columns=["k", "i", "v"]), np.array(tot)


def fee(t, cost):
    days = (t.dx - t.de).dt.days.values
    return cost + days * np.where(t.side.values > 0, FIN[1], FIN[-1]) / 365


def book(t, lg, cost):
    f = fee(t, cost)
    x = lg.copy()
    x["v"] = x.v - np.where(x.i.values == t.ie.values[x.k.values], f[x.k.values], 0)
    return x.groupby("i").v.mean()


def boot(s, n=10000, block=40):
    a = s.values
    m = len(a)
    nb = int(np.ceil(m / block))
    st = rng.integers(0, m - block + 1, size=(n, nb))
    idx = (st[:, :, None] + np.arange(block)).reshape(n, -1)[:, :m]
    mu = a[idx].mean(axis=1)
    return np.quantile(mu, [ALPHA, 1 - ALPHA])


def sr(a):
    return a.mean() / a.std(ddof=1) * np.sqrt(245) if a.std(ddof=1) > 0 else np.nan


SX = {x["Symbol"].split(":")[0] + "0" for x in json.load(open("data/saxo_list/cfd_tyo_20261105.json"))}


def run(name, rows):
    t = to_frame(rows)
    lg, tot = legs(t)
    t = t.assign(tot=tot)
    t["net"] = t.tot - fee(t, COST)
    print(f"\n## {name}\n")
    res = {}
    for per, (a, b) in PERIODS.items():
        q = ((t.de >= a) & (t.de <= b) & t.tot.notna()).values
        tt = t[q].reset_index(drop=True)
        ll = lg[lg.k.isin(np.where(q)[0])].copy()
        ll["k"] = ll.k.map({o: n for n, o in enumerate(np.where(q)[0])})
        idx = np.arange(pos[dates[dates >= a][0]], pos[dates[dates <= b][-1]] + 1)
        idx = np.arange(idx[0], max(idx[-1], int(tt.ix.max()) if len(tt) else idx[-1]) + 1)
        out = {cst: book(tt, ll, cst).reindex(idx).fillna(0.0) for cst in (0, COST, 0.004)}
        s = out[COST]
        lo, hi = boot(s)
        tp = C[B].iloc[idx].pct_change().dropna()
        yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
        ok = (s.mean() > 0) if per == "学習" else (lo > 0 and sr(s) > sr(tp))
        res[per] = ok
        print(f"### {per} ({a} 〜 {b}): {len(tt)}件 (年 {len(tt) / yrs:.0f}件)")
        print(f"- 1日の平均 (コスト 0.2%) {s.mean() * 1e4:+.1f} bp、99.375% 範囲 {lo * 1e4:+.1f} 〜 {hi * 1e4:+.1f} bp、年換算 SR {sr(s):+.2f} (TOPIX の ETF 買い持ち {sr(tp):+.2f}) → **{'満たす' if ok else '満たさない'}**")
        print(f"- コスト 0% / 0.4%: 1日 {out[0].mean() * 1e4:+.1f} / {out[0.004].mean() * 1e4:+.1f} bp、SR {sr(out[0]):+.2f} / {sr(out[0.004]):+.2f}")
        if len(tt):
            print(f"- 1回 (コスト込み): 平均 {tt.net.mean() * 100:+.2f}%・中央 {tt.net.median() * 100:+.2f}%・勝ち {(tt.net > 0).mean():.0%}。持つ数/日 中央 {ll.groupby('i').size().median():.0f}・最大 {ll.groupby('i').size().max()}")
            for sd, nm in ((1, "買い"), (-1, "売り")):
                u = tt[tt.side == sd]
                if len(u) and len(u) < len(tt):
                    print(f"  - {nm}: {len(u)}件 平均 {u.net.mean() * 100:+.2f}%・中央 {u.net.median() * 100:+.2f}%")
            v = tt[tt.code.isin(SX)]
            print(f"  - 今の Saxo の一覧にある会社: {len(v)}件 平均 {v.net.mean() * 100:+.2f}%・中央 {v.net.median() * 100:+.2f}%")
            print("  - 年ごと (1回の平均、コスト込み): " + "、".join(f"{y} {g.net.mean() * 100:+.2f}% ({len(g)})" for y, g in tt.groupby(tt.de.dt.year)))
            if per == "検証":
                w = tt.nsmallest(5, "net")
                print("  - 一番悪い 5件: " + "、".join(f"{r.code} {r.de:%Y-%m-%d} {r.net * 100:+.1f}%" for r in w.itertuples()))
                mo = s.groupby(dates[s.index].to_period("M")).sum()
                print(f"  - 一番悪い月: {mo.idxmin()} {mo.min() * 100:+.1f}% (1日の値の合計)")
    passed = res["学習"] and res["検証"]
    print(f"\n**{name}: {'合格 (検証を通った)' if passed else '不合格'}**")
    return passed


if __name__ == "__main__":
    print("# 段階4 (research/stage4_saxo/PREREG.md) の日足 8候補: 学習・検証の集計\n")
    print(f"対象: 前の取引日の時点で時価総額の上位 {N_TOP} (普通株)。1306 との差。コスト 往復 0.2% + 保有コスト (買い 年4.0%、売り 年1.9%)。範囲は 40営業日のブロックのブートストラップ 1万回、99.375% (8候補の補正)")
    summary = {}
    for name, fn in (("25 本決算の驚き (上を買い・下を売り、20日)", t25), ("50 四半期の進み具合の差 (上を売り・下を買い、20日)", t50),
                     ("54 悪い知らせの −10% 以下の窓を寄りで売り、5日", lambda: gap_trades(54)),
                     ("55 開示なしの +10% 以上の窓を寄りで売り、その日の引けまで", lambda: gap_trades(55)),
                     ("56 開示なしで 1日 +10% 以上上げた翌朝に売り、20日", lambda: gap_trades(56)),
                     ("43 不祥事の次の始値で売り、20日", t43), ("41 分売の日の始値で買い、5日", t41), ("42 自社株買いの期間を買って持つ", t42)):
        summary[name] = run(name, fn())
    print("\n## まとめ\n")
    for k, v in summary.items():
        print(f"- {k}: {'合格' if v else '不合格'}")
