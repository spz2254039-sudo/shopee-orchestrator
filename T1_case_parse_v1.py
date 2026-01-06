# case_parse.py @ v1
# 單一責任：解析 raw_text → segments；並依規則產出 Word 檔名與案件資料夾名稱
# 政策：不丟未捕捉例外；遇到缺漏回空容器或 "查無"

from __future__ import annotations
from typing import List, Dict, Tuple
import os, re, datetime, unicodedata

# ─────────────────────────────────────────────────────────
# 內部常數與工具
# ─────────────────────────────────────────────────────────

# 支援的蝦皮商品網址兩種樣式：
#   1) https://shopee.tw/product/<shop_id>/<item_id>
#   2) https://shopee.tw/…-i.<shop_id>.<item_id>
PRODUCT_PATTERNS = (r"/product/(\d+)/(\d+)", r"-i\.(\d+)\.(\d+)")

INVALID_CHARS = '<>:"/\\|?*'  # Windows 檔名非法字元

def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()

def _sanitize_filename(name: str, max_len: int = 150) -> str:
    name = _nfkc(name)
    for ch in INVALID_CHARS:
        name = name.replace(ch, "_")
    # 壓縮空白與底線
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("__", "_")
    if not name:
        name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return name[:max_len]

def _extract_urls_from_text(text: str) -> List[str]:
    # 寬鬆抓取網址（排除常見中文標點尾巴），再濾成 Shopee 商品頁
    urls = re.findall(r'https?://[^\s\]\)\}\>，。,；;、]+', text or "")
    urls = [u.rstrip('])}>，。,；;、') for u in urls]
    out = []
    for u in urls:
        if "shopee.tw" in u and any(re.search(p, u) for p in PRODUCT_PATTERNS):
            out.append(u)
    return out

def _split_segments_by_receipt(text: str) -> List[str]:
    parts = re.split(r'(?=^收文號：)', text or "", flags=re.M)
    return [p.strip("\n") for p in parts if p.strip()]

def _get_receipt_no(seg_text: str) -> str:
    m = re.search(r'^收文號：\s*([^\s]+)', seg_text or "", flags=re.M)
    return _nfkc(m.group(1)) if m else ""

def _find_insert_at(lines: List[str]) -> int:
    # 回傳應插入的「行索引」；找不到則回到倒數第二行（不越界）
    if not lines:
        return 0
    for i, line in enumerate(lines):
        if (line or "").strip().startswith("正本："):
            return i
    return max(0, len(lines) - 1)

def _extract_sender_from_org(org_line_value: str) -> str:
    # 來文機關：意見信箱-周先生 → 取最後一個 '-' 後
    s = _nfkc(org_line_value)
    parts = [p for p in s.split("-") if p.strip()]
    return parts[-1].strip() if parts else s

def _extract_org_from_lines(lines: List[str]) -> str:
    text = "\n".join(lines or [])
    m = re.search(r"^來文機關：\s*(.+)$", text, flags=re.M)
    return _nfkc(m.group(1)) if m else ""

def _extract_bracket_categories(lines: List[str]) -> List[str]:
    """
    擷取多個 [平台, 商品分類, URL] 片段中的「商品分類」（第二欄）。
    一段中可能出現多組 []。
    """
    cats = []
    txt = "\n".join(lines or [])
    for m in re.finditer(r"\[([^\]]+)\]", txt):
        seg = m.group(1)
        parts = [p.strip() for p in re.split(r"\s*,\s*", seg)]
        if len(parts) >= 2:
            cats.append(_nfkc(parts[1]))
    return cats

def _dedup_preserve_order(items: List[str]) -> List[str]:
    seen, out = set(), []
    for it in items or []:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out

# ─────────────────────────────────────────────────────────
# 對外 API 1：gather_all_segments
# ─────────────────────────────────────────────────────────

def gather_all_segments(text: str) -> List[Dict]:
    """
    解析 raw_text → segments（多筆收文號自動切段）
    結構：
      {
        "receipt": str,
        "lines": List[str],
        "urls": List[str],      # 依出現順序，不去重
        "insert_at": int,       # 插入點（預設為第一個「正本：」行）
        "results": List[Dict],  # 後續模組填寫
      }
    """
    try:
        seg_texts = _split_segments_by_receipt(text or "")
        segments: List[Dict] = []
        for seg in seg_texts:
            lines = (seg or "").splitlines()
            segments.append({
                "receipt": _get_receipt_no(seg) or "查無",
                "lines": lines,
                "urls": _extract_urls_from_text(seg),  # 保序不去重
                "insert_at": _find_insert_at(lines),
                "results": [],
            })
        return segments
    except Exception:
        # 容錯：任何錯誤都回空清單
        return []

# ─────────────────────────────────────────────────────────
# 對外 API 2：build_output_filename_from_segments_v2
# ─────────────────────────────────────────────────────────

def build_output_filename_from_segments_v2(segs: List[Dict]) -> str:
    """
    規則：
      單公文： 主收文號-關鍵詞1、關鍵詞2-來文人.docx
      多公文： 主收文號(併次號1、次號2…)-關鍵詞1、關鍵詞2-來文人.docx
    來源：
      - 主收文號：第一段 segments[0]['receipt']
      - 來文人：第一段「來文機關：意見信箱-xxx」→ 取 '-' 後
      - 關鍵詞：跨全部段落收集 [a, b, url] 的「b=商品分類」，去重保序，以頓號「、」串接
    """
    try:
        if not segs:
            # 沒有 segments 就以當下時間命名
            return _sanitize_filename(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")) + ".docx"

        # 主收文號
        receipt0 = _nfkc((segs[0] or {}).get("receipt") or "")
        # 併號
        others = [_nfkc((s or {}).get("receipt") or "") for s in (segs[1:] or []) if (s or {}).get("receipt")]
        if receipt0:
            head = f"{receipt0}(併{'、'.join([o for o in others if o])})" if others else receipt0
        else:
            head = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 來文人
        org0 = _extract_org_from_lines((segs[0] or {}).get("lines") or [])
        sender = _extract_sender_from_org(org0) if org0 else ""

        # 關鍵詞
        raw_keywords: List[str] = []
        for s in (segs or []):
            raw_keywords.extend(_extract_bracket_categories((s or {}).get("lines") or []))
        keywords = _dedup_preserve_order(raw_keywords)  # 保序去重
        kw_part = "、".join([k for k in keywords if k])

        parts = [head]
        if kw_part:
            parts.append(kw_part)
        if sender:
            parts.append(sender)

        name = "-".join(parts)
        return _sanitize_filename(name) + ".docx"
    except Exception:
        # 容錯退回時間戳
        return _sanitize_filename(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")) + ".docx"

# ─────────────────────────────────────────────────────────
# 對外 API 3：ensure_case_dir_by_wordname
# ─────────────────────────────────────────────────────────

def ensure_case_dir_by_wordname(base_dir: str, word_filename: str) -> str:
    """
    用 Word 檔名（去 .docx）建立案件資料夾。
    回傳該資料夾完整路徑；自動清理非法字元。
    """
    try:
        base = os.path.splitext(os.path.basename(word_filename or ""))[0]
        safe = _sanitize_filename(base)
        case_dir = os.path.join(base_dir or ".", safe)
        os.makedirs(case_dir, exist_ok=True)
        return case_dir
    except Exception:
        # 容錯：退回 base_dir
        try:
            os.makedirs(base_dir or ".", exist_ok=True)
        except Exception:
            pass
        return base_dir or "."

# ─────────────────────────────────────────────────────────
# 對外 API 4：parse_shop_item_id
# ─────────────────────────────────────────────────────────

def parse_shop_item_id(url: str) -> Tuple[str, str]:
    """
    從 Shopee 商品網址抽出 (shop_id, item_id)。
    支援：
      - /product/<shop>/<item>
      - -i.<shop>.<item>
    抽不到則回 ("", "")。
    """
    try:
        for p in PRODUCT_PATTERNS:
            m = re.search(p, url or "")
            if m:
                return m.group(1), m.group(2)
        return "", ""
    except Exception:
        return "", ""

# ─────────────────────────────────────────────────────────
# 簡單自測（可移除）
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    sample = """收文號：1140055405
來文日期：1140815
來文機關：意見信箱-周先生
來文號：2025081500068
受文者：經濟部標準檢驗局台南分局
附件數：0

主旨：市場監督 周先生 (申請流水號：1140815000069) 其他,填寫BSMI与售賣商品圖片認證資料不符合,請嚴查 網路平台

說明：

[蝦皮, 電風扇, https://shopee.tw/product/1166182016/28988664747]

正本：經濟部標準檢驗局
"""
    segs = gather_all_segments(sample)
    print("segments =", segs)
    wordname = build_output_filename_from_segments_v2(segs)
    print("word_filename =", wordname)
    print("case_dir =", ensure_case_dir_by_wordname(".", wordname))
    print("ids =", parse_shop_item_id("https://shopee.tw/product/1166182016/28988664747"))
    print("ids2=", parse_shop_item_id("https://shopee.tw/超長路徑-foo-i.1541867478.29938463826?x=y"))
