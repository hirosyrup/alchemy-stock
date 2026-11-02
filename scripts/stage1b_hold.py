"""段階1b: 数字だけで、開示の翌朝に乗って数日〜数週間持つ (research/stage1b_hold/PREREG.md) の集計。

対象・物差し・入り方は段階1 (scripts/stage1_numbers.py) と同じで、手放すのを 5・20 営業日目の引けにする。
持つ期間が重なるので、1日ごとに「その日持っているもの全部を同じ金額ずつ」の値動きをその日の成績とする。
2024-10-31 より後の値段は使わない (金庫は開けない)。結果は data/results/ に置く。

    uv run python scripts/stage1b_hold.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage1_numbers import (  # noqa: E402
    ANNUAL, BARS, CANDIDATES, COSTS, JUDGE_COST, LAST_DAY, LENDING_FEE, N_CONTROL, PERIODS, boot_range, build,
    eligible, sharpe,
)

HOLDS = {5: 20, 20: 40}  # 持つ営業日数: ブロックの長さ
RANGE = 1 - 0.05 / (len(CANDIDATES) * len(HOLDS))  # 99.375%
OUT = Path("data/results/stage1b_hold.md")


def prepare(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(f"""
        create table ci as
        select date, row_number() over (order by date) i, o, c, lag(c) over (order by date) pc
        from cal where date <= date '{LAST_DAY}'
    """)
    # 値段の付いた日だけ並べる。前日の終値は、その前に値段が付いた日の終値
    con.sql(f"""
        create table bx as
        select code, date, o, c, af, lag(c) over (partition by code order by date) pc
        from (select Code code, Date::date date, O::double o, C::double c, coalesce(AdjFactor::double, 1) af
              from read_csv('{BARS}', union_by_name=true, all_varchar=true)
              where C::double > 0 and Date::date <= date '{LAST_DAY}')
    """)


def paths(con, cand: str, side: int, hold: int) -> tuple[np.ndarray, list[date], np.ndarray, np.ndarray, np.ndarray]:
    """売買ごとの毎日の値動き (コスト前、向きを掛けた後)。値段が付かなくなった日の前日で手放す。

    返り値: 売買の番号, 入った日 (番号順), 何日目か, その日, 値動き。
    """
    rows = con.execute(f"""
        with t as (select row_number() over (order by entry, code) - 1 id, code, entry from tr where {eligible(side)}),
        p as (
            select t.id, t.code, t.entry, k.k, d.date, d.o to_, d.c tc, d.pc tpc
            from t join ci e on e.date = t.entry
            cross join (select unnest(range(1, {hold + 1})) k) k
            join ci d on d.i = e.i + k.k - 1
        ),
        q as (
            select p.*, b.o, b.c, b.pc, b.af, max((b.date is null)::int) over (partition by p.id order by p.k) gone
            from p left join bx b on b.code = p.code and b.date = p.date
        )
        select id, entry, k, date,
            {side} * (case when k = 1 then (c / o - 1) - (tc / to_ - 1) else (c / (pc * af) - 1) - (tc / tpc - 1) end)
        from q where gone = 0
        order by id, k
    """, [cand]).fetchall()
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    entries = {}
    for r in rows:
        entries.setdefault(r[0], r[1])
    return (ids, [entries[i] for i in sorted(entries)], np.array([r[2] for r in rows]),
            np.array([r[3] for r in rows], dtype="datetime64[D]"), np.array([r[4] for r in rows]))


def with_costs(ids, entries, k, days, r, side, cost) -> np.ndarray:
    """1日目に、往復のコストと (売りなら) 持っていた暦の日数分の貸株料を引く。"""
    out = r.copy()
    first = k == 1
    out[first] -= cost
    if side < 0:
        last = np.zeros(len(entries), dtype="datetime64[D]")
        np.maximum.at(last, ids, days)
        held = np.maximum((last - np.array(entries, dtype="datetime64[D]")).astype(int), 1)
        out[first] -= LENDING_FEE * held[ids[first]]
    return out


def daily(cal_days: np.ndarray, days: np.ndarray, v: np.ndarray, keep: np.ndarray) -> np.ndarray:
    pos = np.searchsorted(cal_days, days[keep])
    s = np.bincount(pos, weights=v[keep], minlength=len(cal_days))
    n = np.bincount(pos, minlength=len(cal_days))
    return np.divide(s, n, out=np.zeros_like(s), where=n > 0)


def main() -> int:
    con = duckdb.connect()
    build(con)
    prepare(con)
    rng = np.random.default_rng(0)
    cal = con.execute("select date, c from ci order by date").fetchall()
    all_days = np.array([d for d, _ in cal], dtype="datetime64[D]")
    lines = ["# 段階1b の集計 (宣言: research/stage1b_hold/PREREG.md)", "",
             f"範囲は {RANGE:.3%}、1万回。合否はコスト {JUDGE_COST:.1%}。1日の平均は bp (0.01%)、1回の成績は %。"
             f"{LAST_DAY} より後の値段は使っていない (検証の最後の方に入ったものは、そこで手放したことにしている)。", ""]
    pools = {}
    for name, (start, end) in PERIODS.items():
        closes = np.array([c for d, c in cal if start <= d <= end])
        prev = [c for d, c in cal if d < start][-1:]
        tsr = sharpe(np.diff(np.concatenate([prev, closes])) / np.concatenate([prev, closes])[:-1])
        lines += [f"## {name} ({start} 〜 {end})", "", f"TOPIX 買い持ちの年換算シャープレシオ: {tsr:.2f}", "",
                  "| 候補 | コスト | 1日の平均 | 範囲 | 年換算SR | 回数/年 | 1回の平均 | 1日目 | 2日目以降 | 勝ち | 合否の条件 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        detail = []
        for hold, block in HOLDS.items():
            last = min(len(all_days) - 1, int(np.searchsorted(all_days, np.datetime64(end), side="right")) - 1 + hold - 1)
            cal_days = all_days[int(np.searchsorted(all_days, np.datetime64(start))):last + 1]
            n_period = int(((cal_days >= np.datetime64(start)) & (cal_days <= np.datetime64(end))).sum())
            for cand, side in CANDIDATES.items():
                ids, entries, k, days, r = paths(con, cand, side, hold)
                ent = np.array(entries, dtype="datetime64[D]")
                in_p = (ent >= np.datetime64(start)) & (ent <= np.datetime64(end))
                keep = in_p[ids]
                n_tr = int(in_p.sum())
                for cost in COSTS:
                    v = with_costs(ids, entries, k, days, r, side, cost)
                    x = daily(cal_days, days, v, keep)
                    lo, hi = boot_range(x, rng, block, RANGE)
                    sr = sharpe(x)
                    tot = np.bincount(ids[keep], weights=v[keep], minlength=len(entries))[in_p]
                    d1 = np.bincount(ids[keep & (k == 1)], weights=v[keep & (k == 1)], minlength=len(entries))[in_p]
                    verdict = ""
                    if cost == JUDGE_COST:
                        ok = (x.mean() > 0) if name == "学習" else (lo > 0 and sr > tsr)
                        verdict = "満たす" if ok else "満たさない"
                    lines.append(
                        f"| {cand}-{hold} | {cost:.1%} | {x.mean() * 1e4:.2f} | {lo * 1e4:.2f} 〜 {hi * 1e4:.2f} | {sr:.2f} | "
                        f"{n_tr / n_period * ANNUAL:.0f} | {tot.mean() * 100:.2f} | {d1.mean() * 100:.2f} | "
                        f"{(tot - d1).mean() * 100:.2f} | {(tot > 0).mean():.0%} | {verdict} |")
                v = with_costs(ids, entries, k, days, r, side, JUDGE_COST)
                x = daily(cal_days, days, v, keep)
                by_year, by_month = {}, {}
                for d, val in zip(cal_days.astype(object), x):
                    by_year[d.year] = by_year.get(d.year, 0) + val
                    by_month[(d.year, d.month)] = by_month.get((d.year, d.month), 0) + val
                share = (f"。上位2か月の合計が全体に占める割合 {sum(sorted(by_month.values(), reverse=True)[:2]) / x.sum():.0%}"
                         if x.sum() > 0 else "")
                detail.append(f"- **{cand}-{hold}**: 年ごとの合計 (bp、コスト 0.2%) "
                              + " / ".join(f"{y}: {val * 1e4:.0f}" for y, val in sorted(by_year.items())) + share)
                ctrl = control(con, pools, side, hold, cal_days, start, end, n_tr, rng)
                detail.append(f"  - ランダム対照 (同じ向き・同じ回数・同じ期間、{N_CONTROL}回): 1日の平均 中央 {np.median(ctrl) * 1e4:.2f} bp "
                              f"(5%〜95%: {np.quantile(ctrl, 0.05) * 1e4:.2f} 〜 {np.quantile(ctrl, 0.95) * 1e4:.2f})、"
                              f"候補を上回った割合 {(ctrl >= x.mean()).mean():.1%}")
        lines += [""] + detail + [""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def control(con, pools, side, hold, cal_days, start, end, n_trades, rng) -> np.ndarray:
    """取引時間外の開示がある会社×日 から同じ回数を選び、同じ向き・同じ期間持った場合の 1日の平均 (コスト 0.2%) の分布。"""
    if (side, hold) not in pools:
        ids, entries, k, days, r = paths(con, "ALL", side, hold)
        v = with_costs(ids, entries, k, days, r, side, JUDGE_COST)
        starts = np.searchsorted(ids, np.arange(len(entries)))
        ends = np.searchsorted(ids, np.arange(len(entries)), side="right")
        pools[(side, hold)] = (np.array(entries, dtype="datetime64[D]"), starts, ends, days, v)
    ent, starts, ends, days, v = pools[(side, hold)]
    cands = np.flatnonzero((ent >= np.datetime64(start)) & (ent <= np.datetime64(end)))
    out = np.empty(N_CONTROL)
    for i in range(N_CONTROL):
        sel = rng.choice(cands, size=min(n_trades, len(cands)), replace=False)
        lens = ends[sel] - starts[sel]
        idx = np.repeat(starts[sel] - np.cumsum(lens) + lens, lens) + np.arange(lens.sum())
        keep = np.zeros(len(days), dtype=bool)
        keep[idx] = True
        out[i] = daily(cal_days, days, v, keep).mean()
    return out


if __name__ == "__main__":
    sys.exit(main())
