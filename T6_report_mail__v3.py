# report_mail.py @ v3 (minimal changes from v1)
# -*- coding: utf-8 -*-
"""
T6｜Mail 草稿輸出模組（v3：最小改動）
變更點（相較 v1）：
1) 主旨自動附上首段收文號「後三碼」，例如：請協助代轉通知…（405）
2) 仍採用 multipart/alternative（含 text/plain + text/html），HTML 具可點擊超連結

對外 API（與 v1 相同）：
- write_outlook_draft_eml_html(segments, case_dir, subject=None, to=None, cc=None, extra_html=None) -> str
"""

from typing import List, Dict, Tuple, Union
import os
import datetime
import base64
import html as _html

# ===== 工具 =====

def _ensure_dir(p: str) -> None:
    try:
        os.makedirs(p, exist_ok=True)
    except Exception as e:
        print(f"[T6][WARN] 建立資料夾失敗：{p} -> {e}")

def _sanitize_to_list(x: Union[str, List[str], None]) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        s = x.strip()
        return [s] if s else []
    try:
        out = []
        for i in x:
            s = (i or "").strip()
            if s:
                out.append(s)
        return out
    except Exception:
        s = str(x).strip()
        return [s] if s else []

def _join_addrs(xs: List[str]) -> str:
    # 不做花名/括號，只輸出逗號分隔 email 字串
    return ", ".join(xs) if xs else ""

def _rfc2047_utf8(s: str) -> str:
    """
    將非 ASCII 主旨或人名以 RFC 2047 Base64 編碼：=?UTF-8?B?...?=
    Outlook/各家客戶端相容性佳。
    """
    if not s:
        return ""
    try:
        s.encode("ascii")
        return s  # 純 ASCII 直接用
    except Exception:
        b = base64.b64encode(s.encode("utf-8")).decode("ascii")
        return f"=?UTF-8?B?{b}?="

def _collect_all_urls(segments: List[Dict]) -> List[str]:
    urls: List[str] = []
    for seg in (segments or []):
        for u in (seg.get("urls") or []):
            if isinstance(u, str) and u.strip():
                urls.append(u.strip())
    return urls

def _default_subject() -> str:
    return "請協助代轉通知予被檢舉之賣方並 e-mail 副知本分局"

def _default_to() -> List[str]:
    # 你常用：govletter@shopee.tw；若想預設留空，改為 return []
    return ["govletter@shopee.tw"]

def _default_cc() -> List[str]:
    # 可依需要調整或留空
    return []

def _build_plain_body(urls: List[str]) -> str:
    lines: List[str] = []
    lines.append("敬啟者：貴公司網站網路賣家銷售之商品，因被檢舉未標示商品檢驗標識，請協助代轉下述通知予被檢舉之賣方並 e-mail 副知本分局。")
    lines.append("商品網址:")
    if urls:
        for i, u in enumerate(urls, 1):
            lines.append(f"{i}. {u}")
    else:
        lines.append("(本批未擷取到商品網址)")
    lines.append("")
    lines.append("一、經濟部標準檢驗局公告之應施檢驗品目。應施檢驗商品須依商品檢驗法規定完成檢驗程序，商品本體並標示商品檢驗標識後，才可在國內市場上陳列或銷售。")
    lines.append("二、請依經濟部 106 年 11 月 28 日經標字第 10604605690 號公告於銷售網頁明確標示商品檢驗標識或完成檢驗程序證明之資訊：如您刊登銷售之商品屬標準檢驗局公告之應施檢驗商品，請於 3 天內在刊登網頁內容中明顯標示該商品之檢驗標識或完成檢驗程序證明之資訊；逾期未標示者，相關商品將由平臺業者移除下架。")
    lines.append("三、參考法條：違反商品檢驗法可能被處新臺幣 20 萬元以上 200 萬元以下罰鍰，請參考商品檢驗法第 60 條第 1 項、第 60 條之 2 規定。未符合檢驗規定之商品或非應施檢驗商品，如意圖欺騙他人而印有、貼附或註明標準檢驗局商品檢驗標識，將觸犯刑法第 255 條虛偽標記及販賣該商品罪，可處一年以下有期徒刑、拘役或 3 萬元以下罰金。")
    lines.append("四、相關疑問可至標準檢驗局網站（http://www.bsmi.gov.tw）/商品檢驗/應施檢驗商品專區查詢，或洽經濟部標準檢驗局臺南分局承辦人員：徐先生，聯絡電話：06-2234879 分機 607。")
    return "\r\n".join(lines)  # 直接用 CRLF

def _build_html_body(urls: List[str], extra_html: str = None) -> str:
    if urls:
        lis = "\r\n".join([f'<li><a href="{_html.escape(u)}">{_html.escape(u)}</a></li>' for u in urls])
        url_block = f'<p style="margin:0 0 6pt 0;">商品網址:</p><ol style="margin:0 0 12pt 24pt; padding-left:18pt;">{lis}</ol>'
    else:
        url_block = '<p style="margin:0 0 12pt 0;">商品網址: （本批未擷取到商品網址）</p>'

    extra = ""
    if extra_html:
        extra = f'\r\n  <div style="margin:0 0 12pt 0;">{extra_html}</div>'

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'新細明體',PMingLiU,'Microsoft JhengHei','Noto Sans TC',sans-serif;font-size:12pt;line-height:1.6;color:#000;">
  <p style="margin:0 0 12pt 0;">敬啟者：貴公司網站網路賣家銷售之商品，因被檢舉未標示商品檢驗標識，請協助代轉下述通知予被檢舉之賣方並 e-mail 副知本分局。</p>{extra}
  {url_block}
  <p style="margin:0 0 8pt 0;">一、經濟部標準檢驗局公告之應施檢驗品目。應施檢驗商品須依商品檢驗法規定完成檢驗程序，商品本體並標示商品檢驗標識後，才可在國內市場上陳列或銷售。</p>
  <p style="margin:0 0 8pt 0;">二、請依經濟部 106 年 11 月 28 日經標字第 10604605690 號公告於銷售網頁明確標示商品檢驗標識或完成檢驗程序證明之資訊：如您刊登銷售之商品屬標準檢驗局公告之應施檢驗商品，請於 3 天內在刊登網頁內容中明顯標示該商品之檢驗標識或完成檢驗程序證明之資訊；逾期未標示者，相關商品將由平臺業者移除下架。</p>
  <p style="margin:0 0 8pt 0;">三、參考法條：違反商品檢驗法可能被處新臺幣 20 萬元以上 200 萬元以下罰鍰，請參考商品檢驗法第 60 條第 1 項、第 60 條之 2 規定。未符合檢驗規定之商品或非應施檢驗商品，如意圖欺騙他人而印有、貼附或註明標準檢驗局商品檢驗標識，將觸犯刑法第 255 條虛偽標記及販賣該商品罪，可處一年以下有期徒刑、拘役或 3 萬元以下罰金。</p>
  <p style="margin:0 0 0 0;">四、相關疑問可至標準檢驗局網站（<a href="http://www.bsmi.gov.tw">http://www.bsmi.gov.tw</a>）/商品檢驗/應施檢驗商品專區查詢，或洽經濟部標準檢驗局臺南分局承辦人員：徐先生，聯絡電話：06-2234879 分機 607。</p>
</body>
</html>"""
    # 確保行尾為 CRLF
    return html.replace("\n", "\r\n")

def _build_mime_alt(to_hdr: str, cc_hdr: str, subject_hdr: str, plain_body: str, html_body: str) -> bytes:
    """
    建立 multipart/alternative（text/plain + text/html）
    - 強制 CRLF
    - Content-Transfer-Encoding: 8bit
    """
    boundary = "====ALT_PART_{}====".format(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    # Header 與 body 全用 \r\n
    parts = []
    parts.append("MIME-Version: 1.0")
    parts.append("X-Unsent: 1")
    parts.append(f"To: {to_hdr}")
    if cc_hdr:
        parts.append(f"Cc: {cc_hdr}")
    parts.append(f"Subject: {subject_hdr}")
    parts.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
    parts.append("")  # 空行分隔 header/body
    # text/plain
    parts.append(f"--{boundary}")
    parts.append('Content-Type: text/plain; charset="utf-8"')
    parts.append('Content-Transfer-Encoding: 8bit')
    parts.append("")
    parts.append(plain_body)
    # text/html
    parts.append(f"--{boundary}")
    parts.append('Content-Type: text/html; charset="utf-8"')
    parts.append('Content-Transfer-Encoding: 8bit')
    parts.append("")
    parts.append(html_body)
    # end
    parts.append(f"--{boundary}--")
    parts.append("")  # 最終 CRLF

    eml_str = "\r\n".join(parts)
    return eml_str.encode("utf-8", errors="strict")

# === v3 新增：取首段收文號後三碼 ===
def _first_receipt_tail3(segments: List[Dict]) -> str:
    try:
        r = (segments[0].get("receipt") or "").strip()
        return r[-3:] if r else ""
    except Exception:
        return ""

# ===== 對外 API =====

def write_outlook_draft_eml_html(
    segments: List[Dict],
    case_dir: str,
    subject: str = None,
    to: Union[str, List[str], None] = None,
    cc: Union[str, List[str], None] = None,
    extra_html: str = None
) -> str:
    """
    產出 draft_mail.eml（multipart/alternative；含 text/plain 與 text/html；X-Unsent: 1）
    失敗不丟例外，印出原因並回傳 out_path / 或空字串
    """
    out_path = ""
    try:
        if not isinstance(case_dir, str) or not case_dir.strip():
            print("[T6][ERR] case_dir 無效。")
            return ""
        _ensure_dir(case_dir)
        out_path = os.path.join(case_dir, "draft_mail.eml")

        # 收件人與副本
        to_list = _sanitize_to_list(to) if to is not None else _default_to()
        cc_list = _sanitize_to_list(cc) if cc is not None else _default_cc()
        to_hdr = _join_addrs(to_list)
        cc_hdr = _join_addrs(cc_list)

        # 主旨（v3：自動附上收文號後三碼）
        subj = subject if isinstance(subject, str) and subject.strip() else _default_subject()
        tail3 = _first_receipt_tail3(segments)
        if tail3:
            subj = f"{subj}（{tail3}）"  # 使用全形括號
        subject_hdr = _rfc2047_utf8(subj)

        # 內容
        urls = _collect_all_urls(segments)
        plain_body = _build_plain_body(urls)
        html_body  = _build_html_body(urls, extra_html=extra_html)

        # MIME（multipart/alternative）
        blob = _build_mime_alt(to_hdr, cc_hdr, subject_hdr, plain_body, html_body)

        # 落地
        with open(out_path, "wb") as f:
            f.write(blob)

        return out_path
    except Exception as e:
        print(f"[T6][ERR] 產生 .eml 失敗：{e}")
        # 儘量回傳已決定的 out_path（若有）
        return out_path or ""

# ===== 範例測試（可獨立執行） =====
if __name__ == "__main__":
    segments = [{
        "receipt": "1140055405",
        "lines": ["收文號：1140055405", "意見信箱-周先生"],
        "urls": [
            "https://shopee.tw/product/1166182016/28988664747",
            "https://shopee.tw/product/1541867478/29938463826"
        ],
        "insert_at": 2,
        "results": []
    }]
    case_dir = "./1140055405_case_v3"
    _ensure_dir(case_dir)
    out = write_outlook_draft_eml_html(
        segments,
        case_dir,
        subject=None,                          # 不給 → 用預設主旨 +（尾三碼）
        to=["govletter@shopee.tw"],            # 可給字串或清單；也可省略用預設
        cc=["tnbsmi@bsmi.gov.tw"],             # 可給字串或清單；不給則不輸出 Cc
        extra_html="<p>（此段為自訂補充說明，可依實際案件調整。）</p>"
    )
    print("EML 路徑：", out)
