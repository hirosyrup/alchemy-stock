"""決算短信のサマリー (1ページ目) のインライン XBRL から数字を取り出す (PLAN.md §6.1)。

数字の1つ1つに、何の数字か (name)・いつのどの区分か (contextRef)・単位・桁 (scale)・符号 (sign) の印が付いている。
contextRef は「期間_区分_区分…」の形 (例: CurrentYearDuration_ConsolidatedMember_ForecastMember =
当期・通期・連結・予想)。期間は Current / Prior / Next × Year / AccumulatedQ1〜Q3 × Duration / Instant、
区分は 連結 (Consolidated) / 単体 (NonConsolidated)、実績 (Result) / 予想 (Forecast) / 予想の上限・下限 (Upper / Lower)、
配当の時期 (FirstQuarter〜YearEnd / Annual) など。

2017〜2025年の実物では、どの年もインライン XBRL 2008年版・tse-ed-t の書式だった。会計基準ごとに項目名が違う
(日本基準 NetSales、IFRS SalesIFRS、銀行 OrdinaryRevenuesBK など) ので、名前はそのまま残し、使う側で選ぶ。
長い文章 (業績の説明など) は取らない。LLM の段階で PDF から読む。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from lxml import etree

XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
EMPTY = {"", "-", "－", "―", "—", "‐"}
MINUS = ("△", "▲", "-", "－")  # 決算書のマイナスの印。本来は sign="-" で表すが、文字に入っていることもある
MAX_TEXT = 200  # これより長い文は取らない


def _local(name: str) -> str:
    return name.split(":", 1)[-1]


def _text(e) -> str:
    return "".join(e.itertext()).strip()


def parse_number(text: str, fmt: str | None, scale: str | None, sign: str | None) -> float | None:
    """表に見えている文字を数に直す。空欄・横棒は None。"""
    fmt = (fmt or "").replace("-", "")
    if "zero" in fmt:  # ixt:fixedzero / zerodash: 横棒で 0 を表す
        return 0.0
    t = text.replace(" ", "").replace("　", "")
    if t in EMPTY:
        return None
    negative = sign == "-"
    if t.startswith(MINUS):
        t, negative = t[1:], True
    if fmt.endswith("numcommadecimal"):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        v = Decimal(t).scaleb(int(scale or 0))
    except InvalidOperation:
        return None
    return float(-v if negative else v)


def _contexts(root) -> dict[str, dict]:
    out = {}
    for e in root.iter():
        if isinstance(e.tag, str) and etree.QName(e).localname == "context":
            dates = {etree.QName(d).localname: (d.text or "").strip() for d in e.iter() if isinstance(d.tag, str)}
            out[e.get("id")] = {"start": dates.get("startDate"), "end": dates.get("endDate") or dates.get("instant")}
    return out


def parse(content: bytes) -> dict:
    """インライン XBRL を読み、書類の情報 (doc)・数字 (facts)・短い文や印 (texts) を返す。"""
    root = etree.fromstring(content)
    contexts = _contexts(root)
    # 会社ごとの名前空間 (例: tse-qcedjpsm-33530) の真ん中が書類の種類
    # (a 通期 / q 四半期、c 連結 / n 単体、jp 日本基準 / if IFRS / us 米国基準)
    report = next((p.split("-")[1] for p in root.nsmap if p and p.startswith("tse-") and p != "tse-ed-t"), None)
    facts, texts = [], []
    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        kind = etree.QName(e).localname
        if kind not in ("nonFraction", "nonNumeric"):
            continue
        ctx = e.get("contextRef")
        period, *members = ctx.split("_")
        base = {"element": _local(e.get("name")), "context": ctx, "period": period, "members": members, **contexts.get(ctx, {})}
        if kind == "nonFraction":
            nil = e.get(XSI_NIL) == "true"
            value = None if nil else parse_number(_text(e), e.get("format"), e.get("scale"), e.get("sign"))
            facts.append({**base, "value": value, "unit": e.get("unitRef"), "decimals": e.get("decimals")})
        else:
            fmt = e.get("format") or ""
            if fmt.endswith("booleantrue") or fmt.endswith("booleanfalse"):
                value = "true" if fmt.endswith("booleantrue") else "false"
            else:
                value = _text(e)
                if len(value) > MAX_TEXT:
                    continue
            texts.append({**base, "value": value})
    first = {t["element"]: t["value"] for t in reversed(texts)}
    doc = {
        "report": report,
        "document_name": first.get("DocumentName"),
        "securities_code": first.get("SecuritiesCode"),
        "fiscal_year_end": first.get("FiscalYearEnd"),
        "n_facts": len(facts),
        "n_values": sum(f["value"] is not None for f in facts),
    }
    return {"doc": doc, "facts": facts, "texts": texts}
