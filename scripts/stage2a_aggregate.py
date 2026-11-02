"""段階2a (research/stage2a_llm_trial/PREREG.md) の集計。全件の答えがそろってから1回だけ動かす。

答えは試し読み (data/stage2a/pilot/b*.jsonl) と全件 (--answers の *.jsonl、こちらが優先) を合わせる。
組の分け方・候補・基準は宣言どおり。結果は data/results/stage2a_llm_trial.md。

    uv run python scripts/stage2a_aggregate.py --answers <answers_v2 フォルダ> --docs <docs_v2 フォルダ>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage1_numbers import (  # noqa: E402
    ANNUAL, COSTS, JUDGE_COST, LENDING_FEE, N_BOOT, N_CONTROL, PERIODS, boot_range, build, daily, sharpe,
)

ITEMS = Path("data/stage2a/items.jsonl")
PILOT = Path("data/stage2a/pilot")
OUT = Path("data/results/stage2a_llm_trial.md")
RANGE = 1 - 0.05 / 2  # 97.5%
CANDS = {"CONT": ("続く組", 1), "REV": ("戻る組", -1)}
Q1, Q2, Q3 = set("ABCD"), {"続く", "一時的", "判断できない"}, {"控えめ", "妥当", "判断できない"}
YEAR_LEFT = re.compile(r"平\s+成|令\s+和|(?<![\d,.])(1\s*9|2\s*0)\s*\d\s*\d\s*年|(?<![\d,.])2\s+0\d{2}")


def group(a: dict) -> str:
    if a["q1"] == "A" and a["q2"] == "続く":
        return "続く組"
    if a["q1"] in ("B", "C") or a["q2"] == "一時的":
        return "戻る組"
    return "どちらでもない"


def load_answers(answers_dir: Path) -> dict[str, dict]:
    out = {}
    for path in sorted(PILOT.glob("b*.jsonl")) + sorted(answers_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                a = json.loads(line)
                out[a["qid"]] = a
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--answers", type=Path, required=True)
    p.add_argument("--docs", type=Path, required=True)
    args = p.parse_args()

    items = [json.loads(line) for line in ITEMS.read_text().splitlines()]
    answers = load_answers(args.answers)
    missing = [it["qid"] for it in items if it["qid"] not in answers]
    bad = [q for q, a in answers.items() if a.get("q1") not in Q1 or a.get("q2") not in Q2 or a.get("q3") not in Q3]
    if missing or bad:
        print(f"答えが無い {len(missing)}件 {missing[:10]}、形が崩れた {len(bad)}件 {bad[:10]}。集計しない", file=sys.stderr)
        return 1

    con = duckdb.connect()
    build(con)
    tr = {(c, str(e)): row for c, e, *row in con.execute(
        "select code, entry, o, h, l, c, ul, ll, to_, tc, mrgn, adv from tr where cand = 'U10'").fetchall()}
    start, end = PERIODS["学習"]
    days = [d for (d,) in con.execute(f"select date from cal where date between '{start}' and '{end}' order by date").fetchall()]

    rows = []
    for it in items:
        a = answers[it["qid"]]
        o, h, l, c, ul, ll, to_, tc, mrgn, adv = tr[(it["code"], it["entry"])]
        rows.append({
            "qid": it["qid"], "entry": date.fromisoformat(it["entry"]), "group": group(a), "q3": a["q3"],
            "known": a["q5"] != "分からない", "year_left": bool(YEAR_LEFT.search((args.docs / f"{it['qid']}.md").read_text())),
            "move": (c / o - 1) - (tc / to_ - 1), "adv": adv,
            "can_short": mrgn == "2" and not (ll == "1" and o == l),
        })
    adv_median = float(np.median([r["adv"] for r in rows]))
    rng = np.random.default_rng(0)

    def trades(cand: str, keep=lambda r: True) -> list[tuple[date, float]]:
        name, side = CANDS[cand]
        sel = [r for r in rows if r["group"] == name and keep(r) and (side > 0 or r["can_short"])]
        return [(r["entry"], side * r["move"] - (LENDING_FEE if side < 0 else 0)) for r in sel]

    def stats(t, cost):
        x = daily(days, t, cost)
        lo, hi = boot_range(x, rng, level=RANGE)
        per = np.array([v - cost for _, v in t]) if t else np.zeros(1)
        return x, lo, hi, per

    lines = ["# 段階2a の集計 (宣言: research/stage2a_llm_trial/PREREG.md)", "",
             f"学習期間のみ。範囲は {RANGE:.1%}、ブロック 20 営業日、{N_BOOT} 回。1日の平均は bp、1回の成績は %。", "",
             "## 組ごとの件数", "", "| 組 | 件数 | 会社が分かった | 年が残っていた | 売れる (貸借) |", "|---|---|---|---|---|"]
    for name in ("続く組", "戻る組", "どちらでもない"):
        g = [r for r in rows if r["group"] == name]
        lines.append(f"| {name} | {len(g)} | {sum(r['known'] for r in g)} | {sum(r['year_left'] for r in g)} | {sum(r['can_short'] for r in g)} |")
    lines += ["", f"会社が分かった割合: {np.mean([r['known'] for r in rows]):.0%}", "",
              "## 候補 (合否)", "",
              "| 候補 | 対象 | コスト | 1日の平均 | 範囲 | 年換算SR | 回数/年 | 1回の平均 | 勝ち | 条件 |", "|---|---|---|---|---|---|---|---|---|---|"]
    verdicts = {}
    for cand in CANDS:
        for label, keep in (("全部", lambda r: True), ("会社が分からないもの", lambda r: not r["known"])):
            t = trades(cand, keep)
            for cost in COSTS:
                x, lo, hi, per = stats(t, cost)
                v = ""
                if cost == JUDGE_COST:
                    v = "満たす" if x.mean() > 0 else "満たさない"
                    verdicts[(cand, label)] = x.mean() > 0
                lines.append(f"| {cand} | {label} | {cost:.1%} | {x.mean() * 1e4:.2f} | {lo * 1e4:.2f} 〜 {hi * 1e4:.2f} | "
                             f"{sharpe(x):.2f} | {len(t) / len(days) * ANNUAL:.0f} | {per.mean() * 100:.2f} | {(per > 0).mean():.0%} | {v} |")
        ok = verdicts[(cand, "全部")] and verdicts[(cand, "会社が分からないもの")]
        lines.append(f"| **{cand}** | | | | | | | | | **次に進む条件を{'満たす' if ok else '満たさない'}** |")

    def move_table(title, splits):
        out = ["", f"## {title} (始値 → 終値、TOPIX を引く、コスト前、1回の平均 %)", "",
               "| 分け方 | 続く組 | 戻る組 | どちらでもない | 続く − 戻る (97.5% 範囲) |", "|---|---|---|---|---|"]
        for label, keep in splits:
            cells, m = [], {}
            for name in ("続く組", "戻る組", "どちらでもない"):
                v = np.array([r["move"] for r in rows if r["group"] == name and keep(r)])
                m[name] = v
                cells.append(f"{v.mean() * 100:+.2f} ({len(v)})" if len(v) else "—")
            a, b = m["続く組"], m["戻る組"]
            if len(a) > 1 and len(b) > 1:
                diffs = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(N_BOOT)]
                q = (1 - RANGE) / 2
                cells.append(f"{(a.mean() - b.mean()) * 100:+.2f} ({np.quantile(diffs, q) * 100:+.2f} 〜 {np.quantile(diffs, 1 - q) * 100:+.2f})")
            else:
                cells.append("—")
            out.append(f"| {label} | " + " | ".join(cells) + " |")
        return out

    lines += move_table("組ごとの値動き", [
        ("全部", lambda r: True),
        ("会社が分かった", lambda r: r["known"]),
        ("会社が分からない", lambda r: not r["known"]),
        ("年が残っていたものを除く", lambda r: not r["year_left"]),
        (f"大きい会社 (売買代金 ≥ 中央値 {adv_median / 1e8:.1f}億円)", lambda r: r["adv"] >= adv_median),
        ("小さい会社 (売買代金 < 中央値)", lambda r: r["adv"] < adv_median),
    ])
    lines += move_table("質問3 (控えめか) ごと (報告だけ)", [(f"質問3 = {k}", (lambda k: lambda r: r["q3"] == k)(k)) for k in sorted(Q3)])

    lines += ["", "## ランダム対照 (対象全体から同じ回数を選び、同じ向きに売買。コスト 0.2%)", ""]
    for cand, (name, side) in CANDS.items():
        t = trades(cand)
        pool = [(r["entry"], side * r["move"] - (LENDING_FEE if side < 0 else 0)) for r in rows if side > 0 or r["can_short"]]
        x = daily(days, t, JUDGE_COST)
        ctrl = np.array([daily(days, [pool[i] for i in rng.choice(len(pool), len(t), replace=False)], JUDGE_COST).mean()
                         for _ in range(N_CONTROL)])
        lines.append(f"- {cand}: 1日の平均 {x.mean() * 1e4:.2f} bp。ランダム 中央 {np.median(ctrl) * 1e4:.2f} bp "
                     f"(5%〜95%: {np.quantile(ctrl, 0.05) * 1e4:.2f} 〜 {np.quantile(ctrl, 0.95) * 1e4:.2f})、候補を上回った割合 {(ctrl >= x.mean()).mean():.1%}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
