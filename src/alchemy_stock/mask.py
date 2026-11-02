"""LLM に読ませる前に、開示の文字から社名・証券コード・年を伏せる (research/stage2a_llm_trial/PREREG.md)。"""
from __future__ import annotations

import re
import unicodedata

MARKS = re.compile(r"\*\*|~~|`")
CORP = re.compile(r"株式会社|\(株\)")
EN_SUFFIX = re.compile(r"[,.\s]*(CO\.?|CORPORATION|CORP\.?|INC\.?|LTD\.?|LIMITED|HOLDINGS|K\.K\.)[,.\s]*$", re.I)
CONTACTS = [
    (re.compile(r"(代表者|問合せ先責任者|問い合わせ先責任者|責任者)[^\n(（]*"), r"\1 (伏せ字) "),
    (re.compile(r"TEL[.:]?\s*[\d\s()\-‐—]{6,}", re.I), "TEL XX "),
    (re.compile(r"(?<!\d)0\d{1,4}\s*-\s*\d{1,4}\s*-\s*\d{3,4}(?!\d)"), "XX-XXXX-XXXX"),
    (re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.]+"), "(URL)"),
]
TAGS = re.compile(r"</?(u|b|mark)>|<br\s*/?>", re.I)
# 年は書き方の揺れ (数字の間の空白、表のマス目での分断) が多いので、強めに伏せる
YEAR_PATTERNS = [
    (re.compile(r"(令和|平成|昭和)\s*(元|\d(\s*\d)?)?"), "X"),
    (re.compile(r"(?<![\d,.])(19|20)\d{2}(?![\d,.])"), "X"),
    (re.compile(r"(?<![\dA-Za-z])FY\s*X?\d{0,2}(?!\d)", re.I), "FYX"),
]


def clean(text: str) -> str:
    """pymupdf4llm の太字・取り消し線・下線などの印を消し、全角・半角をそろえる。"""
    return unicodedata.normalize("NFKC", TAGS.sub(" ", MARKS.sub("", text)))


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


def drop_header(text: str, limit: int = 1500) -> str:
    """題名 (「…のお知らせ」) より前の、会社名・代表者・問い合わせ先・日付の部分を落とす。"""
    m = re.search(r"お\s*知\s*ら\s*せ", text[:limit])
    return text[text.rfind("\n", 0, m.start()) + 1:] if m else text


def mask(text: str, names: list[str], code: str) -> str:
    text = drop_header(text)
    for n in names:  # 字の間に空白が入っていても拾う
        text = re.sub(r"\s*".join(map(re.escape, n.replace(" ", ""))), "当社", text, flags=re.I)
    text = re.sub(r"当社\s*株式会社|株式会社\s*当社", "当社", text)
    for pattern, repl in CONTACTS:
        text = pattern.sub(repl, text)
    code4 = code[:4]
    text = re.sub(rf"(?<![\d,]){re.escape(code4)}(?![\d,])", "XXXX", text)
    for pattern, repl in YEAR_PATTERNS:
        text = pattern.sub(repl, text)
    return text
