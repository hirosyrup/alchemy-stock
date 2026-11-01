"""読み取り部分のテスト。サイトの HTML は公開リポジトリに入れず、作りを真似た短い HTML で確かめる。"""
from datetime import date

from alchemy_stock.jpx_listed import parse_company_list, parse_delisted_on, parse_disclosures
from alchemy_stock.tdnet import parse_list_page

TDNET_HTML = """
<div class="pager-M" onClick="pagerLink('I_list_002_20261030.html')">2</div>
<table id="main-list-table">
<tr>
<td class="oddnew-L kjTime" noWrap>15:30</td>
<td class="oddnew-M kjCode" noWrap>12340</td>
<td class="oddnew-M kjName" noWrap>テスト工業　　</td>
<td class="oddnew-M kjTitle"><a href="140120261030000001.pdf">業績予想の修正に関するお知らせ</a></td>
<td class="oddnew-M kjXbrl"><div><a class="style002" href="081220261030000001.zip">XBRL</a></div></td>
<td class="oddnew-M kjPlace">東　　</td>
<td class="oddnew-R kjHistroy">　　</td>
</tr>
<tr>
<td class="evennew-L kjTime" noWrap>16:00</td>
<td class="evennew-M kjCode" noWrap>56780</td>
<td class="evennew-M kjName" noWrap>サンプル</td>
<td class="evennew-M kjTitle"><a href="140120261030000002.pdf">自己株式取得に係る事項の決定に関するお知らせ</a></td>
<td class="evennew-M kjXbrl"> </td>
<td class="evennew-M kjPlace">東</td>
<td class="evennew-R kjHistroy"></td>
</tr>
</table>
"""

COMPANY_LIST_HTML = """
<form name="JJK010030Form" method="POST" action="/tseHpFront/JJK010030Action.do">
<input type="hidden" name="Transition" value="Transition" disabled="disabled">
<input type="hidden" name="lstDspPg" value="1">
<input type="hidden" name="souKnsu" value="590">
<input type="hidden" name="jjHisiFlg" value="1">
<input type="hidden" name="ccJjCrpSelKekkLst_st[0].eqMgrCd" value="12340">
<input type="hidden" name="ccJjCrpSelKekkLst_st[0].eqMgrNm" value="テスト工業">
<input type="hidden" name="ccJjCrpSelKekkLst_st[0].szkbuNm" value="グロース">
<input type="hidden" name="ccJjCrpSelKekkLst_st[0].gyshDspNm" value="情報・通信業">
<input type="hidden" name="ccJjCrpSelKekkLst_st[0].dspYuKssnKi" value="3月">
<input type="button" onclick="gotoBaseJh('12340', '1');">
</form>
"""

DETAIL_HTML = """
<form name="JJK010040Form"></form>
<table>
<tr id="1101_0">
  <td align="center">2026/08/04</td>
  <td><div><a href="/disc/12340/140120260730000003.pdf">2027年3月期 第1四半期決算短信〔日本基準〕（連結)</a></div></td>
  <td><a href="/disc/12340/081220260730000003_tse-qcedjpsm-12340-ixbrl.htm">XBRL</a></td>
  <td></td>
  <td><a href="/disc/12340/081220260730000003_qualitative.htm">HTML</a></td>
</tr>
<tr id="1102_0">
  <td align="center">2026/07/01</td>
  <td><a href="/disc/12340/140120260701000004.pdf">業績予想の修正に関するお知らせ</a></td>
  <td>&nbsp;</td><td></td><td></td>
</tr>
<tr id="1105_0">
  <td align="center">2026/06/01</td>
  <td><a href="/disc/12340/140120260601000005.pdf">定時株主総会招集通知</a></td>
</tr>
</table>
"""


DELISTED_HTML = """
<form name="JJK010040Form"></form>
<table><tr><th>コード</th><th>ISINコード</th><th>上場廃止日</th><th>業種</th></tr>
<tr><td>13520</td><td>JP0000000000</td><td>2022/05/19</td><td>卸売業</td></tr></table>
<table>
<tr id="5101_0"><td>2022/05/13</td><td><a href="/disc/13520/140120220513000006.pdf">2022年3月期 決算短信</a></td></tr>
<tr id="5103_0"><td>2022/04/01</td><td><a href="/disc/13520/140120220401000007.pdf">お知らせ</a></td></tr>
<tr id="5104_0"><td>2022/03/01</td><td><a href="/disc/13520/140120220301000008.pdf">定款</a></td></tr>
</table>
"""


def test_tdnet_list_page():
    rows, pages = parse_list_page(TDNET_HTML, date(2026, 10, 30))
    assert pages == [1, 2]
    assert len(rows) == 2
    first = rows[0]
    assert first["time"] == "15:30"
    assert first["code"] == "12340"
    assert first["name"] == "テスト工業"
    assert first["doc_id"] == "140120261030000001"
    assert first["pdf_url"].endswith("/inbs/140120261030000001.pdf")
    assert first["xbrl_url"].endswith("/inbs/081220261030000001.zip")
    assert first["exchange"] == "東"
    assert rows[1]["xbrl_url"] == ""


def test_company_list():
    companies, total, fields = parse_company_list(COMPANY_LIST_HTML)
    assert total == 590
    assert companies == [{
        "code": "12340", "name": "テスト工業", "market": "グロース",
        "industry": "情報・通信業", "fiscal_month": "3月", "flag": "1",
    }]
    # 次のページ用の隠しフィールド: 無効化されたものと各行の分は含めない
    assert fields == {"lstDspPg": "1", "souKnsu": "590", "jjHisiFlg": "1"}


def test_disclosures_keep_only_timely_disclosure_categories():
    rows = parse_disclosures(DETAIL_HTML, "12340")
    assert [r["category"] for r in rows] == ["1101", "1102"]
    kessan = rows[0]
    assert kessan["date"] == "2026-08-04"
    assert kessan["category_name"] == "決算情報"
    assert kessan["doc_id"] == "140120260730000003"
    assert kessan["pdf_url"] == "https://www2.jpx.co.jp/disc/12340/140120260730000003.pdf"
    assert kessan["xbrl_url"].endswith("ixbrl.htm")
    assert kessan["attachment_html_url"].endswith("qualitative.htm")
    assert rows[1]["title"] == "業績予想の修正に関するお知らせ"
    assert rows[1]["xbrl_url"] == ""
    assert parse_delisted_on(DETAIL_HTML) == ""


def test_delisted_company_page():
    rows = parse_disclosures(DELISTED_HTML, "13520")
    assert [(r["category"], r["category_name"]) for r in rows] == [("5101", "決算情報"), ("5103", "その他")]
    assert parse_delisted_on(DELISTED_HTML) == "2022-05-19"
