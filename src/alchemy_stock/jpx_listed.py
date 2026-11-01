"""東証上場会社情報サービス (過去10年分の適時開示、日付のみ) を読む。

流れ: 検索ページを開く → 市場ごと (または上場廃止会社) に検索 → 会社ごとのページを開く。
会社ページの開示の行には <tr id="1101_3"> のように番号が振られていて、先頭4桁が区分。
上場廃止した会社は、廃止後5年分だけ残っている。そのページは区分の番号が 5101〜 の別体系で、上場廃止日が載る。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .http import PoliteSession

HOST = "https://www2.jpx.co.jp"
SEARCH_PAGE = f"{HOST}/tseHpFront/JJK010010Action.do?Show=Show"
SEARCH_ACTION = f"{HOST}/tseHpFront/JJK010010Action.do"
LIST_ACTION = f"{HOST}/tseHpFront/JJK010030Action.do"

MARKETS = {"011": "プライム", "012": "スタンダード", "013": "グロース"}
DISCLOSURE_CATEGORIES = {
    # 上場中の会社のページ
    "1101": "決算情報",
    "1102": "決定事実・発生事実",
    "1103": "上場維持基準への適合に向けた計画",
    "1104": "その他",
    # 上場廃止した会社のページ
    "5101": "決算情報",
    "5102": "決定事実・発生事実",
    "5103": "その他",
}
PAGE_SIZE = 200


def _hidden_fields(soup: BeautifulSoup, form_name: str) -> dict[str, str]:
    """フォームの隠しフィールド (送信用のもの) を集める。結果一覧の各行の分は送らない。"""
    form = soup.find("form", attrs={"name": form_name})
    fields = {}
    for inp in form.find_all("input", type="hidden") if form else []:
        name = inp.get("name")
        if not name or inp.has_attr("disabled") or name.startswith("ccJjCrpSelKekkLst_st"):
            continue
        fields[name] = inp.get("value", "")
    return fields


def parse_company_list(html: str) -> tuple[list[dict], int, dict[str, str]]:
    """検索結果1ページ分の会社、全件数、次のページ用の隠しフィールドを返す。"""
    soup = BeautifulSoup(html, "lxml")
    flags = dict(re.findall(r"gotoBaseJh\('([^']+)',\s*'(\d+)'\)", html))
    fields = {}
    for inp in soup.find_all("input", attrs={"name": re.compile(r"^ccJjCrpSelKekkLst_st\[\d+\]\.")}):
        idx, key = re.match(r"ccJjCrpSelKekkLst_st\[(\d+)\]\.(\w+)", inp["name"]).groups()
        fields.setdefault(int(idx), {})[key] = inp.get("value", "").strip()
    companies = []
    for idx in sorted(fields):
        f = fields[idx]
        code = f.get("eqMgrCd", "")
        companies.append({
            "code": code,
            "name": f.get("eqMgrNm", ""),
            "market": f.get("szkbuNm", ""),
            "industry": f.get("gyshDspNm", ""),
            "fiscal_month": f.get("dspYuKssnKi", ""),
            "flag": flags.get(code, ""),
        })
    total = soup.find("input", attrs={"name": "souKnsu"})
    return companies, int(total["value"]) if total else len(companies), _hidden_fields(soup, "JJK010030Form")


def search_companies(session: PoliteSession, market: str | None = None, delisted: bool = False) -> list[dict]:
    """市場を1つ指定して上場中の会社を、または delisted=True で上場廃止会社を全件取る。"""
    session.get(SEARCH_PAGE)
    data = {"ListShow": "ListShow", "sniMtGmnId": "", "dspSsuPd": str(PAGE_SIZE), "mgrMiTxtBx": "", "eqMgrCd": ""}
    if market:
        data["szkbuChkbx"] = market
    if delisted:
        data["jjHisiKbnChkbx"] = "on"
    r = session.post(SEARCH_ACTION, data=data)
    companies, total, fields = parse_company_list(r.text)
    page = 1
    while len(companies) < total:
        page += 1
        r = session.post(LIST_ACTION, data={**fields, "Transition": "Transition", "lstDspPg": str(page)})
        more, _, fields = parse_company_list(r.text)
        if not more:
            break
        companies += more
    for c in companies:
        c["delisted"] = delisted
        if market and not c["market"]:
            c["market"] = MARKETS.get(market, market)
    return companies


def fetch_company_page(session: PoliteSession, code: str, flag: str) -> str:
    """会社ページを開く。検索をした同じセッションで呼ぶこと。"""
    data = {
        "BaseJh": "BaseJh", "lstDspPg": "1", "dspGs": str(PAGE_SIZE), "souKnsu": "1",
        "sniMtGmnId": "JJK010010", "dspJnKbn": "0", "dspJnKmkNo": "0", "mgrCd": code, "jjHisiFlg": flag,
    }
    return session.post(LIST_ACTION, data=data).text


def _link_kind(href: str) -> str:
    name = href.rsplit("/", 1)[-1].lower()
    if name.endswith(".pdf"):
        return "pdf"
    if "ixbrl" in name or name.endswith(".zip"):
        return "xbrl"
    if "qualitative" in name:
        return "attachment_html"
    return "summary_html"


def parse_delisted_on(html: str) -> str:
    """上場廃止した会社のページから上場廃止日を取る。上場中の会社なら空。"""
    i = html.find("上場廃止日")
    if i < 0:
        return ""
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})", re.sub(r"<[^>]+>", " ", html[i:i + 3000]))
    return "-".join(m.groups()) if m else ""


def parse_disclosures(html: str, code: str) -> list[dict]:
    """会社ページから適時開示の行 (DISCLOSURE_CATEGORIES の区分) を取り出す。"""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find_all("tr", id=re.compile(r"^\d{4}_\d+$")):
        category = tr["id"].split("_")[0]
        if category not in DISCLOSURE_CATEGORIES:
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        links = {}
        for a in tr.find_all("a", href=True):
            links.setdefault(_link_kind(a["href"]), HOST + a["href"] if a["href"].startswith("/") else a["href"])
        title_a = tds[1].find("a")
        pdf = links.get("pdf", "")
        out.append({
            "code": code,
            "date": tds[0].get_text(strip=True).replace("/", "-"),
            "category": category,
            "category_name": DISCLOSURE_CATEGORIES[category],
            "title": " ".join((title_a or tds[1]).get_text(" ", strip=True).split()),
            "doc_id": pdf.rsplit("/", 1)[-1].removesuffix(".pdf") if pdf else "",
            "pdf_url": pdf,
            "xbrl_url": links.get("xbrl", ""),
            "summary_html_url": links.get("summary_html", ""),
            "attachment_html_url": links.get("attachment_html", ""),
        })
    return out
