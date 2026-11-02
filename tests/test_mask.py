from alchemy_stock.mask import clean, mask, name_variants


def test_clean_removes_marks_and_normalizes():
    assert clean("**2** 019年 ~~１２３~~") == "2 019年 123"


def test_name_variants():
    v = name_variants("株式会社極洋", "KYOKUYO CO.,LTD.")
    assert "株式会社極洋" in v and "極洋" in v and "KYOKUYO" in v
    assert v == sorted(v, key=len, reverse=True)


def test_mask_names_code_years():
    names = name_variants("株式会社極洋", "KYOKUYO CO.,LTD.")
    text = "株式会社極洋 (コード番号 1301) 2019年3月期 令和元年11月8日 平成30年 FY2019 2019/11/08 売上 13,010百万円 Kyokuyo"
    got = mask(text, names, "13010")
    assert "極洋" not in got and "Kyokuyo" not in got
    assert "XXXX" in got and "13,010" in got
    assert "2019" not in got and "令和" not in got and "平成" not in got
    assert "X年3月期" in got and "X/11/08" in got and "FYX" in got


def test_mask_contacts_and_split_years():
    names = name_variants("NOK株式会社", None)
    text = clean("各位\n上場会社名 NOK 株式会社 代表者 取締役社長 土居 清志 (コード番号 7240) 問合せ先責任者 広報部部長 加来 隆志 (TEL 03-6891-0191)\n# 業績予想の修正に関するお知らせ\n2020 `年` 3 `月期` https://www.nok.co.jp/")
    got = mask(text, names, "72400")
    for leak in ("NOK", "土居", "加来", "6891", "2020", "nok.co.jp", "7240"):
        assert leak not in got
    assert "X 年 3 月期" in got


def test_drop_header_keeps_title():
    got = mask("各位\n会社名 当社 問合せ先 辻本 拓 (TEL 07 5-6 9 2-0)\n# 業績予想の修正に関するお知らせ\n本文", [], "99990")
    assert got.startswith("# 業績予想の修正に関するお知らせ") and "辻本" not in got
