# report_xml.py @ v1
# 目的：輸出兩份 XML——便箋(-001) 與 回覆擬稿(-002)
# 公用 API：
#   write_bianzhen_xml_file(segments, case_dir, template_version="商品全數判定違規") -> str
#   write_reply_xml_file(segments, case_dir, template_version="商品全數判定違規") -> str
#
# 規格重點：
# - 不丟未捕獲例外；缺資料以「查無」或安全預設
# - UTF-8、無 BOM；XML 需 escape 特殊字元
# - ROC 日期省略規則：
#     首筆：YYY年M月D日
#     其後：同年同月 → 僅「D日」；同年不同月 → 「M月D日」；跨年 → 「YYY年M月D日」
# - item_count：全部 segments.urls 的總數（不去重）
# - categories_joined：全形引號包住、以頓號串接；來源為 [平台, 分類, URL] 的分類欄位（第二欄）
# - receiver_name：由「意見信箱-某某」裁出某某 + 固定「網路朋友您好：」
# - takedown_date_roc = notice_date_roc + 3 天

from typing import List, Dict, Tuple
import re, os, datetime

# ===== 工具 =====

def _xml_escape(s: str) -> str:
    s = str(s or "")
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _first_receipt(segments: List[Dict]) -> str:
    return (segments[0].get("receipt") or "") if segments else ""

def _first_receipt_year3(segments: List[Dict]) -> str:
    r = _first_receipt(segments)
    m = re.match(r"^(\d{3})", r or "")
    return m.group(1) if m else ""

def _now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# ---- ROC 日期工具 ----
def _parse_roc_compact(roc_yyyymmdd: str) -> Tuple[int,int,int]:
    # 1140805 -> (114, 8, 5)
    s = (roc_yyyymmdd or "").strip()
    m = re.match(r"^(\d{3})(\d{2})(\d{2})$", s)
    if not m: return 0,0,0
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def _roc_compact_to_date(roc_yyyymmdd: str):
    y,m,d = _parse_roc_compact(roc_yyyymmdd)
    if y==0: return None
    try:
        return datetime.date(y+1911, m, d)
    except Exception:
        return None

def _date_to_roc_full(d: datetime.date) -> str:
    return f"{d.year-1911}年{d.month}月{d.day}日"

def _join_report_dates_roc_with_omission(compacts: List[str]) -> str:
    """依規則串接 ROC 日期：
       首筆完整；同年同月→僅 D日；同年不同月→M月D日；跨年→YYY年M月D日"""
    if not compacts:
        return ""
    parts = []
    base = _roc_compact_to_date(compacts[0])
    parts.append(_date_to_roc_full(base))
    by0, bm0 = base.year, base.month
    for s in compacts[1:]:
        d = _roc_compact_to_date(s)
        if not d:
            continue
        if d.year == by0:  # 同年
            if d.month == bm0:   # 同年同月
                parts.append(f"{d.day}日")
            else:                # 同年不同月
                parts.append(f"{d.month}月{d.day}日")
        else:                    # 跨年
            parts.append(_date_to_roc_full(d))
    return "、".join(parts)

def _default_notice_and_takedown_dates(compacts: List[str]) -> Tuple[str,str]:
    """notice=第一個來文日期；takedown=+3 天；皆以 ROC 全形輸出"""
    if not compacts:
        today = datetime.date.today()
        return _date_to_roc_full(today), _date_to_roc_full(today + datetime.timedelta(days=3))
    d0 = _roc_compact_to_date(compacts[0])
    return _date_to_roc_full(d0), _date_to_roc_full(d0 + datetime.timedelta(days=3))

# ---- 由 segments 抽變數 ----
def _extract_all_roc_dates_compact(segments: List[Dict]) -> List[str]:
    ds, seen = [], set()
    for seg in segments or []:
        for line in (seg.get("lines") or []):
            m = re.match(r"^來文日期：\s*(\d{7,})", (line or "").strip())
            if m:
                v = m.group(1)[:7]  # 寬鬆容錯：超長也切 7 碼
                if v not in seen:
                    seen.add(v)
                    ds.append(v)
    return ds

def _extract_all_letter_ids(segments: List[Dict]) -> List[str]:
    out = []
    for seg in segments or []:
        for line in (seg.get("lines") or []):
            m = re.match(r"^來文號：\s*(\S+)", (line or "").strip())
            if m:
                out.append(m.group(1).strip())
    return out

def _collect_item_count(segments: List[Dict]) -> int:
    return sum(len(seg.get("urls") or []) for seg in segments or [])

def _parse_bracket_categories(lines: List[str]) -> List[str]:
    """抽出所有 [平台, 商品分類, url] 的第二欄分類（支援一行多個 []）"""
    cats = []
    text = "\n".join(lines or [])
    for m in re.finditer(r"\[([^\]]+)\]", text):
        seg = m.group(1)
        parts = [p.strip() for p in re.split(r"\s*,\s*", seg)]
        if len(parts) >= 2:
            cats.append(parts[1])
    return cats

def _collect_categories_joined(segments: List[Dict]) -> str:
    raws = []
    for seg in segments or []:
        raws.extend(_parse_bracket_categories(seg.get("lines") or []))
    # 保序去重
    seen, heads = set(), []
    for k in raws:
        if not k: 
            continue
        if k not in seen:
            seen.add(k); heads.append(k)
    # 全形引號 + 頓號
    return "、".join([f"「{h}」" for h in heads if h])

def _extract_sender_from_org(org: str) -> str:
    """來文機關：意見信箱-XXX -> 取最後一個 '-' 後"""
    if not org: return ""
    parts = [p for p in org.split("-") if p.strip()]
    return parts[-1].strip() if parts else org.strip()

def _collect_receiver_name(segments: List[Dict]) -> str:
    # 從首段抓「來文機關：意見信箱-XXX」
    lines0 = (segments[0].get("lines") or []) if segments else []
    joined = "\n".join(lines0)
    m = re.search(r"^來文機關：\s*(.+)$", joined, flags=re.M)
    who = _extract_sender_from_org(m.group(1).strip()) if m else ""
    return who

# ===== 模板：目前只提供「商品全數判定違規」版本 =====
_FIXED = {
    "full_title": "經濟部標準檢驗局臺南分局",
    "address": "70043臺南市中西區北門路1段179號",
    "org_code": "A13090400G",
    "unit": "市場監督科",
    "officer": "徐培智",
    "tel": "06-2234879",
    "ext": "607",
    "fax": "06-2236449",
    "email": "pg.hsu@bsmi.gov.tw",
    "category_no": "98353",
    "retention": "10",
    "decision_level": "一",
    "signature_bianzhen": "分局長　蔡　○　○",
    "signature_reply_tail": "經濟部標準檢驗局  敬復",
}

# ===== XML 組裝 =====

def _build_bianzhen_xml(segments: List[Dict]) -> str:
    head = _first_receipt(segments) or _now_stamp()
    year3 = _first_receipt_year3(segments) or "114"

    roc_dates = _extract_all_roc_dates_compact(segments)
    report_dates_joined = _join_report_dates_roc_with_omission(roc_dates)
    notice_date_roc, takedown_date_roc = _default_notice_and_takedown_dates(roc_dates)

    categories_joined = _collect_categories_joined(segments) or "「查無」"
    item_count = _collect_item_count(segments)

    # 受文者：保留「意見信箱-某某」全銜（XML 內同一值用於全銜與正式名稱）
    rcvr_full = (_collect_receiver_name(segments) or "查無")
    rcvr_full = f"意見信箱-{rcvr_full}"

    # XML
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<便箋 類別='' StyleSymbols='translated' NewDraft='N' DLWork='Y' LastModified='{_xml_escape(year3)}0715180651'>
  <發文機關列表>
    <發文機關>
      <全銜>{_xml_escape(_FIXED["full_title"])}</全銜>
      <機關地址>{_xml_escape(_FIXED["address"])}</機關地址>
      <機關代碼>{_xml_escape(_FIXED["org_code"])}</機關代碼>
      <承辦單位>{_xml_escape(_FIXED["unit"])}</承辦單位>
      <承辦人>{_xml_escape(_FIXED["officer"])}</承辦人>
      <聯絡電話>{_xml_escape(_FIXED["tel"])}</聯絡電話>
      <分機>{_xml_escape(_FIXED["ext"])}</分機>
      <傳真>{_xml_escape(_FIXED["fax"])}</傳真>
      <Email>{_xml_escape(_FIXED["email"])}</Email>
      <條戳 value='' value2=''/>
    </發文機關>
  </發文機關列表>
  <受文者列表 編號='2'>
    <文字>如行文單位</文字>
    <受文者 本別='正本' 識別碼='CZBLFEZV' CreateSN='0' 編號='1'>
      <全銜>{_xml_escape(rcvr_full)}</全銜>
      <正式名稱>{_xml_escape(rcvr_full)}</正式名稱>
      <機關代碼/><單位代碼/><姓名/><職稱/><郵遞區號/><地址/><FEP交換代碼/>
      <發文方式>人工傳遞</發文方式>
      <含附件>是</含附件>
      <櫃號/><匣道/><SYSID/><Email/><內部/><海外單位/><國別/><郵寄地區/><電子交換現況/>
    </受文者>
  </受文者列表>
  <函類別 代碼='便箋'/>
  <速別 代碼='普通件'/>
  <密等及解密條件或保密期限><密等 代碼=''/><解密條件或保密期限/></密等及解密條件或保密期限>
  <解密日期><年月日/></解密日期>
  <附件列表><文字/></附件列表>
  <陳核日期><年月日>{_xml_escape(takedown_date_roc)}</年月日></陳核日期>
  <發文日期><年月日/></發文日期>
  <主旨><文字>檢舉蝦皮賣家販賣沒有BSMI的商品</文字></主旨>
  <段落 段名=''>
    <文字/>
    <條列 序號='一、'><文字>檢舉人於{_xml_escape(report_dates_joined)}透過本局意見信箱反映蝦皮購物網站銷售之{_xml_escape(categories_joined)}等{item_count}項商品，疑未經檢驗。（查核結果詳如參考附件）</文字></條列>
    <條列 序號='二、'><文字>本(市場監督)科於{_xml_escape(notice_date_roc)}請平臺業者代轉通知被檢舉之賣家，請其3日內於銷售網頁明確標示商品檢驗標識或完成檢驗程序證明之資訊，否則將由平臺業者移除下架；惟於宣導期過後繫案商品皆未於銷售網頁明確標示完成檢驗之證明，爰{_xml_escape(takedown_date_roc)}請平臺業者將該等商品移除下架。</文字></條列>
    <條列 序號='三、'><文字>本案擬將處理情形以電子郵件回復檢舉人（如附稿），可否？謹請核示。</文字></條列>
  </段落>
  <署名 取代章戳=''>{_xml_escape(_FIXED["signature_bianzhen"])}</署名>
  <署名 取代章戳=''>　</署名>
  <公文擬辦方式>簽稿併陳</公文擬辦方式>
  <年度號>{_xml_escape(year3 or "114")}</年度號>
  <案次號>1</案次號>
  <分類號>{_xml_escape(_FIXED["category_no"])}</分類號>
  <保存年限>{_xml_escape(_FIXED["retention"])}</保存年限>
  <決行層次 決行層級='{_xml_escape(_FIXED["decision_level"])}'/>
  <公文文號>{_xml_escape(head)}</公文文號>
</便箋>'''.strip()

def _build_reply_xml(segments: List[Dict]) -> str:
    head = _first_receipt(segments) or _now_stamp()
    year3 = _first_receipt_year3(segments) or "114"

    roc_dates = _extract_all_roc_dates_compact(segments)
    report_dates_joined = _join_report_dates_roc_with_omission(roc_dates)
    categories_joined = _collect_categories_joined(segments) or "「查無」"
    item_count = _collect_item_count(segments)
    letter_ids_joined = "、".join(_extract_all_letter_ids(segments)) or "查無"

    who = _collect_receiver_name(segments) or "網路朋友"
    greeting = f"{who}網路朋友您好："

    notice_date_roc, takedown_date_roc = _default_notice_and_takedown_dates(roc_dates)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<意見信箱答復 類別='稿' DefaultSign='sign' StyleSymbols='translated' NewDraft='N' DLWork='Y' LastModified='{_xml_escape(year3)}0715180651'>
  <發文機關列表>
    <發文機關>
      <全銜>{_xml_escape(_FIXED["full_title"])}</全銜>
      <機關地址>{_xml_escape(_FIXED["address"])}</機關地址>
      <機關代碼>{_xml_escape(_FIXED["org_code"])}</機關代碼>
      <承辦單位>{_xml_escape(_FIXED["unit"])}</承辦單位>
      <承辦人>{_xml_escape(_FIXED["officer"])}</承辦人>
      <聯絡電話>{_xml_escape(_FIXED["tel"])}</聯絡電話>
      <分機>{_xml_escape(_FIXED["ext"])}</分機>
      <傳真>{_xml_escape(_FIXED["fax"])}</傳真>
      <Email>{_xml_escape(_FIXED["email"])}</Email>
      <條戳 value='' value2=''/>
    </發文機關>
  </發文機關列表>
  <受文者列表 編號='2'>
    <文字/>
    <受文者 本別='正本' 識別碼='CZBLFEZV' CreateSN='0' 編號='1'>
      <全銜>{_xml_escape("意見信箱-" + (who if who != "網路朋友" else "查無"))}</全銜>
      <正式名稱>{_xml_escape("意見信箱-" + (who if who != "網路朋友" else "查無"))}</正式名稱>
      <發文方式>人工傳遞</發文方式>
      <含附件>是</含附件>
    </受文者>
  </受文者列表>
  <速別 代碼='普通件'/>
  <密等及解密條件或保密期限><密等 代碼=''/><解密條件或保密期限/></密等及解密條件或保密期限>
  <附件列表><文字/></附件列表>
  <公文擬辦方式>簽稿併陳</公文擬辦方式>
  <陳核日期><年月日>{_xml_escape(takedown_date_roc)}</年月日></陳核日期>
  <年度號>{_xml_escape(year3 or "114")}</年度號>
  <案次號>1</案次號>
  <分類號>{_xml_escape(_FIXED["category_no"])}</分類號>
  <保存年限>{_xml_escape(_FIXED["retention"])}</保存年限>
  <段落 段名=''>
    <文字>{_xml_escape(greeting)}</文字>
    <條列 序號='' AlignParentContext='1'>
      <文字>您致本局意見信箱電子郵件（信件編號：{_xml_escape(letter_ids_joined)}），本局非常重視，有關您於{_xml_escape(report_dates_joined)}反映蝦皮購物網站銷售之{_xml_escape(categories_joined)}等{item_count}項商品疑未經檢驗一案，說明如下：</文字>
    </條列>
    <條列 序號='一、'><文字>針對您所反映之商品，本局已透過網站平臺業者通知賣家，告知銷售之商品如為本局公告之應施檢驗商品，應符合商品檢驗法之相關規定，商品本體若未正確標示商品檢驗標識及型號，必須下架停止銷售。</文字></條列>
    <條列 序號='二、'><文字>本局並將視需要購樣檢測或依相關規定進行後續追蹤調查，若確屬違規者，將依商品檢驗法或相關規定處理。</文字></條列>
    <條列 序號='三、'><文字>感謝您熱心反映及為維護消費者權益所付出的努力，若您尚有其它問題，請洽本案承辦人：本局臺南分局市場監督科徐先生，聯絡電話：06-2234879分機607。</文字></條列>
    <條列 序號='' AlignParentContext='1'><文字>謝謝您來信與指教，並祝您</文字></條列>
    <條列 序號='' AlignParentContext='1'><文字>健康愉快</文字></條列>
    <條列 序號='' AlignParentContext='1'><文字>{_xml_escape(_FIXED["signature_reply_tail"])}</文字></條列>
  </段落>
  <公文文號>{_xml_escape(head)}</公文文號>
</意見信箱答復>'''.strip()

# ===== 對外 API =====

def write_bianzhen_xml_file(segments: List[Dict], case_dir: str, template_version: str = "商品全數判定違規") -> str:
    """輸出 *-001.xml（便箋）；回傳實際路徑"""
    try:
        os.makedirs(case_dir, exist_ok=True)
    except Exception:
        pass
    head = _first_receipt(segments) or _now_stamp()
    xml = _build_bianzhen_xml(segments)
    out_path = os.path.join(case_dir, f"{head}-001.xml")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(xml)
    return out_path

def write_reply_xml_file(segments: List[Dict], case_dir: str, template_version: str = "商品全數判定違規") -> str:
    """輸出 *-002.xml（回覆擬稿）；回傳實際路徑"""
    try:
        os.makedirs(case_dir, exist_ok=True)
    except Exception:
        pass
    head = _first_receipt(segments) or _now_stamp()
    xml = _build_reply_xml(segments)
    out_path = os.path.join(case_dir, f"{head}-002.xml")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(xml)
    return out_path
