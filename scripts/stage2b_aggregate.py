"""段階2b (research/stage2b_revision/PREREG.md) の採点。1回読ませるごとに動かす。

LLM の点数 (-2 〜 +2) と、始値から 1・5・20 営業日目の引けまでの値動き (TOPIX を引く) のそろい具合 (順位の相関) を出し、
数字だけで並べた場合 (営業利益の修正率の順) と、点数をランダムに入れ替えた場合と比べる。
結果は data/results/stage2b_<組>_<回>.md。

    uv run python scripts/stage2b_aggregate.py --set a --round r1 --answers <答えのフォルダ>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage1_numbers import LAST_DAY, build  # noqa: E402
from stage2b_prepare import prepare  # noqa: E402

HOLDS = (1, 5, 20)
MAIN = 20
N_BOOT = 10_000
SCORES = {-2, -1, 0, 1, 2}


def moves(con, items: list[dict]) -> dict[str, dict[int, float]]:
    """始値で入って k 営業日目の引けで手放したときの値動き − TOPIX。値段が付かなくなったら最後の値段で手放す。"""
    con.execute("create or replace temp table it (qid varchar, code varchar, entry date)")
    con.executemany("insert into it values (?, ?, ?)", [(i["qid"], i["code"], i["entry"]) for i in items])
    rows = con.execute(f"""
        with b as (
            select it.qid, it.code, k.k, e.o to_, x.date dx, x.c tx, bo.o, pe.s se
            from it join ci e on e.date = it.entry
            cross join (select unnest({list(HOLDS)}) k) k
            join ci x on x.i = e.i + k.k - 1
            join (select Code code, Date::date date, O::double o from read_csv('data/jquants/equities/bars/daily/**/*.csv.gz',
                  union_by_name=true, all_varchar=true)) bo on bo.code = it.code and bo.date = it.entry
            join px pe on pe.code = it.code and pe.date = it.entry
            where x.date <= date '{LAST_DAY}'
        )
        select qid, k, (p.c / o) * exp(se - p.s) - (tx / to_) from b
        asof join px p on p.code = b.code and b.dx >= p.date
    """).fetchall()
    out: dict[str, dict[int, float]] = {}
    for qid, k, v in rows:
        out.setdefault(qid, {})[k] = v
    return out


def rho(x, y) -> float:
    return float(spearmanr(x, y).statistic)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--set", required=True)
    p.add_argument("--round", required=True)
    p.add_argument("--answers", type=Path, required=True)
    args = p.parse_args()

    items = [json.loads(line) for line in Path(f"data/stage2b/items_{args.set}.jsonl").read_text().splitlines()]
    answers = {}
    for path in sorted(args.answers.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                a = json.loads(line)
                answers[a["qid"]] = a
    missing = [it["qid"] for it in items if it["qid"] not in answers]
    bad = [q for q, a in answers.items() if a.get("score") not in SCORES]
    if missing or bad:
        print(f"答えが無い {len(missing)}件 {missing[:10]}、形が崩れた {len(bad)}件 {bad[:10]}。採点しない", file=sys.stderr)
        return 1

    con = duckdb.connect()
    build(con)
    prepare(con)
    mv = moves(con, items)
    rng = np.random.default_rng(0)
    rows = [{**it, "score": answers[it["qid"]]["score"], "conf": answers[it["qid"]].get("conf"), "mv": mv[it["qid"]]}
            for it in items if it["qid"] in mv]

    lines = [f"# 段階2b 組 {args.set} 回 {args.round} (宣言: research/stage2b_revision/PREREG.md)", "",
             f"{len(rows)}件。値動きは始値 → k 営業日目の引け、TOPIX を引く、コスト前。主に見るのは {MAIN}日。"
             f"範囲は件を選び直すブートストラップ ({N_BOOT}回) の 95%。", "",
             "## 点数の分布", "", "| 点数 | 件数 | " + " | ".join(f"{k}日の平均" for k in HOLDS) + " |",
             "|---|---|" + "---|" * len(HOLDS)]
    for s in sorted(SCORES):
        g = [r for r in rows if r["score"] == s]
        cells = [f"{np.mean([r['mv'][k] for r in g if k in r['mv']]) * 100:+.2f}%" if g else "—" for k in HOLDS]
        lines.append(f"| {s:+d} | {len(g)} | " + " | ".join(cells) + " |")

    lines += ["", "## そろい具合 (順位の相関) と、買い − 売り の差", "",
              "| 持つ日数 | LLM の相関 (範囲) | ランダムに入れ替えて LLM 以上になる割合 | 数字だけ (修正率) の相関 (範囲) | "
              "LLM の 買い(+1,+2) − 売り(−1,−2) |", "|---|---|---|---|---|"]
    for k in HOLDS:
        r = [x for x in rows if k in x["mv"]]
        s, y = np.array([x["score"] for x in r]), np.array([x["mv"][k] for x in r])
        base = [x for x in r if x["rate"] is not None]
        bs, by = np.array([x["rate"] for x in base]), np.array([x["mv"][k] for x in base])
        idx = [rng.integers(0, len(r), len(r)) for _ in range(N_BOOT)]
        boot = [rho(s[i], y[i]) for i in idx]
        bidx = [rng.integers(0, len(base), len(base)) for _ in range(N_BOOT)]
        bboot = [rho(bs[i], by[i]) for i in bidx]
        perm = np.array([rho(rng.permutation(s), y) for _ in range(N_BOOT)])
        buy, sell = y[s > 0], y[s < 0]
        diff = f"{(buy.mean() - sell.mean()) * 100:+.2f}% ({len(buy)}件 / {len(sell)}件)" if len(buy) and len(sell) else "—"
        lines.append(f"| {k}日 | {rho(s, y):+.3f} ({np.quantile(boot, 0.025):+.3f} 〜 {np.quantile(boot, 0.975):+.3f}) | "
                     f"{(perm >= rho(s, y)).mean():.1%} | {rho(bs, by):+.3f} ({np.quantile(bboot, 0.025):+.3f} 〜 "
                     f"{np.quantile(bboot, 0.975):+.3f}) | {diff} |")

    def split(title, keep_fns):
        out = ["", f"## {title} ({MAIN}日の相関、報告だけ)", "", "| 分け方 | 件数 | LLM | 数字だけ |", "|---|---|---|---|"]
        for label, keep in keep_fns:
            r = [x for x in rows if keep(x) and MAIN in x["mv"]]
            b = [x for x in r if x["rate"] is not None]
            if len(r) < 10:
                out.append(f"| {label} | {len(r)} | — | — |")
                continue
            lr = rho([x["score"] for x in r], [x["mv"][MAIN] for x in r])
            br = rho([x["rate"] for x in b], [x["mv"][MAIN] for x in b]) if len(b) >= 10 else float("nan")
            out.append(f"| {label} | {len(r)} | {lr:+.3f} | {br:+.3f} |")
        return out

    lines += split("分けて見る", [
        ("上方修正 (修正率 > 0)", lambda x: (x["rate"] or 0) > 0),
        ("下方修正 (修正率 < 0)", lambda x: (x["rate"] or 0) < 0),
        ("それ以外", lambda x: not x["rate"]),
        ("大きい会社", lambda x: x["size"].startswith("大")),
        ("中", lambda x: x["size"] == "中"),
        ("小さい会社", lambda x: x["size"].startswith("小")),
        ("自信 高", lambda x: x["conf"] == "高"),
        ("自信 中", lambda x: x["conf"] == "中"),
        ("自信 低", lambda x: x["conf"] == "低"),
    ])
    out = Path(f"data/results/stage2b_{args.set}_{args.round}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
