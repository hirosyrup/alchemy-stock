"""段階3 (research/stage3_supply/PREREG.md) の集計。宣言の決め方のとおりに、学習期間と検証期間を1回だけ集計する。
表は scripts/build_stage3_db.py で作った data/explore/stage3.duckdb (2024-10-31 まで)。
    uv run python scripts/run_stage3.py > data/results/stage3_supply.md
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import duckdb

DB = "data/explore/stage4_vault.duckdb" if os.environ.get("STAGE3_VAULT") == "1" else "data/explore/stage3.duckdb"  # 金庫は scripts/run_stage3_vault.py から
PERIODS = {"学習": ("2016-11-01", "2021-10-31"), "検証": ("2021-11-01", "2024-10-31")}
COST, BORROW = 0.002, 0.039
ALPHA = 0.0125 / 2  # 4候補の補正 → 98.75% 範囲
rng = np.random.default_rng(0)
c = duckdb.connect(DB, read_only=True)

# ---- 値段 ----
px = c.execute("select code, d, o, c, c_raw, Va from p").df()
px["d"] = pd.to_datetime(px.d)
dates = pd.DatetimeIndex(sorted(px.d.unique()))
O = px.pivot(index="d", columns="code", values="o").reindex(dates)
C = px.pivot(index="d", columns="code", values="c").reindex(dates)
CR = px.pivot(index="d", columns="code", values="c_raw").reindex(dates)
VA = px.pivot(index="d", columns="code", values="Va").reindex(dates)
ADV = VA.rolling(20, min_periods=20).mean().shift(1)  # その日より前の 20日
codes = O.columns
pos = {d: i for i, d in enumerate(dates)}
tpx = c.execute("select d, c from tpx").df()
tpx["d"] = pd.to_datetime(tpx.d)
tpx = tpx.set_index("d").c.reindex(dates)
m = c.execute("select d, code, S33Nm, MktNm, MrgnNm from m").df()
m["d"] = pd.to_datetime(m.d)
m = m.sort_values("d")
dis = c.execute("select code, date, title from d").df()
dis["date"] = pd.to_datetime(dis.date)


def next_td(day):
    """day より後の最初の取引日の位置"""
    i = dates.searchsorted(pd.Timestamp(day), side="right")
    return i if i < len(dates) else None


def info(df, col="D"):
    """出来事の日より前の最新の銘柄情報 (業種・市場・信用の区分)"""
    df = df.sort_values(col)
    return pd.merge_asof(df, m.rename(columns={"d": col}), on=col, by="code", direction="backward", allow_exact_matches=False)


def pair(ann, ends, days=30, strict=False):
    """発表と、その後 30暦日以内の最初の終わりの開示を対にする"""
    out = []
    ends = ends.sort_values("date")
    g = {k: v for k, v in ends.groupby("code")}
    for r in ann.itertuples():
        e = g.get(r.code)
        if e is None:
            continue
        lo = e.date > r.D if strict else e.date >= r.D
        e = e[lo & (e.date <= r.D + pd.Timedelta(days=days))]
        if len(e):
            out.append((r.code, r.D, e.date.iloc[0], e.kind.iloc[0] if "kind" in e else ""))
    return pd.DataFrame(out, columns=["code", "D", "Dend", "endkind"])


# ---- 出来事 → 取引 (code, 入る位置, 入る種類, 出る位置, 出る種類) ----
def t_s16():
    t = dis[dis.title.str.contains("立会外分売")]
    ann = t[~t.title.str.contains("実施|終了|中止|訂正")][["code", "date"]].drop_duplicates().rename(columns={"date": "D"})
    exe = t[t.title.str.contains("立会外分売.*実施")][["code", "date"]].drop_duplicates().assign(kind="実施")
    stop = t[t.title.str.contains("中止")][["code", "date"]].drop_duplicates().assign(kind="中止")
    pr = pair(ann, pd.concat([exe, stop]))
    # 実施を中止より優先 (同じ窓に両方あるときは実施)
    pe = pair(ann, exe)
    pr = pd.concat([pe, pr[~pr.set_index(["code", "D"]).index.isin(pe.set_index(["code", "D"]).index)]])
    rows = [dict(code=r.code, D=r.D, ie=next_td(r.D), te="o", ix=next_td(r.Dend), tx="o", note=r.endkind) for r in pr.itertuples()]
    return pd.DataFrame(rows)


def t_s17():
    t = dis
    ann = t[t.title.str.contains("新株式発行|売出し") & ~t.title.str.contains("投資口|訂正|決定|第三者割当|譲渡制限|ストック|新株予約権")]
    ann = ann[["code", "date"]].drop_duplicates().rename(columns={"date": "D"})
    pri = t[t.title.str.contains("発行価格.*決定|売出価格.*決定|処分価格.*決定")][["code", "date"]].drop_duplicates().assign(kind="決定")
    pr = pair(ann, pri, strict=True)
    rows = [dict(code=r.code, D=r.D, ie=next_td(r.D), te="o", ix=next_td(r.Dend), tx="o") for r in pr.itertuples()]
    df = pd.DataFrame(rows)
    df = info(df)
    return df[df.MrgnNm == "貸借"].drop(columns=["S33Nm", "MktNm", "MrgnNm"])


def t_s18():
    fins = c.execute("select code, disc, fm from fins").df()
    fins["disc"] = pd.to_datetime(fins.disc)
    yu = dis[dis.title.str.contains("株主優待") & ~dis.title.str.contains("訂正")]
    first = yu.groupby("code").date.min()
    stop = yu[yu.title.str.contains("廃止")].groupby("code").date.min()
    rows = []
    for ym, g in pd.Series(dates, index=dates).groupby(dates.to_period("M")):
        n = 4 if g.iloc[-1] < pd.Timestamp("2019-07-16") else 3
        if len(g) < n + 5:
            continue
        dcum = g.iloc[-n]
        tc = pos[dcum]
        if tc + 5 >= len(dates):
            continue
        f = fins[fins.disc < dcum]
        fm = f.groupby("code").fm.agg(lambda s: s.mode().iloc[0])
        for code in fm.index[fm == ym.month]:
            if code not in first.index or first[code] > dcum:
                continue
            if code in stop.index and stop[code] <= dcum:
                continue
            rows.append(dict(code=code, D=dcum, ie=tc + 1, te="c", ix=tc + 5, tx="c"))
    return pd.DataFrame(rows)


def t_s33():
    pc = CR.shift(1)
    r0 = CR / pc - 1
    cond = (VA >= 5 * ADV) & (ADV >= 1e8) & (pc >= 200) & (r0 >= 0.05)
    ev = cond.stack()
    ev = ev[ev].reset_index()
    ev.columns = ["D", "code", "_"]
    # その日と前の 3暦日に開示が無い
    dd = dis[["code", "date"]].drop_duplicates()
    x = ev.merge(dd, on="code", how="left")
    hit = x[(x.date >= x.D - pd.Timedelta(days=3)) & (x.date <= x.D)][["code", "D"]].drop_duplicates()
    ev = ev.merge(hit.assign(h=1), on=["code", "D"], how="left")
    ev = ev[ev.h.isna()]
    ev = info(ev)
    ev = ev[ev.S33Nm.notna() & (ev.S33Nm != "その他")].drop(columns=["S33Nm", "MktNm", "MrgnNm"])
    rows =[dict(code=r.code, D=r.D, ie=pos[r.D] + 1, te="o", ix=pos[r.D] + 5, tx="c") for r in ev.itertuples() if pos[r.D] + 5 < len(dates)]
    return pd.DataFrame(rows)


def clean(t):
    """値段の無いもの・金庫にかかるものを除き、同じ会社を持っている間の新しい出来事を除く。普通株 (PRO・ETF など) だけ"""
    t = t.dropna(subset=["ie", "ix"]).copy()
    t["ie"], t["ix"] = t.ie.astype(int), t.ix.astype(int)
    t = t[t.ix >= t.ie]
    t = t[t.code.isin(codes)]
    t = info(t)
    t = t[(t.MktNm != "TOKYO PRO MARKET") & t.S33Nm.notna() & (t.S33Nm != "その他")]
    keep, last = [], {}
    for r in t.sort_values(["ie", "code"]).itertuples():
        if r.code in last and r.ie <= last[r.code]:
            continue
        last[r.code] = r.ix
        keep.append(r.Index)
    t = t.loc[keep].sort_values("ie").reset_index(drop=True)
    t["de"], t["dx"] = dates[t.ie], dates[t.ix]
    t["adv"] = [ADV.iat[r.ie, codes.get_loc(r.code)] / 1e8 for r in t.itertuples()]
    return t


def price(code, i, kind, M=None):
    j = codes.get_loc(code)
    return (O if kind == "o" else C).iat[i, j]


B = "13060"


def legs(t):
    """1件ごとの、日ごとの空売り (1306 との差) の値動き。返す: {(取引番号, 日の位置): 値動き}、1件の合計の値動き"""
    jb = codes.get_loc(B)
    rows, tot = [], []
    for k, r in enumerate(t.itertuples()):
        j = codes.get_loc(r.code)
        segs = []  # (日, 株の始まり, 株の終わり, 1306 の始まり, 1306 の終わり)
        s0 = O.iat[r.ie, j] if r.te == "o" else C.iat[r.ie, j]
        b0 = O.iat[r.ie, jb] if r.te == "o" else C.iat[r.ie, jb]
        if not (np.isfinite(s0) and np.isfinite(b0)):
            tot.append(np.nan)
            continue
        ps, pb = s0, b0
        for i in range(r.ie, r.ix + 1):
            last = i == r.ix
            if i == r.ie and r.te == "c":
                segs.append((i, 0.0))
                continue
            if last and r.tx == "o":
                s1, b1 = O.iat[i, j], O.iat[i, jb]
            else:
                s1, b1 = C.iat[i, j], C.iat[i, jb]
            if not np.isfinite(s1):  # 値段が付かない日: 動きなし (次に付いた日にまとめて入る)
                if last:
                    s1 = C.iloc[i:, j].dropna().iloc[0] if C.iloc[i:, j].notna().any() else np.nan
                    if not np.isfinite(s1):
                        break
                else:
                    segs.append((i, 0.0))
                    continue
            segs.append((i, -(s1 / ps - 1) + (b1 / pb - 1)))
            ps, pb = s1, b1
        s_end = ps
        tot.append(-(s_end / s0 - 1) + (pb / b0 - 1))
        for i, v in segs:
            rows.append((k, i, v))
    return pd.DataFrame(rows, columns=["k", "i", "v"]), np.array(tot)


def book(t, lg, cost):
    """日ごとの成績: その日持っているもの全部を同じ金額ずつ。入った日にコストと借りる料金を引く"""
    days = (t.dx - t.de).dt.days.values
    fee = cost + days * BORROW / 365
    x = lg.copy()
    x["v"] = x.v - np.where(x.i.values == t.ie.values[x.k.values], fee[x.k.values], 0)
    s = x.groupby("i").v.mean()
    return s


def boot(s, n=10000, block=40):
    a = s.values
    L = len(a)
    nb = int(np.ceil(L / block))
    st = rng.integers(0, L - block + 1, size=(n, nb))
    idx = (st[:, :, None] + np.arange(block)).reshape(n, -1)[:, :L]
    mu = a[idx].mean(axis=1)
    return np.quantile(mu, [ALPHA, 1 - ALPHA])


def sr(a):
    return a.mean() / a.std(ddof=1) * np.sqrt(245) if a.std(ddof=1) > 0 else np.nan


def run(name, t):
    print(f"\n## {name}\n")
    lg, tot = legs(t)
    t = t.assign(tot=tot)
    days = (t.dx - t.de).dt.days
    t["net"] = t.tot - COST - days * BORROW / 365
    res = {}
    for per, (a, b) in PERIODS.items():
        q = (t.de >= a) & (t.de <= b) & t.tot.notna()
        tt = t[q].reset_index(drop=True)
        ll = lg[lg.k.isin(np.where(q)[0])].copy()
        ll["k"] = ll.k.map({o: n for n, o in enumerate(np.where(q)[0])})
        idx = dates[(dates >= a) & (dates <= b)]
        full = pd.Series(0.0, index=range(pos[idx[0]], max(pos[idx[-1]], int(tt.ix.max()) if len(tt) else 0) + 1))
        out = {}
        for cst in (0, COST, 0.004):
            s = book(tt, ll, cst).reindex(full.index).fillna(0.0)
            out[cst] = s
        s = out[COST]
        lo, hi = boot(s)
        tp = tpx.iloc[full.index].pct_change().dropna()
        yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
        res[per] = dict(n=len(tt), s=s, lo=lo, hi=hi)
        print(f"### {per} ({a} 〜 {b})\n")
        print(f"- 件数 {len(tt)} (年 {len(tt) / yrs:.0f}件)、持つ暦日 中央 {days[q].median():.0f}日")
        print(f"- 1日の平均 (コスト 0.2% + 借りる料金): **{s.mean() * 1e4:+.2f} bp** (98.75% 範囲 {lo * 1e4:+.2f} 〜 {hi * 1e4:+.2f})、"
              f"年換算シャープレシオ **{sr(s.values):+.2f}** (同じ期間の TOPIX 買い持ち {sr(tp.values):+.2f})")
        print(f"- コスト別の 1日の平均: 0% {out[0].mean() * 1e4:+.2f} bp / 0.2% {out[COST].mean() * 1e4:+.2f} bp / 0.4% {out[0.004].mean() * 1e4:+.2f} bp (どれも借りる料金込み)")
        n = t.net[q]
        print(f"- 1回あたり (コスト込み): 平均 {n.mean() * 100:+.2f}%・中央 {n.median() * 100:+.2f}%・勝ち {(n > 0).mean():.0%}。コスト前 平均 {t.tot[q].mean() * 100:+.2f}%")
        hold = days[q].mean()
        be = (t.tot[q].mean() - COST - BORROW / 365 * hold) / hold if hold > 0 else np.nan
        print(f"- 逆日歩の損益の分かれ目: 1日 {be * 100:.2f}% (1回の平均が 0 になる、持つ暦日の平均 {hold:.1f}日)")
        if per == "検証":
            ok = lo > 0 and sr(s.values) > sr(tp.values)
            print(f"- **合否 (検証): {'合格' if ok else '不合格'}** (範囲の下限 > 0: {lo > 0}、SR が TOPIX を上回る: {sr(s.values) > sr(tp.values)})")
        else:
            print(f"- 学習期間の条件 (1日の平均 > 0): {s.mean() > 0}")
        yy = pd.Series(s.values, index=dates[np.minimum(s.index, len(dates) - 1)])
        tq = t[q]
        print("\n| 年 | 件数 | 1日の平均 (bp) | 1回の平均 (コスト込み) | 勝ち |\n|---|---|---|---|---|")
        for y in sorted(tq.de.dt.year.unique()):
            qq = tq.de.dt.year == y
            print(f"| {y} | {qq.sum()} | {yy[yy.index.year == y].mean() * 1e4:+.2f} | {tq.net[qq].mean() * 100:+.2f}% | {(tq.net[qq] > 0).mean():.0%} |")
        print("\n| 組 | 件数 | 1回の平均 (コスト込み) | 中央 | 勝ち |\n|---|---|---|---|---|")
        for lab, qq in (("貸借", tq.MrgnNm == "貸借"), ("貸借以外", tq.MrgnNm != "貸借"), ("売買代金 1億円未満", tq.adv < 1), ("1〜10億円", (tq.adv >= 1) & (tq.adv < 10)), ("10億円以上", tq.adv >= 10)):
            if qq.sum():
                print(f"| {lab} | {qq.sum()} | {tq.net[qq].mean() * 100:+.2f}% | {tq.net[qq].median() * 100:+.2f}% | {(tq.net[qq] > 0).mean():.0%} |")
        w = tq.nsmallest(5, "net")
        print("\n一番悪い 5件: " + "、".join(f"{r.code} {r.de:%Y-%m-%d} {r.net * 100:+.1f}%" for r in w.itertuples()))
        mo = yy.groupby(yy.index.to_period("M")).sum()
        print(f"一番悪い月: {mo.idxmin()} {mo.min() * 100:+.2f}% (1日の値動きの合計)、勝った月 {(mo > 0).mean():.0%}")
        # ランダム対照 (1回あたりの平均、コスト前): 同じ日・同じ売買代金の 1/5 の帯の銘柄を同じ持ち方で
        rnd = random_ctrl(tq)
        if rnd is not None:
            print(f"ランダム対照 (1回の平均、コスト前、1,000回): 95% 点 {np.quantile(rnd, .95) * 100:+.2f}%、候補 {tq.tot.mean() * 100:+.2f}% → "
                  f"{'上回る' if tq.tot.mean() > np.quantile(rnd, .95) else '上回らない'} (ランダムの平均 {rnd.mean() * 100:+.2f}%)")
    return t, res


def random_ctrl(tq, n=1000):
    jb = codes.get_loc(B)
    advm = ADV.values
    picks = []
    for r in tq.itertuples():
        a = advm[r.ie]
        ok = np.isfinite(a)
        if not ok.sum():
            continue
        qs = np.nanquantile(a[ok], [.2, .4, .6, .8])
        band = np.searchsorted(qs, advm[r.ie, codes.get_loc(r.code)]) if np.isfinite(advm[r.ie, codes.get_loc(r.code)]) else None
        if band is None:
            continue
        cb = np.searchsorted(qs, np.where(ok, a, -1))
        s0 = (O if r.te == "o" else C).values[r.ie]
        s1 = (O if r.tx == "o" else C).values[r.ix]
        pool = np.where(ok & (cb == band) & np.isfinite(s0) & np.isfinite(s1) & (s0 > 0))[0]
        pool = pool[pool != jb]
        if not len(pool):
            continue
        ch = rng.choice(pool, size=n)
        b = (O if r.tx == "o" else C).values[r.ix, jb] / (O if r.te == "o" else C).values[r.ie, jb] - 1
        picks.append(-(s1[ch] / s0[ch] - 1) + b)
    if not picks:
        return None
    return np.vstack(picks).mean(axis=0)


if __name__ == "__main__":
    print("# 段階3 の結果 (research/stage3_supply/PREREG.md)\n")
    print("値動きは 1306 との差 (空売り)。1日の平均は「その日持っているもの全部を同じ金額ずつ」。合否はコスト 0.2% + 借りる料金 年 3.9%。")
    allt = []
    for name, fn in (("S16 立会外分売", t_s16), ("S17 公募増資・売出し (貸借銘柄)", t_s17), ("S18 優待の権利落ち", t_s18), ("S33 理由のない出来高の急増", t_s33)):
        t = clean(fn())
        t, res = run(name, t)
        allt.append(t.assign(cand=name))
    # 4つを重ねた場合 (報告だけ)
    print("\n## 参考: 4つを重ねた場合 (同じ金額ずつ、合否には使わない)\n")
    T = pd.concat(allt, ignore_index=True)
    T = T[T.tot.notna()].reset_index(drop=True)
    lg, _ = legs(T)
    for per, (a, b) in PERIODS.items():
        q = (T.de >= a) & (T.de <= b)
        tt = T[q].reset_index(drop=True)
        ll = lg[lg.k.isin(np.where(q)[0])].copy()
        ll["k"] = ll.k.map({o: n for n, o in enumerate(np.where(q)[0])})
        idx = dates[(dates >= a) & (dates <= b)]
        full = range(pos[idx[0]], max(pos[idx[-1]], int(tt.ix.max())) + 1)
        s = book(tt, ll, COST).reindex(full).fillna(0.0)
        lo, hi = boot(s)
        print(f"- {per}: {len(tt)}件、1日の平均 {s.mean() * 1e4:+.2f} bp (範囲 {lo * 1e4:+.2f} 〜 {hi * 1e4:+.2f})、年換算シャープレシオ {sr(s.values):+.2f}")
