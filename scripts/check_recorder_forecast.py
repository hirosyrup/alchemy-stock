"""XBRL から読んだ今期の予想 (src/alchemy_stock/recorder/forecast.py) が、J-Quants の財務情報の FOP・FOdP・FNP と同じになるかを確かめる。

    uv run python scripts/check_recorder_forecast.py [件数]

J-Quants の財務情報 (手元の data/jquants/fins/summary/live) の、四半期の決算短信と業績予想の修正を、
手元の TDnet の一覧 (data/tdnet、XBRL の zip のリンクつき、直近31日しか取れない) と会社・日付・時刻で突き合わせ、
zip を取って読む。TDnet には 1秒に1回まで。株価は使わない。
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alchemy_stock.http import PoliteSession  # noqa: E402
from alchemy_stock.recorder.forecast import read_zip  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 80
COLS = {"op": "FOP", "odp": "FOdP", "np": "FNP"}

fins = duckdb.sql(f"""select DiscDate::date d, substr(DiscTime, 1, 5) t, Code code, DocType kind, CurFYEn fy,
    try_cast(FOP as double) FOP, try_cast(FOdP as double) FOdP, try_cast(FNP as double) FNP
  from read_csv('{ROOT}/data/jquants/fins/summary/live/*.csv.gz', all_varchar=true)
  where DocType in ('EarnForecastRevision') or regexp_matches(DocType, '^[123]QFinancialStatements')""").df()
tdn = pd.concat([pd.read_json(f, lines=True, dtype={"code": str}) for f in sorted(glob.glob(f"{ROOT}/data/tdnet/2026/*.jsonl"))])
tdn = tdn[tdn.xbrl_url.fillna("") != ""]
tdn["d"] = pd.to_datetime(tdn.date)
fins["d"] = pd.to_datetime(fins.d)
fins["is_rev"] = fins.kind == "EarnForecastRevision"
tdn["is_rev"] = tdn.title.str.contains("予想") & ~tdn.title.str.contains("決算短信")
m = fins.merge(tdn[tdn.title.str.contains("決算短信|予想")][["code", "d", "time", "is_rev", "title", "xbrl_url"]],
               left_on=["code", "d", "t", "is_rev"], right_on=["code", "d", "time", "is_rev"])
m = m.drop_duplicates(["code", "d", "t", "kind"])
print(f"突き合わせできた {len(m)}件 (修正 {int(m.is_rev.sum())}件)。うち {N}件を読む\n")
m = pd.concat([m[m.is_rev].sample(min(N // 2, int(m.is_rev.sum())), random_state=0),
               m[~m.is_rev].sample(min(N - N // 2, int((~m.is_rev).sum())), random_state=0)])

s = PoliteSession(min_interval=1.0)
CACHE = ROOT / "data/recorder/xbrl_cache"  # 読み直しのときに TDnet に取りに行かない
CACHE.mkdir(parents=True, exist_ok=True)
rows = []
for r in m.itertuples():
    path = CACHE / r.xbrl_url.rsplit("/", 1)[-1]
    try:
        if not path.exists():
            path.write_bytes(s.get(r.xbrl_url).content)
        fc = read_zip(path.read_bytes())
    except Exception as e:  # noqa: BLE001
        fc = {"err": type(e).__name__}
    rows.append({"code": r.code, "d": r.d, "kind": r.kind, "fy": r.fy, **{v: getattr(r, v) for v in COLS.values()},
                 **{f"x_{k}": (fc or {}).get(k) for k in COLS}, "x_fy": (fc or {}).get("fy_end"), "file": (fc or {}).get("file"),
                 "err": (fc or {}).get("err") if fc else "予想なし"})
x = pd.DataFrame(rows)
for k, col in COLS.items():
    both = x[col].notna() & x[f"x_{k}"].notna()
    same = both & ((x[col] - x[f"x_{k}"]).abs() <= 1e-6 * x[col].abs().clip(lower=1))
    only_j = x[col].notna() & x[f"x_{k}"].isna()
    only_x = x[col].isna() & x[f"x_{k}"].notna()
    print(f"- {col}: 両方あり {int(both.sum())}件 → 一致 {int(same.sum())}件。J-Quants だけ {int(only_j.sum())}件、XBRL だけ {int(only_x.sum())}件")
fy_ok = (x.fy.astype(str) == x.x_fy.astype(str)) | x.x_fy.isna()
print(f"- 年度の終わりの日: 一致 {int((x.fy.astype(str) == x.x_fy.astype(str)).sum())}件 / 読めた {int(x.x_fy.notna().sum())}件")
print(f"- 読めなかった: {x.err.value_counts().to_dict()}")
bad = x[~fy_ok | x.apply(lambda r: any(pd.notna(r[c]) and pd.notna(r[f'x_{k}']) and abs(r[c] - r[f'x_{k}']) > 1e-6 * max(abs(r[c]), 1)
                                        or (pd.notna(r[c]) != pd.notna(r[f'x_{k}'])) for k, c in COLS.items()), axis=1)]
if len(bad):
    print("\n食い違い:")
    print(bad.drop(columns=["err"]).to_string(index=False))
