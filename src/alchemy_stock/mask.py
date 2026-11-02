"""LLM に読ませる前に、開示の文字から社名・証券コード・年を伏せる (research/stage2a_llm_trial/PREREG.md)。"""
from __future__ import annotations

import re
import unicodedata

MARKS = re.compile(r"\*\*|~~")
CORP = re.compile(r"株式会社|\(株\)")
EN_SUFFIX = re.compile(r"[,.\s]*(CO\.?|CORPORATION|CORP\.?|INC\.?|LTD\.?|LIMITED|HOLDINGS|K\.K\.)[,.\s]*$", re.I)
YEAR_PATTERNS = [
    (re.compile(r"(令和|平成|昭和)\s*(\d{1,2}|元)\s*年"), "X年"),
    (re.compile(r"(?<!\d)(19|20)\d{2}(?=\s*(年|/|\.|-|年度))"), "X"),
    (re.compile(r"(?<![\dA-Za-z])FY\s*(19|20)?\d{2}(?!\d)", re.I), "FYX"),
]


def clean(text: str) -> str:
    """pymupdf4llm の太字・取り消し線の印を消し、全角・半角をそろえる。"""
    return unicodedata.normalize("NFKC", MARKS.sub("", text))


def name_variants(*names: str | None) -> list[str]:
    """社名の書き方の揺れ (株式会社の有無、英語名の CO.,LTD. の有無) を、長いものから並べる。"""
    out = set()
    for name in names:
        if not name:
            continue
        n = unicodedata.normalize("NFKC", name).strip()
        out.add(n)
        bare = CORP.sub("", n).strip()
        out.add(bare)
        while (shorter := EN_SUFFIX.sub("", bare).strip()) != bare:
            bare = shorter
        out.add(bare)
    return sorted((v for v in out if len(v) >= 2), key=len, reverse=True)


def mask(text: str, names: list[str], code: str) -> str:
    for n in names:
        text = re.sub(re.escape(n), "当社", text, flags=re.I)
    code4 = code[:4]
    text = re.sub(rf"(?<![\d,]){re.escape(code4)}(?![\d,])", "XXXX", text)
    for pattern, repl in YEAR_PATTERNS:
        text = pattern.sub(repl, text)
    return text
