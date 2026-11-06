"""TDnet の XBRL (zip) から、今期の業績予想 (営業利益・経常利益・純利益) を読む (49 で使う)。

決算短信のサマリー (…-Summary…ixbrl.htm) と業績予想の修正 (…-rvfc-…ixbrl.htm) のインライン XBRL を
alchemy_stock.xbrl.parse で読み、通期 (CurrentYearDuration) の予想 (ForecastMember) の数字を取る。
連結があれば連結、無ければ単体 (J-Quants の財務情報と同じ選び方)。予想に幅があるもの (上限・下限) は使わない。
修正の書類には前回の予想 (PreviousMember) も入っているので、確かめ用に一緒に返す。

項目名と J-Quants の列の対応 (FOP・FOdP・FNP) は scripts/check_recorder_forecast.py で突き合わせて決めた (2026-11-06、
2026-10 の開示 80件で、両方に数字があるものは全部一致):
- IFRS・米国基準の税引前利益は、J-Quants では経常利益 (FOdP) に入らない → 経常利益は日本基準の OrdinaryIncome だけ
- 業績予想の修正の書類の単体の予想は、J-Quants では別の列 (FNCOP など) に入り、FOP などは空になる
  (2024-01 以降の修正 5,780件のうち 1,155件)。検証の 49 はこれを出来事に数えていない → 修正の書類は連結の予想だけを使う。
  決算短信は、単体だけの会社の予想も FOP などに入る → 決算短信は連結が無ければ単体を使う
"""
from __future__ import annotations

import io
import zipfile

from ..xbrl import parse

# J-Quants の列 → XBRL の項目名 (会計基準ごと、先にあるものを使う)
NAMES = {
    "op": ("OperatingIncome", "OperatingIncomeIFRS"),
    "odp": ("OrdinaryIncome",),
    "np": ("ProfitAttributableToOwnersOfParent", "ProfitAttributableToOwnersOfParentIFRS", "NetIncome"),
}
RANGE = {"UpperMember", "LowerMember"}


def ixbrl_files(zbytes: bytes) -> list[tuple[str, bytes]]:
    """zip の中の、サマリーと業績予想の修正のインライン XBRL。"""
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    names = [n for n in z.namelist() if n.endswith("ixbrl.htm") and ("Summary" in n or "-rvfc-" in n)]
    return [(n, z.read(n)) for n in names]


def current_forecast(parsed: dict, allow_noncons: bool = True) -> dict | None:
    """通期の予想を {fy_end, cons, op, odp, np, prev_op, prev_odp, prev_np} で返す。予想が無ければ None。

    allow_noncons=False (業績予想の修正の書類) のときは、連結の予想が無ければ None。
    """
    rows = []
    for f in parsed["facts"]:
        m = set(f["members"])
        if f["period"] != "CurrentYearDuration" or "ForecastMember" not in m or m & RANGE or f["value"] is None:
            continue
        rows.append((f, m))
    cons = any("ConsolidatedMember" in m for _, m in rows)
    if not rows or not (cons or allow_noncons):
        return None
    out: dict = {"cons": cons, "fy_end": None}
    for f, m in rows:
        if cons != ("ConsolidatedMember" in m):
            continue
        for key, names in NAMES.items():
            if f["element"] not in names:
                continue
            col = f"prev_{key}" if "PreviousMember" in m else key
            rank = names.index(f["element"])
            if col not in out or rank < out[col][1]:
                out[col] = (f["value"], rank)
            if col == key:
                out["fy_end"] = out["fy_end"] or f.get("end")
    vals = {k: (v[0] if isinstance(v, tuple) else v) for k, v in out.items()}
    for k in ("op", "odp", "np", "prev_op", "prev_odp", "prev_np"):
        vals.setdefault(k, None)
    return vals if any(vals[k] is not None for k in ("op", "odp", "np")) else None


def read_zip(zbytes: bytes) -> dict | None:
    """zip から今期の予想を読む。サマリーと修正の両方があれば、修正を先に見る。"""
    files = sorted(ixbrl_files(zbytes), key=lambda x: "-rvfc-" not in x[0])
    for name, content in files:
        fc = current_forecast(parse(content), allow_noncons="-rvfc-" not in name)
        if fc:
            return {**fc, "file": name.rsplit("/", 1)[-1]}
    return None
