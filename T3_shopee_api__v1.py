# shopee_api.py @ v1
# 單一職責：穩定取得 Shopee get_pc payload，並抽取「品名、賣家帳號、BSMI、型號」
# 公開 API：
#   - fetch_get_pc(shop_id, item_id, cookie="")
#   - fetch_get_pc_via_page(driver, shop_id, item_id)
#   - fetch_get_pc_with_fallback(driver, shop_id, item_id)
#   - extract_title(payload) -> str
#   - extract_seller_account(payload) -> str
#   - extract_bsmi(payload) -> str
#   - extract_model(payload) -> str
#
# 設計原則：
#   * 不丟例外到上層；直連失敗回 {}，抽取失敗回 "查無"
#   * via_page 僅用 driver.execute_async_script(fetch) 沿用登入 Cookie
#   * 正則保守、避免誤抓（尤其「是否/有無」等布林欄位與單位/材質雜訊）
#
# 參考實作對齊（monolith）：
#   - fetch_get_pc             :contentReference[oaicite:4]{index=4}
#   - fetch_get_pc_via_page    :contentReference[oaicite:5]{index=5}
#   - extract_title            :contentReference[oaicite:6]{index=6}
#   - extract_seller_account   :contentReference[oaicite:7]{index=7}
#   - extract_bsmi             :contentReference[oaicite:8]{index=8}
#   - extract_model            :contentReference[oaicite:9]{index=9}

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, Optional

__all__ = [
    "fetch_get_pc",
    "fetch_get_pc_via_page",
    "fetch_get_pc_with_fallback",
    "extract_title",
    "extract_seller_account",
    "extract_bsmi",
    "extract_model",
]

# ---- 常數（依 Shopee 網站時區與最小 detail_level）----
_TZ_OFFSET_MINUTES = 480
_DETAIL_LEVEL = 0


# ---- 小工具 --------------------------------------------------------------

def _pick(obj: Any, path: str) -> Any:
    """
    安全取值：以 'a.b.c' 路徑存取巢狀 dict/list；任一步不存在回 None。
    """
    cur = obj
    for k in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and k.isdigit():
            cur = cur[int(k)]
        else:
            return None
    return cur


# ---- 直連 get_pc（可選帶 Cookie）-----------------------------------------
def fetch_get_pc(shop_id: str, item_id: str, cookie: str = "") -> Dict[str, Any]:
    """
    直連 https://shopee.tw/api/v4/pdp/get_pc?item_id=...&shop_id=...
    - 成功回 dict；任意例外/解析失敗回 {}
    - 可選帶 cookie 以提高成功率
    參考對齊：monolith fetch_get_pc:contentReference[oaicite:10]{index=10}
    """
    url = (
        "https://shopee.tw/api/v4/pdp/get_pc"
        f"?item_id={item_id}&shop_id={shop_id}"
        f"&tz_offset_minutes={_TZ_OFFSET_MINUTES}&detail_level={_DETAIL_LEVEL}"
    )
    headers = {
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0",
        "referer": "https://shopee.tw/",
    }
    if cookie:
        headers["cookie"] = cookie

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            return json.loads(text)
    except Exception as e:
        print(f"[T3] fetch_get_pc error: {e}")
        return {}


# ---- 透過瀏覽器頁面上下文的 JS fetch（沿用登入狀態）-----------------------
def fetch_get_pc_via_page(driver, shop_id: str, item_id: str) -> Dict[str, Any]:
    """
    使用 driver.execute_async_script 在頁面上下文呼叫 fetch(url, {credentials:'include'})
    - 成功回 dict；失敗回 {}
    參考對齊：monolith fetch_get_pc_via_page:contentReference[oaicite:11]{index=11}
    """
    js = r"""
    const shop = arguments[0], item = arguments[1], done = arguments[2];
    const url = `https://shopee.tw/api/v4/pdp/get_pc?item_id=${item}&shop_id=${shop}&tz_offset_minutes=480&detail_level=0`;
    fetch(url, { credentials: "include" })
      .then(r => r.json())
      .then(j => done(j))
      .catch(_ => done({}));
    """
    try:
        return driver.execute_async_script(js, shop_id, item_id) or {}
    except Exception as e:
        print(f"[T3] fetch_get_pc_via_page error: {e}")
        return {}


def fetch_get_pc_with_fallback(driver, shop_id: str, item_id: str) -> Dict[str, Any]:
    """
    先直連；若拿不到 data 或被擋/驗證，改以 via_page 取值。
    - 任何情況保證回 dict（可能為 {}）
    """
    payload = fetch_get_pc(shop_id, item_id)
    if not payload or not payload.get("data"):
        payload = fetch_get_pc_via_page(driver, shop_id, item_id)
    if not isinstance(payload, dict):
        payload = {}
    return payload


# ---- 欄位抽取（空值→"查無"）---------------------------------------------

def extract_title(payload: Dict[str, Any]) -> str:
    """
    優先 data.item.name → data.item.title
    參考對齊：monolith extract_title:contentReference[oaicite:12]{index=12}
    """
    v = _pick(payload, "data.item.name") or _pick(payload, "data.item.title") or ""
    v = str(v).strip()
    return v or "查無"


def extract_seller_account(payload: Dict[str, Any]) -> str:
    """
    data.shop_detailed.account.username → data.shop.username → data.item.account.username → data.shop.name
    參考對齊：monolith extract_seller_account:contentReference[oaicite:13]{index=13}
    """
    v = (
        _pick(payload, "data.shop_detailed.account.username")
        or _pick(payload, "data.shop.username")
        or _pick(payload, "data.item.account.username")
        or _pick(payload, "data.shop.name")
        or ""
    )
    v = str(v).strip()
    return v or "查無"


def extract_bsmi(payload: Dict[str, Any]) -> str:
    """
    1) 掃 attributes / product_attributes.attrs，找名稱含 'bsmi' 且排除「是否/有無」
    2) 仍無 → 從描述文字（含 rich_text_description.paragraph_list）以保守正則抓 Rxxxx 或「BSMI/商檢/檢驗標識/檢驗字號/型式認證/許可證號」
    參考對齊：monolith extract_bsmi:contentReference[oaicite:14]{index=14}
    """
    def _from_attrs(arr) -> str:
        for a in (arr or []):
            name  = (a.get("name") or a.get("attribute_name") or "").strip()
            value = (a.get("value") or a.get("attribute_value") or a.get("value_name") or "")
            if name and name.lower() == "bsmi":
                v = str(value).strip()
                if v:
                    return v
        for a in (arr or []):
            name  = (a.get("name") or a.get("attribute_name") or "").strip()
            value = (a.get("value") or a.get("attribute_value") or a.get("value_name") or "")
            if name and ("bsmi" in name.lower()):
                if any(k in name for k in ("是否", "有無")):
                    continue
                v = str(value).strip()
                if v:
                    return v
        return ""

    v = _from_attrs(_pick(payload, "data.item.attributes"))
    if not v:
        v = _from_attrs(_pick(payload, "data.product_attributes.attrs"))

    if not v:
        # 拼接一般描述 + rich_text 段落
        desc = str(_pick(payload, "data.item.description") or "")
        plist = _pick(payload, "data.item.rich_text_description.paragraph_list") or []
        for p in plist:
            t = p.get("text") or ""
            if t:
                desc += "\n" + str(t)

        m = (
            re.search(r"\bR\d{4,6}\b", desc, re.I)
            or re.search(r"(BSMI|商檢|檢驗標識|檢驗字號|型式認證|許可證號)[^:：\n]{0,8}[:：]?\s*([A-Za-z0-9\-]{5,30})", desc, re.I)
        )
        if m:
            v = m.group(0) if m.lastindex is None else m.group(2)
            v = str(v).strip()

    return v or "查無"


def extract_model(payload: Dict[str, Any]) -> str:
    """
    1) 掃 attributes / product_attributes.attrs 找名稱含「型號」，且排除「是否/有無」
    2) 仍無 → 從描述（含 rich_text 段落）以正則抓「型號/型式/型名/Model/Model No」
    參考對齊：monolith extract_model:contentReference[oaicite:15]{index=15}
    """
    def _from_attrs(arr) -> str:
        for a in (arr or []):
            name  = (a.get("name") or a.get("attribute_name") or "").strip()
            value = (a.get("value") or a.get("attribute_value") or a.get("value_name") or "").strip()
            if name and "型號" in name:
                if any(k in name for k in ("是否", "有無")):
                    continue
                if value:
                    return value
        return ""

    v = _from_attrs(_pick(payload, "data.item.attributes"))
    if not v:
        v = _from_attrs(_pick(payload, "data.product_attributes.attrs"))

    # 描述回退
    if not v:
        desc = str(_pick(payload, "data.item.description") or "")
        plist = _pick(payload, "data.item.rich_text_description.paragraph_list") or []
        for p in plist:
            t = p.get("text") or ""
            if t:
                desc += "\n" + str(t)

        patterns = [
            r"(?:型號|型式|型名)[^:：\n]{0,8}[:：]\s*([^\n\r，。,；;)】]{2,60})",
            r"(?:Model(?:\s*No\.?)?|Model)[^:：\n]{0,8}[:：]\s*([A-Za-z0-9][A-Za-z0-9 .+\-_/]{1,60})",
        ]
        for rx in patterns:
            m = re.search(rx, desc, re.I)
            if m:
                v = m.group(1).strip()
                # 收斂多重空白
                v = re.sub(r"\s{2,}", " ", v)
                break

    return v or "查無"


# ----（選用）簡易自測：僅列印抽取欄位，不驅動瀏覽器 -----------------------
if __name__ == "__main__":
    # 最小自測：只做直連（可自行填 cookie 提高成功率）
    # 正式端請由 orchestrator 呼叫 fetch_get_pc_with_fallback(driver, ...)
    TEST_SHOP = "1541867478"
    TEST_ITEM = "29938463826"
    payload = fetch_get_pc(TEST_SHOP, TEST_ITEM, cookie="")
    print("title        :", extract_title(payload))
    print("seller_acct  :", extract_seller_account(payload))
    print("bsmi         :", extract_bsmi(payload))
    print("model        :", extract_model(payload))
