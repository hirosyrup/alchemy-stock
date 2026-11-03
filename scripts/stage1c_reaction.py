"""段階1c: 業績予想の修正で1日目に大きく上げたものを、2日目の始値で買って20日持つ (research/stage1c_reaction/PREREG.md) の集計。

対象は取引時間外に出た業績予想の修正のお知らせ (向き・幅を問わない、同じ会社で同じ朝に入るものは1件)。
1日目の反応 (前日の終値 → 1日目の終値 − TOPIX) が +5% 以上 (R5) / +10% 以上 (R10) のものを、2日目の始値で買い、
2日目を1日目と数えて20営業日目の引けで手放す。成績の付け方は段階1b と同じ。金庫 (2024-11 以降) は計算しない。

    uv run python scripts/stage1c_reaction.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from alchemy_stock.liquidity import MIN_ADV  # noqa: E402
from stage1_numbers import (  # noqa: E402
    ANNUAL, BARS, COSTS, JUDGE_COST, LAST_DAY, N_CONTROL, PERIODS, boot_range, build, sharpe,
)
from stage1b_hold import daily, prepare, with_costs  # noqa: E402

CANDIDATES = {"R5": 0.05, "R10": 0.10}
HOLD = 20
BLOCK = 40
RANGE = 1 - 0.05 / len(CANDIDATES)  # 97.5%
SUB = {"学習の前半 (2016-11〜2019、探索で見た)": (date(2016, 11, 1), date(2019, 12, 31)),
       "学習の後半 (2020〜2021-10、見ていない)": (date(2020, 1, 1), date(2021, 10, 31))}
OUT = Path("data/results/stage1c_reaction.md")


def events(con: duckdb.DuckDBPyConnection) -> None:
    """rv: 1日目に売買できた修正のお知らせと1日目の反応。en: そのうち2日目の始値で買えたもの。"""
    con.sql(f"""
        create table rv as
        with rev as (
            select f.code, c.date entry
            from f asof join cal c on c.date >= case when f.t < '09:00:00' then f.d else f.d + 1 end
            where f.kind = 'EarnForecastRevision'
              and (f.t < '09:00:00' or f.t >= case when f.d <= date '2024-11-01' then '15:00:00' else '15:30:00' end)
        )
        select distinct e.code, e.entry, tr.adv, ci.i, b.c / (b.pc * b.af) - ci.c / ci.pc react
        from rev e
        join tr on tr.cand = 'ALL' and tr.code = e.code and tr.entry = e.entry
        join ci on ci.date = e.entry
        join bx b on b.code = e.code and b.date = e.entry
        where tr.prod = '011' and tr.mkt not in ('0105', '0109') and tr.adv >= {MIN_ADV} and tr.o > 0 and tr.c > 0
          and b.pc is not null
    """)
    con.sql(f"""
        create table en as
        select rv.*, d2.date entry2, d2.i i2, b.o o2,
               coalesce(u.UL = '1' and u.H::double = b.o, false) blocked, b.date is null or b.o <= 0 no_price
        from rv join ci d2 on d2.i = rv.i + 1
        left join bx b on b.code = rv.code and b.date = d2.date
        left join (select Code code, Date::date date, H, UL from read_csv('{BARS}', union_by_name=true, all_varchar=true)) u
          on u.code = rv.code and u.date = d2.date
    """)


def paths(con) -> tuple:
    """2日目の始値で買えたもの全部の、毎日の値動き (コスト前、TOPIX を引く)。値段が付かなくなった日の前日で手放す。"""
    meta = con.execute("""
        select row_number() over (order by entry2, code) - 1 id, code, entry2, react, adv from en
        where not blocked and not no_price order by id
    """).fetchall()
    rows = con.execute(f"""
        with t as (select row_number() over (order by entry2, code) - 1 id, code, i2 from en where not blocked and not no_price),
        p as (
            select t.id, t.code, k.k, d.date, d.o to_, d.c tc, d.pc tpc
            from t cross join (select unnest(range(1, {HOLD + 1})) k) k
            join ci d on d.i = t.i2 + k.k - 1
        ),
        q as (
            select p.*, b.o, b.c, b.pc, b.af, max((b.date is null)::int) over (partition by p.id order by p.k) gone
            from p left join bx b on b.code = p.code and b.date = p.date
        )
        select id, k, date, case when k = 1 then (c / o - 1) - (tc / to_ - 1) else (c / (pc * af) - 1) - (tc / tpc - 1) end
        from q where gone = 0 order by id, k
    """).fetchall()
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    k = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows], dtype="datetime64[D]")
    r = np.array([r[3] for r in rows])
    entries = [m[2] for m in meta]
    react = np.array([m[3] for m in meta])
    adv = np.array([m[4] for m in meta])
    return ids, entries, k, days, r, react, adv


def main() -> int:
    con = duckdb.connect()
    build(con)
    prepare(con)
    events(con)
    ids, entries, k, days, r, react, adv = paths(con)
    ent = np.array(entries, dtype="datetime64[D]")
    cal = con.execute("select date, c from ci order by date").fetchall()
    all_days = np.array([d for d, _ in cal], dtype="datetime64[D]")
    v_cost = {c: with_costs(ids, entries, k, days, r, 1, c) for c in COSTS}
    tot = np.bincount(ids, weights=v_cost[JUDGE_COST], minlength=len(entries))
    adv_u = np.sort(con.execute("""select adv from rv where entry between '2016-11-01' and '2019-12-31'""").fetchnumpy()["adv"])
    cuts = adv_u[len(adv_u) // 3], adv_u[2 * len(adv_u) // 3]
    size = np.where(adv >= cuts[1], "大", np.where(adv >= cuts[0], "中", "小"))
    rng = np.random.default_rng(0)

    lines = ["# 段階1c の集計 (宣言: research/stage1c_reaction/PREREG.md)", "",
             f"範囲は {RANGE:.1%}、ブロック {BLOCK} 営業日、1万回。合否はコスト {JUDGE_COST:.1%}。1日の平均は bp (0.01%)、1回の成績は %。"
             f"{LAST_DAY} より後の値段は使っていない。", ""]

    def period_days(start, end):
        last = min(len(all_days) - 1, int(np.searchsorted(all_days, np.datetime64(end), side="right")) - 1 + HOLD - 1)
        cal_days = all_days[int(np.searchsorted(all_days, np.datetime64(start))):last + 1]
        n_period = int(((cal_days >= np.datetime64(start)) & (cal_days <= np.datetime64(end))).sum())
        return cal_days, n_period

    def control(cal_days, in_p, n_tr):
        """対象全体 (反応を問わない) から同じ回数を選んで同じく持った場合の、1日の平均 (コスト 0.2%) の分布。"""
        cands = np.flatnonzero(in_p)
        out = np.empty(N_CONTROL)
        for i in range(N_CONTROL):
            sel = rng.choice(cands, size=min(n_tr, len(cands)), replace=False)
            keep = np.zeros(len(entries), dtype=bool)
            keep[sel] = True
            out[i] = daily(cal_days, days, v_cost[JUDGE_COST], keep[ids]).mean()
        return out

    n_ev = dict(con.execute("""
        select case when entry <= date '2021-10-31' then '学習' else '検証' end, count(*) from en group by 1""").fetchall())
    n_blocked = dict(con.execute("""
        select case when entry <= date '2021-10-31' then '学習' else '検証' end, count(*) from en
        where react >= 0.05 and (blocked or no_price) group by 1""").fetchall())

    for name, (start, end) in PERIODS.items():
        closes = np.array([c for d, c in cal if start <= d <= end])
        prev = [c for d, c in cal if d < start][-1:]
        series = np.concatenate([prev, closes])
        tsr = sharpe(np.diff(series) / series[:-1])
        cal_days, n_period = period_days(start, end)
        in_p = (ent >= np.datetime64(start)) & (ent <= np.datetime64(end))
        lines += [f"## {name} ({start} 〜 {end})", "",
                  f"TOPIX 買い持ちの年換算シャープレシオ: {tsr:.2f}。対象 (1日目に売買できた修正のお知らせ) {n_ev.get(name, 0)}件、"
                  f"反応 +5% 以上で2日目の始値に入れなかったもの (ストップ高・値段なし) {n_blocked.get(name, 0)}件", "",
                  "| 候補 | コスト | 1日の平均 | 範囲 | 年換算SR | 回数/年 | 1回の平均 | 勝ち | 合否の条件 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        detail = []
        groups = {**{c: react >= th for c, th in CANDIDATES.items()}, "全部 (参考)": np.ones(len(entries), dtype=bool)}
        for cand, mask in groups.items():
            sel = in_p & mask
            n_tr = int(sel.sum())
            for cost in COSTS:
                x = daily(cal_days, days, v_cost[cost], sel[ids])
                lo, hi = boot_range(x, rng, BLOCK, RANGE)
                sr = sharpe(x)
                per = np.bincount(ids, weights=v_cost[cost], minlength=len(entries))[sel]
                verdict = ""
                if cost == JUDGE_COST and cand in CANDIDATES:
                    ok = (x.mean() > 0) if name == "学習" else (lo > 0 and sr > tsr)
                    verdict = "満たす" if ok else "満たさない"
                lines.append(f"| {cand} | {cost:.1%} | {x.mean() * 1e4:.2f} | {lo * 1e4:.2f} 〜 {hi * 1e4:.2f} | {sr:.2f} | "
                             f"{n_tr / n_period * ANNUAL:.0f} | {per.mean() * 100:.2f} | {(per > 0).mean():.0%} | {verdict} |")
            if cand not in CANDIDATES:
                continue
            x = daily(cal_days, days, v_cost[JUDGE_COST], sel[ids])
            by_year = {}
            for d, val in zip(cal_days.astype(object), x):
                by_year[d.year] = by_year.get(d.year, 0) + val
            detail.append(f"- **{cand}**: 年ごとの合計 (bp、コスト 0.2%) " + " / ".join(f"{y}: {val * 1e4:.0f}" for y, val in sorted(by_year.items())))
            detail.append("  - 会社の大きさ別 (1回の平均、コスト 0.2%): " + " / ".join(
                f"{s} {tot[sel & (size == s)].mean() * 100:+.2f}% ({int((sel & (size == s)).sum())}件)" for s in ("大", "中", "小")
                if (sel & (size == s)).any()))
            if name == "学習":
                for sub, (s0, s1) in SUB.items():
                    sd, _ = period_days(s0, s1)
                    ss = sel & (ent >= np.datetime64(s0)) & (ent <= np.datetime64(s1))
                    xs = daily(sd, days, v_cost[JUDGE_COST], ss[ids])
                    detail.append(f"  - {sub}: 1日の平均 {xs.mean() * 1e4:.2f} bp、年換算SR {sharpe(xs):.2f}、"
                                  f"1回の平均 {tot[ss].mean() * 100:+.2f}% ({int(ss.sum())}件)")
            ctrl = control(cal_days, in_p, n_tr)
            detail.append(f"  - ランダム対照 (対象全体から同じ回数、{N_CONTROL}回): 1日の平均 中央 {np.median(ctrl) * 1e4:.2f} bp "
                          f"(5%〜95%: {np.quantile(ctrl, 0.05) * 1e4:.2f} 〜 {np.quantile(ctrl, 0.95) * 1e4:.2f})、"
                          f"候補を上回った割合 {(ctrl >= x.mean()).mean():.1%}")
        lines += [""] + detail + [""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
