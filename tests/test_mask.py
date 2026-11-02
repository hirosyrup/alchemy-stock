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
