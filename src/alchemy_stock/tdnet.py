"""TDnet 適時開示情報閲覧サービス (直近31日分、時刻つき) の一覧を読む。

一覧は日付ごとに I_list_001_YYYYMMDD.html, I_list_002_... と100件ずつのページに分かれている。
PDF のファイル名 (doc_id) は東証上場会社情報サービスと同じ番号なので、2つを突き合わせられる。
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .http import PoliteSession

BASE = "https://www.release.tdnet.info/inbs/"


def list_url(day: date, page: int) -> str:
    return f"{BASE}I_list_{page:03d}_{day:%Y%m%d}.html"


def _text(td) -> str:
    return td.get_text(" ", strip=True).replace("　", " ").strip() if td else ""


def parse_list_page(html: str, day: date) -> tuple[list[dict], list[int]]:
    """1ページ分の行と、その日にあるページ番号の一覧を返す。"""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    table = soup.find("table", id="main-list-table")
    for tr in table.find_all("tr") if table else []:
        cells = {c: tr.find("td", class_=c) for c in
                 ("kjTime", "kjCode", "kjName", "kjTitle", "kjXbrl", "kjPlace", "kjHistroy")}
        if not cells["kjTime"]:
            continue
        a = cells["kjTitle"].find("a") if cells["kjTitle"] else None
        pdf = a["href"] if a and a.has_attr("href") else ""
        xbrl_a = cells["kjXbrl"].find("a") if cells["kjXbrl"] else None
        rows.append({
            "date": day.isoformat(),
            "time": _text(cells["kjTime"]),
            "code": _text(cells["kjCode"]),
            "name": _text(cells["kjName"]),
            "title": _text(cells["kjTitle"]),
            "doc_id": pdf.rsplit("/", 1)[-1].removesuffix(".pdf"),
            "pdf_url": BASE + pdf if pdf else "",
            "xbrl_url": BASE + xbrl_a["href"] if xbrl_a and xbrl_a.has_attr("href") else "",
            "exchange": _text(cells["kjPlace"]),
            "history": _text(cells["kjHistroy"]),
        })
    pages = sorted({int(p) for p in re.findall(rf"I_list_(\d{{3}})_{day:%Y%m%d}\.html", html)} | {1})
    return rows, pages


def fetch_day(session: PoliteSession, day: date) -> list[dict]:
    """その日の全ページを読む。ページが無い日 (404) は空を返す。"""
    r = session.get(list_url(day, 1), allow_404=True)
    if r is None:
        return []
    rows, pages = parse_list_page(r.text, day)
    for page in pages:
        if page == 1:
            continue
        r = session.get(list_url(day, page), allow_404=True)
        if r is not None:
            rows += parse_list_page(r.text, day)[0]
    return rows
