"""段階1: 数字だけで、開示の翌朝に乗る (research/stage1_numbers/PREREG.md) の集計。

J-Quants の財務情報から予想の修正・来期の予想の伸び・増配を測り、開示の後の最初の寄り付きで入って
その日の引けで手放した成績を、学習・検証の期間ごとに出す。金庫 (2024-11 以降) は計算しない。
結果は data/results/ に置く (株価から作った数字なので Git には入れない)。

    uv run python scripts/stage1_numbers.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.liquidity import MIN_ADV, create_adv  # noqa: E402

FINS = "data/jquants/fins/summary/**/*.csv.gz"
BARS = "data/jquants/equities/bars/daily/**/*.csv.gz"
MASTER = "data/jquants/equities/master/**/*.csv.gz"
TOPIX = "data/jquants/indices/bars/daily/topix/**/*.csv.gz"
OUT = Path("data/results/stage1_numbers.md")

PERIODS = {"学習": (date(2016, 11, 1), date(2021, 10, 31)), "検証": (date(2021, 11, 1), date(2024, 10, 31))}
LAST_DAY = date(2024, 10, 31)  # 金庫は開けない
COSTS = (0.0, 0.002, 0.004)
JUDGE_COST = 0.002
LENDING_FEE = 0.011 / 365
CANDIDATES = {"U10": 1, "D10": -1, "DIV": 1, "NXT10": 1}  # 買い 1 / 売り -1
RANGE = 1 - 0.05 / len(CANDIDATES)  # 98.75%
BLOCK = 20
N_BOOT = 10_000
N_CONTROL = 1_000
ANNUAL = 245


def build(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(f"""
        create table f as
        select DiscDate::date d, case when length(DiscTime) = 5 then DiscTime || ':00' else DiscTime end t,
               Code code, DiscNo no, regexp_extract(DocType, '^[^_]+') kind,
               CurFYEn::date fy, NxtFYEn::date nfy,
               OP::double op, OdP::double odp, NP::double np,
               FOP::double fop, FOdP::double fodp, FNP::double fnp, FDivAnn::double fdiv,
               NxFOP::double nxop, NxFOdP::double nxodp, NxFNp::double nxnp, NxFDivAnn::double nxdiv
        from read_csv('{FINS}', union_by_name=true, all_varchar=true)
        where DocType not like '%REIT%' and DocType not like 'OtherPeriod%'
    """)
    # 予想の流れ: 決算短信 (通期以外)・修正は今期の予想、通期の決算短信は来期の予想を、その会計年度の予想として並べる
    con.sql("""
        create table fc as
        with s as (
            select code, fy, d, t, no, true is_new, fop vop, fodp vodp, fnp vnp, fdiv vdiv from f where kind <> 'FYFinancialStatements'
            union all
            select code, nfy, d, t, no, false, nxop, nxodp, nxnp, nxdiv from f where kind = 'FYFinancialStatements'
        )
        select *,
            last_value(vop ignore nulls) over w p_op,
            last_value(vodp ignore nulls) over w p_odp,
            last_value(vnp ignore nulls) over w p_np,
            last_value(vdiv ignore nulls) over w p_div,
            last_value(case when vdiv is not null then d end ignore nulls) over w p_div_d
        from s where fy is not null
        window w as (partition by code, fy order by d, t, no rows between unbounded preceding and 1 preceding)
    """)
    con.sql(f"""
        create table adjcum as
        select Code code, Date::date date, sum(ln(coalesce(AdjFactor::double, 1))) over (partition by Code order by Date) s
        from read_csv('{BARS}', union_by_name=true, all_varchar=true)
    """)
    # 分割・併合の前後は、会社が予想をどちらの1株で出したかが揃わない (宣言の追記 1)
    con.sql("create table splits as select code, date from adjcum qualify s <> lag(s, 1, 0) over (partition by code order by date)")
    con.sql("""
        create table sig as
        with rev as (
            select code, d, t,
                case when vop is not null and p_op is not null then vop / p_op - 1
                     when vodp is not null and p_odp is not null then vodp / p_odp - 1
                     when vnp is not null and p_np is not null then vnp / p_np - 1 end raw,
                case when vop is not null and p_op is not null then p_op
                     when vodp is not null and p_odp is not null then p_odp
                     when vnp is not null and p_np is not null then p_np end prior
            from fc where is_new
        ),
        dv as (
            select fc.code, fc.d, fc.t, fc.vdiv, fc.p_div * exp(coalesce(a1.s, 0) - coalesce(a0.s, 0)) p_adj
            from fc
            asof left join adjcum a1 on fc.code = a1.code and fc.d >= a1.date
            asof left join adjcum a0 on fc.code = a0.code and fc.p_div_d >= a0.date
            where fc.is_new and fc.vdiv is not null and fc.p_div is not null
              and not exists (select 1 from splits sp where sp.code = fc.code and sp.date between fc.d - 365 and fc.d + 365)
        ),
        nxt as (
            select code, d, t,
                case when nxop is not null and op is not null then (op, nxop / op - 1)
                     when nxodp is not null and odp is not null then (odp, nxodp / odp - 1)
                     when nxnp is not null and np is not null then (np, nxnp / np - 1) end g
            from f where kind = 'FYFinancialStatements'
        )
        select 'U10' cand, code, d, t from rev where prior > 0 and raw >= 0.10
        union all select 'D10', code, d, t from rev where prior > 0 and raw <= -0.10
        union all select 'DIV', code, d, t from dv where vdiv > p_adj * (1 + 1e-6) + 1e-9
        union all select 'NXT10', code, d, t from nxt where g[1] > 0 and g[2] >= 0.10
        union all select 'ALL', code, d, t from f
    """)
    con.sql(f"""
        create table cal as select Date::date date, O::double o, C::double c from read_csv('{TOPIX}', union_by_name=true)
    """)
    create_adv(con, BARS)
    con.sql(f"""
        create table ev as
        with a as (
            select cand, code, d, case when t < '09:00:00' then d else d + 1 end ed
            from sig
            where t < '09:00:00' or t >= case when d <= date '2024-11-01' then '15:00:00' else '15:30:00' end
        ),
        e as (
            select a.cand, a.code, min(a.d) d, c.date entry
            from a asof join cal c on c.date >= a.ed
            group by all
        )
        select e.*, adv.adv from e asof left join adv on e.code = adv.code and e.d > adv.date
        where e.entry <= date '{LAST_DAY}'
    """)
    con.sql(f"""
        create table tr as
        select ev.cand, ev.code, ev.entry, ev.adv, m.Mkt mkt, m.ProdCat prod, m.Mrgn mrgn,
               b.O::double o, b.H::double h, b.L::double l, b.C::double c, b.UL ul, b.LL ll, cal.o to_, cal.c tc
        from ev
        join cal on cal.date = ev.entry
        left join read_csv('{MASTER}', union_by_name=true, all_varchar=true) m on m.Code = ev.code and m.Date::date = ev.entry
        left join read_csv('{BARS}', union_by_name=true, all_varchar=true) b on b.Code = ev.code and b.Date::date = ev.entry
    """)


def trades(con, cand: str, side: int) -> list[tuple[date, float]]:
    """(入った日, コスト前の成績) の一覧。対象のふるい分けと入れない日を当てたあと。"""
    blocked = "ul = '1' and o = h" if side > 0 else "ll = '1' and o = l"
    short = "and mrgn = '2'" if side < 0 else ""
    return con.execute(f"""
        select entry, {side} * ((c / o - 1) - (tc / to_ - 1)) - {LENDING_FEE if side < 0 else 0}
        from tr
        where cand = ? and prod = '011' and mkt not in ('0105', '0109') and adv >= {MIN_ADV}
          and o > 0 and c > 0 and not ({blocked}) {short}
        order by entry, code
    """, [cand]).fetchall()


def daily(days: list[date], rows: list[tuple[date, float]], cost: float) -> np.ndarray:
    pos = {d: i for i, d in enumerate(days)}
    s, n = np.zeros(len(days)), np.zeros(len(days))
    for d, r in rows:
        if d in pos:
            s[pos[d]] += r - cost
            n[pos[d]] += 1
    return np.divide(s, n, out=np.zeros_like(s), where=n > 0)


def boot_range(x: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    n = len(x)
    k = -(-n // BLOCK)
    starts = rng.integers(0, n - BLOCK + 1, size=(N_BOOT, k))
    idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(N_BOOT, -1)[:, :n]
    means = x[idx].mean(axis=1)
    a = (1 - RANGE) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def sharpe(x: np.ndarray) -> float:
    return float(x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL)) if x.std() > 0 else 0.0


def control(days, pool, n_trades, rng) -> np.ndarray:
    """対象全体から同じ回数をランダムに選んだときの、1日の平均 (コスト 0.2% 込み) の分布。"""
    pos = {d: i for i, d in enumerate(days)}
    pool = [(pos[d], r - JUDGE_COST) for d, r in pool if d in pos]
    di = np.array([p for p, _ in pool])
    rr = np.array([r for _, r in pool])
    out = np.empty(N_CONTROL)
    for i in range(N_CONTROL):
        pick = rng.choice(len(pool), size=min(n_trades, len(pool)), replace=False)
        s = np.bincount(di[pick], weights=rr[pick], minlength=len(days))
        n = np.bincount(di[pick], minlength=len(days))
        out[i] = np.divide(s, n, out=np.zeros_like(s), where=n > 0).mean()
    return out


def main() -> int:
    con = duckdb.connect()
    build(con)
    rng = np.random.default_rng(0)
    cal = con.execute("select date, c from cal order by date").fetchall()
    lines = [f"# 段階1の集計 (宣言: research/stage1_numbers/PREREG.md)", "",
             f"範囲は {RANGE:.2%}、ブロック {BLOCK} 営業日、{N_BOOT} 回。合否はコスト {JUDGE_COST:.1%}。数字の単位は 1日の平均が bp (0.01%)。", ""]
    lines += ["## 件数 (ふるい分けの前 → 後)", "", "| 候補 | 時間外の開示 (会社×入る日) | 売買できたもの |", "|---|---|---|"]
    for cand, side in CANDIDATES.items():
        n_ev = con.execute("select count(*) from tr where cand = ?", [cand]).fetchone()[0]
        lines.append(f"| {cand} | {n_ev} | {len(trades(con, cand, side))} |")
    lines.append("")

    for name, (start, end) in PERIODS.items():
        days = [d for d, _ in cal if start <= d <= end]
        closes = np.array([c for d, c in cal if start <= d <= end])
        prev = [c for d, c in cal if d < start][-1:]
        topix = np.diff(np.concatenate([prev, closes])) / np.concatenate([prev, closes])[:-1]
        tsr = sharpe(topix)
        lines += [f"## {name} ({start} 〜 {end}、{len(days)} 営業日)", "",
                  f"TOPIX 買い持ちの年換算シャープレシオ: {tsr:.2f}", "",
                  "| 候補 | コスト | 1日の平均 | 範囲 | 年換算SR | 回数/年 | 1回の平均 | 勝ち | 合否の条件 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        detail = []
        for cand, side in CANDIDATES.items():
            rows = [(d, r) for d, r in trades(con, cand, side) if start <= d <= end]
            for cost in COSTS:
                x = daily(days, rows, cost)
                lo, hi = boot_range(x, rng)
                sr = sharpe(x)
                per = np.array([r - cost for _, r in rows])
                if cost == JUDGE_COST:
                    ok = (x.mean() > 0) if name == "学習" else (lo > 0 and sr > tsr)
                    verdict = "満たす" if ok else "満たさない"
                else:
                    verdict = ""
                lines.append(
                    f"| {cand} | {cost:.1%} | {x.mean() * 1e4:.2f} | {lo * 1e4:.2f} 〜 {hi * 1e4:.2f} | {sr:.2f} | "
                    f"{len(rows) / len(days) * ANNUAL:.0f} | {per.mean() * 1e4 if len(per) else 0:.1f} | "
                    f"{(per > 0).mean() if len(per) else 0:.0%} | {verdict} |")
            x = daily(days, rows, JUDGE_COST)
            by_year = {}
            by_month = {}
            for d, v in zip(days, x):
                by_year[d.year] = by_year.get(d.year, 0) + v
                by_month[(d.year, d.month)] = by_month.get((d.year, d.month), 0) + v
            total = x.sum()
            top2 = sorted(by_month.values(), reverse=True)[:2]
            ctrl = control(days, [(d, r) for d, r in trades(con, "ALL", side) if start <= d <= end], len(rows), rng)
            share = f"。上位2か月の合計が全体に占める割合 {sum(top2) / total:.0%}" if total > 0 else ""
            detail.append(f"- **{cand}**: 年ごとの合計 (bp、コスト 0.2%) "
                          + " / ".join(f"{y}: {v * 1e4:.0f}" for y, v in sorted(by_year.items())) + share)
            detail.append(
                f"  - ランダム対照 (同じ向き・同じ回数、{N_CONTROL}回): 1日の平均 中央 {np.median(ctrl) * 1e4:.2f} bp "
                f"(5%〜95%: {np.quantile(ctrl, 0.05) * 1e4:.2f} 〜 {np.quantile(ctrl, 0.95) * 1e4:.2f})、"
                f"候補を上回った割合 {(ctrl >= x.mean()).mean():.1%}")
        lines += [""] + detail + [""]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
