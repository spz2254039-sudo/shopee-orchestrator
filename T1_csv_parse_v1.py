# T1_csv_parse_v1.py  # v1
# 責任：讀取「序號,商品網址」CSV → 產出 segments 結構（單一 segment）
#
# 期待 CSV 來源（由油猴腳本產生）格式：
#   序號,商品網址
#   1,https://shopee.tw/product/123456789/1111
#   2,https://shopee.tw/product/123456789/2222
#
# 輸出 segments：
# [
#   {
#     "receipt": "",
#     "lines": [],
#     "urls": [...],       # 依序列出所有有效網址
#     "insert_at": 0,
#     "results": [],
#     "seqs": [...],       # 對應原始「序號」（int）
#     "shop_id": "123456789",
#   }
# ]

from __future__ import annotations

from typing import List, Dict, Tuple
import csv
import os

from T1_case_parse_v1 import parse_shop_item_id


PRINT_PREFIX = "[T1-CSV]"


def _log(msg: str) -> None:
    print(f"{PRINT_PREFIX} {msg}")


def _normalize_header_cell(s: str) -> str:
    """
    標頭欄位名稱前處理：
      - 去掉 BOM
      - 去掉前後空白
    """
    if s is None:
        return ""
    return s.replace("\ufeff", "").strip()


def _locate_header_indexes(header: List[str]) -> Tuple[int, int]:
    """
    嘗試從 header 找出「序號」與「商品網址」欄位 index。
    若找不到對應欄位，回傳 (-1, -1)。
    """
    norm = [_normalize_header_cell(h) for h in header or []]

    seq_idx = -1
    url_idx = -1

    for i, name in enumerate(norm):
        if name == "序號" and seq_idx < 0:
            seq_idx = i
        elif name == "商品網址" and url_idx < 0:
            url_idx = i

    # 最小容錯：若完全找不到，試著用「id」「url」類字眼兜一個
    if seq_idx < 0:
        for i, name in enumerate(norm):
            if name.lower() in ("序號", "id", "index", "no", "num"):
                seq_idx = i
                break
    if url_idx < 0:
        for i, name in enumerate(norm):
            if "網址" in name or name.lower() in ("url", "link", "href"):
                url_idx = i
                break

    return seq_idx, url_idx


def gather_segments_from_csv(csv_path: str) -> List[Dict]:
    """
    讀取油猴腳本產出的 CSV，輸出單一 segment：

      {
        "receipt": "",
        "lines": [],
        "urls": [...],
        "insert_at": 0,
        "results": [],
        "seqs": [...],
        "shop_id": "123456789",
      }

    任何錯誤都以「印 log + 回傳空清單」方式容錯。
    """
    try:
        csv_path = (csv_path or "").strip()
        if not csv_path:
            _log("未提供 csv_path。")
            return []
        if not os.path.exists(csv_path):
            _log(f"檔案不存在：{csv_path}")
            return []

        urls: List[str] = []
        seqs: List[int] = []
        shop_ids = []

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                _log("CSV 無內容（連標頭都沒有）。")
                return []

            seq_idx, url_idx = _locate_header_indexes(header)
            if seq_idx < 0 or url_idx < 0:
                _log(f"找不到欄位 '序號' 或 '商品網址'，header={header}")
                return []

            row_idx = 1  # 包含標頭那列
            for row in reader:
                row_idx += 1
                if not row:
                    continue

                # 防止 row 長度不足
                if seq_idx >= len(row) and url_idx >= len(row):
                    _log(f"第 {row_idx} 列欄位不足，略過。")
                    continue

                seq_raw = row[seq_idx] if seq_idx < len(row) else ""
                url_raw = row[url_idx] if url_idx < len(row) else ""

                seq_raw = (seq_raw or "").strip()
                url_raw = (url_raw or "").strip()

                if not seq_raw and not url_raw:
                    # 全空列
                    continue

                # 解析序號
                try:
                    seq_val = int(seq_raw)
                except Exception:
                    _log(f"第 {row_idx} 列序號非整數（'{seq_raw}'），略過。")
                    continue

                if not url_raw:
                    _log(f"第 {row_idx} 列網址為空，略過。")
                    continue

                # 檢查是否為 Shopee 商品頁
                shop_id, item_id = parse_shop_item_id(url_raw)
                if not shop_id or not item_id:
                    _log(f"第 {row_idx} 列網址非合法 Shopee 商品頁，略過：{url_raw}")
                    continue

                urls.append(url_raw)
                seqs.append(seq_val)
                shop_ids.append(shop_id)

        if not urls:
            _log("CSV 中沒有任何有效商品網址。")
            return []

        # 檢查是否為單一賣場
        unique_shops = sorted(set(shop_ids))
        chosen_shop_id = unique_shops[0] if unique_shops else ""

        if len(unique_shops) > 1:
            _log(f"⚠️ 偵測到多個 shop_id：{unique_shops}，目前僅取第一個 {chosen_shop_id} 作為案件命名依據。")

        seg = {
            "receipt": "",
            "lines": [],
            "urls": urls,
            "insert_at": 0,
            "results": [],
            "seqs": seqs,
            "shop_id": chosen_shop_id,
        }

        _log(f"解析完成：共 {len(urls)} 筆商品網址，shop_id={chosen_shop_id or '未知'}")
        return [seg]

    except Exception as e:
        _log(f"gather_segments_from_csv 發生未預期錯誤：{e}")
        # 容錯：任何錯誤回空清單
        return []


if __name__ == "__main__":
    # 簡單自測（可以必要時關掉）
    path = input("請輸入測試 CSV 路徑（Enter 略過）：").strip()
    if path:
        segs = gather_segments_from_csv(path)
        print("segments =", segs)
