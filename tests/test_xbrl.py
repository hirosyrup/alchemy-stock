"""XBRL の読み取りのテスト。実物は公開リポジトリに入れず、作りを真似た短いものを使う。"""
import pytest

from alchemy_stock.xbrl import parse, parse_number

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2008/inlineXBRL"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2011-07-31" xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:tse-ed-t="http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12"
 xmlns:tse-qcedjpsm-12340="http://www.xbrl.tdnet.info/jp/tse/tdnet/qc/edjp/sm/12340/20250101000000">
<body>
<ix:header><ix:resources>
<xbrli:context id="CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember"><xbrli:period>
<xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="CurrentYearInstant"><xbrli:period><xbrli:instant>2025-08-01</xbrli:instant></xbrli:period></xbrli:context>
</ix:resources></ix:header>
<ix:nonNumeric contextRef="CurrentYearInstant" name="tse-ed-t:DocumentName">第１四半期決算短信〔日本基準〕（連結）</ix:nonNumeric>
<ix:nonNumeric contextRef="CurrentYearInstant" name="tse-ed-t:SecuritiesCode">12340</ix:nonNumeric>
<ix:nonNumeric contextRef="CurrentYearInstant" format="ixt:booleanfalse" name="tse-ed-t:CorrectionOfDividendForecastInThisQuarter"/>
<ix:nonFraction contextRef="CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember" decimals="-6" format="ixt:numdotdecimal"
 name="tse-ed-t:NetSales" scale="6" unitRef="JPY">1,<span>234</span></ix:nonFraction>
<ix:nonFraction contextRef="CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember" decimals="3" format="ixt:numdotdecimal"
 name="tse-ed-t:ChangeInNetSales" scale="-2" sign="-" unitRef="Pure">15.2</ix:nonFraction>
<ix:nonFraction contextRef="CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember" name="tse-ed-t:OperatingIncome"
 unitRef="JPY" xsi:nil="true"/>
</body></html>"""


def test_parse_sample():
    r = parse(SAMPLE.encode())
    assert r["doc"] == {
        "report": "qcedjpsm",
        "document_name": "第１四半期決算短信〔日本基準〕（連結）",
        "securities_code": "12340",
        "fiscal_year_end": None,
        "n_facts": 3,
        "n_values": 2,
    }
    sales, change, op = r["facts"]
    assert sales["element"] == "NetSales" and sales["value"] == 1_234_000_000
    assert sales["period"] == "CurrentAccumulatedQ1Duration"
    assert sales["members"] == ["ConsolidatedMember", "ResultMember"]
    assert (sales["start"], sales["end"]) == ("2025-04-01", "2025-06-30")
    assert change["value"] == pytest.approx(-0.152)
    assert op["value"] is None
    flags = {t["element"]: t["value"] for t in r["texts"]}
    assert flags["CorrectionOfDividendForecastInThisQuarter"] == "false"
    assert next(t for t in r["texts"] if t["element"] == "SecuritiesCode")["end"] == "2025-08-01"


@pytest.mark.parametrize(
    "text,fmt,scale,sign,expected",
    [
        ("298,935", "ixt:numdotdecimal", "6", None, 298_935_000_000),
        ("0.9", "ixt:numdotdecimal", "-2", None, 0.009),
        ("15.2", "ixt:numdotdecimal", "-2", "-", -0.152),
        ("△1,234", "ixt:numdotdecimal", "6", None, -1_234_000_000),
        ("1.234,5", "ixt:numcommadecimal", "0", None, 1234.5),
        ("－", "ixt:numdotdecimal", "6", None, None),
        ("－", "ixt:fixed-zero", None, None, 0.0),
        ("", None, None, None, None),
        ("64.00", "ixt:numdotdecimal", None, None, 64.0),
    ],
)
def test_parse_number(text, fmt, scale, sign, expected):
    assert parse_number(text, fmt, scale, sign) == expected
