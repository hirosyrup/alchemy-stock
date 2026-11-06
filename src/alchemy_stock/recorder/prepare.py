"""前の晩 (と朝) に作る、次の取引日の記録の材料 (docs/RECORDER_DESIGN.md §3)。

- 時価総額の順位 (54・49 の対象)
- 会社・会計年度ごとの、今の業績予想 (49 の「前の予想」)

J-Quants の財務情報の表 (一括ダウンロードの CSV か API の data と同じ列名) を受け取って作る。取り方は持たない。
"""
from __future__ import annotations

import pandas as pd

FC_COLS = {"op": ("FOP", "NxFOP"), "odp": ("FOdP", "NxFOdP"), "np": ("FNP", "NxFNp")}


def fins_rows(fins: pd.DataFrame) -> pd.DataFrame:
    """財務情報を、予想の流れの1行ずつに直す (検証の stage1_numbers.py の f と fc の s と同じ)。

    決算短信 (通期以外)・修正は今期 (CurFYEn) の予想、通期の決算短信は来期 (NxtFYEn) の予想として並べる。
    REIT と OtherPeriod は除く。返す列: code, d, t, no, kind, fy, is_new, op, odp, np
    """
    f = fins[~fins.DocType.str.contains("REIT") & ~fins.DocType.str.startswith("OtherPeriod")].copy()
    t = f.DiscTime.fillna("").astype(str)
    out = pd.DataFrame({
        "code": f.Code.astype(str), "d": pd.to_datetime(f.DiscDate), "t": t.where(t.str.len() != 5, t + ":00"),
        "no": f.DiscNo.astype(str), "kind": f.DocType.str.extract(r"^([^_]+)", expand=False),
    })
    fy = out.kind == "FYFinancialStatements"
    out["fy"] = pd.to_datetime(f.NxtFYEn.where(fy, f.CurFYEn), errors="coerce")
    out["is_new"] = ~fy
    for k, (cur, nxt) in FC_COLS.items():
        out[k] = pd.to_numeric(f[nxt].where(fy, f[cur]), errors="coerce")
    return out[out.fy.notna()].sort_values(["d", "t", "no"]).reset_index(drop=True)


class ForecastBook:
    """会社・会計年度ごとの、項目別の最後の (空でない) 予想。前の晩に J-Quants から作り、その日は窓で読んだ予想を足す。"""

    def __init__(self) -> None:
        self.v: dict[tuple[str, pd.Timestamp], dict[str, float]] = {}

    def update(self, code: str, fy, values: dict) -> None:
        cur = self.v.setdefault((code, pd.Timestamp(fy)), {})
        for k in FC_COLS:
            x = values.get(k)
            if x is not None and x == x:
                cur[k] = float(x)

    def prev(self, code: str, fy) -> dict[str, float]:
        return dict(self.v.get((code, pd.Timestamp(fy)), {}))

    @classmethod
    def from_rows(cls, rows: pd.DataFrame) -> "ForecastBook":
        b = cls()
        for r in rows.itertuples():
            b.update(r.code, r.fy, {"op": r.op, "odp": r.odp, "np": r.np})
        return b


def cap_ranks(prev: pd.DataFrame) -> pd.DataFrame:
    """前の取引日の時価総額 (調整前の終値 × (発行済み − 自己株)) の順位。普通株だけ。

    prev は1行1社: code、close_raw、shares、common。返す列に cap_rank を足す (順位の無い会社は除く)。
    """
    p = prev[prev.common & prev.close_raw.notna() & prev.shares.notna()].copy()
    p["cap_rank"] = (p.close_raw * p.shares).rank(ascending=False)
    return p.reset_index(drop=True)
