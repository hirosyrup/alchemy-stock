"""自社株買いの資料 (data/pdf_buyback) から数字を抜き出して data/explore/buyback_parsed.jsonl に置く。
抜き出すもの: 決議の上限 (株数・金額)、決議の取得期間 (始め・終わり)、累計の取得 (株数・金額、取得状況の報告だけ)。
資料の種類 (決定・状況・終了) は開示の題名で分ける。
    uv run python scripts/parse_buyback.py
"""
from __future__ import annotations

import glob
import json
import re
import unicodedata
from datetime import date

import pymupdf

ERA = {"平成": 1988, "令和": 2018}
DATE = re.compile(r"(平成|令和|)(\d{1,4}|元)年(\d{1,2})月(\d{1,2})日")
NUM = r"([\d,]+(?:\.\d+)?)(億|百万|万|千|)"


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", "", t)


def to_date(m) -> date | None:
    era, y, mo, d = m.groups()
    y = 1 if y == "元" else int(y)
    if era:
        y += ERA[era]
    try:
        return date(y, int(mo), int(d))
    except ValueError:
        return None


def to_num(n: str, u: str) -> float:
    return float(n.replace(",", "")) * {"億": 1e8, "百万": 1e6, "万": 1e4, "千": 1e3, "": 1}[u]


def first(pat: str, t: str):
    m = re.search(pat, t)
    return to_num(m.group(1), m.group(2)) if m else None


def parse(t: str) -> dict:
    out = {}
    # 上限: 「… 株 (上限)」「… 円 (上限)」をすべて拾い、違う値は足す (決議を 2回に分けた場合)。無ければラベルの後の最初の数字
    UP = r"\(上限(?:とする|とします)?\)"
    for key, unit, lab in (("cap_shares", "株", r"(?:取得し得る|取得しうる|取得する|取得可能)株式の総数"), ("cap_amount", "円", r"(?:取得価額|取得価格|取得金額)の総額")):
        vals = [to_num(a, b) for a, b in re.findall(NUM + unit + UP, t)]
        out[key] = sum(dict.fromkeys(vals)) if vals else first(lab + r"(?:\(上限\))?:?" + NUM + unit, t)
    # 期間: 文中の「日付〜日付」の組をすべて拾い、一番遅い終わりを決議の期間とみなす
    spans = []
    for m in re.finditer(DATE.pattern + r"(?:\(約定ベース\))?(?:～|~|〜|から|-|－|―)" + DATE.pattern, t):
        a = to_date(type("M", (), {"groups": lambda s, g=m.groups()[:4]: g})())
        b = to_date(type("M", (), {"groups": lambda s, g=m.groups()[4:]: g})())
        if a and b and a <= b:
            spans.append((a, b))
    if spans:
        out["period_start"] = min(a for a, _ in spans).isoformat()
        out["period_end"] = max(b for _, b in spans).isoformat()
    # 累計: 「累計」の後の最初の株数と金額 (ラベルが無い表の形でも、最初の「…株」「…円」)
    i = t.find("累計")
    if i >= 0:
        tail = t[i:i + 400]
        out["cum_shares"] = first(r"総数:?" + NUM + r"株", tail) or first(NUM + r"株", tail)
        out["cum_amount"] = first(r"総額:?" + NUM + r"円", tail) or first(NUM + r"円", tail)
    # 今回分 (月の途中経過)
    out["this_shares"] = first(r"取得した株式の総数:?" + NUM + r"株", t)
    out["this_amount"] = first(r"取得価額の総額:?" + NUM + r"円", t)
    return out


def main() -> None:
    meta = {}
    for f in glob.glob("data/jpx_listed/disclosures/*.jsonl"):
        for line in open(f):
            x = json.loads(line)
            if "自己株式" in x["title"]:
                meta[x["doc_id"]] = x
    with open("data/explore/buyback_parsed.jsonl", "w") as out:
        for f in sorted(glob.glob("data/pdf_buyback/*/*.pdf")):
            did = f.rsplit("/", 1)[1][:-4]
            m = meta.get(did)
            if m is None:
                continue
            kind = "決定" if "決定" in m["title"] else ("終了" if re.search("終了|結果", m["title"]) else "状況")
            try:
                t = norm("".join(p.get_text() for p in pymupdf.open(f)))
            except Exception as e:  # 壊れた PDF
                out.write(json.dumps({"doc_id": did, "code": m["code"], "date": m["date"], "kind": kind, "error": str(e)[:80]}, ensure_ascii=False) + "\n")
                continue
            rec = {"doc_id": did, "code": m["code"], "date": m["date"], "kind": kind, **parse(t)}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
