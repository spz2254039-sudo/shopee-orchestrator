# T4｜型號備援模組 v5（白名單修正 + download-only 描述圖支援）
# 變更點：
# 1) 沿用 v3 架構：
#    - 每次呼叫建立 {work_root}/desc_ocr/item_NN/{INPUT|OUTPUT}
#    - INPUT 每次清空、OUTPUT 保留歷史
#
# 2) 延續 v4：
#    - 新增 confusion_whitelist_fix()
#    - 修正常見 OCR 符號誤判（$/ → SY、$ → S、/ → Y）
#
# 3) v5 新增：
#    - download_desc_images_only()
#      * 僅下載商品描述區圖片（不執行 OCR、不寫 models_found.txt）
#      * 下載後重新命名為 desc_01, desc_02, ...
#      * 回傳穩定排序後的本地圖片路徑清單
#
# 4) 對外介面與既有 fallback_model_via_AB 簽名維持不變
#    - T0 依 t4_mode（off / ocr / download_only）決定呼叫路徑
#    - 既有 OCR 行為不受影響


from __future__ import annotations
import os, sys, shutil, inspect, re
from typing import List, Tuple

PRINT_PREFIX = "[T4]"
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# ---- A：描述區圖片工具（下載器）----
try:
    from ocr_two_stage_downloader import (
        grab_desc_image_urls,
        download_images,
        build_cookie_header,
    )
    _A_OK = True
except Exception as e:
    print(f"{PRINT_PREFIX} A_IMPORT_FAIL -> {e}")
    grab_desc_image_urls = download_images = build_cookie_header = None
    _A_OK = False

# ---- B：OCR pipeline（EasyOCR）----
try:
    from OCR_OR import run_pipeline, MODEL_DIR
    _B_OK = True
except Exception as e:
    print(f"{PRINT_PREFIX} B_IMPORT_FAIL -> {e}")
    run_pipeline = None
    MODEL_DIR = ""
    _B_OK = False

SUBDIR_NAME = "desc_ocr"
INPUT_NAME  = "INPUT"   # 實際使用：{base}/item_NN/INPUT
OUTPUT_NAME = "OUTPUT"  # 實際使用：{base}/item_NN/OUTPUT
MODELS_TXT  = "models_found.txt"
MAX_IMAGES  = 99

ITEM_DIR_RE = re.compile(r"^item_(\d{2})$")

def _ensure_dir(p: str) -> None:
    if not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)

def _clean_dir(p: str) -> None:
    """清空指定資料夾內容（不刪資料夾本身）。"""
    try:
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True); return
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            try:
                if os.path.isfile(fp) or os.path.islink(fp):
                    os.remove(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        try: os.makedirs(p, exist_ok=True)
        except Exception: pass

def _list_item_dirs(base_dir: str) -> List[Tuple[int, str]]:
    """列出既有 item_NN 目錄，回傳 [(NN, fullpath), ...]（NN 為 int）。"""
    items = []
    try:
        for name in os.listdir(base_dir):
            m = ITEM_DIR_RE.match(name)
            if m:
                nn = int(m.group(1))
                items.append((nn, os.path.join(base_dir, name)))
    except Exception:
        pass
    items.sort(key=lambda x: x[0])
    return items

def _next_item_dir(base_dir: str) -> Tuple[int, str]:
    """選出下一個 item_NN 目錄（兩位數遞增）。"""
    items = _list_item_dirs(base_dir)
    nxt = (items[-1][0] + 1) if items else 1
    nn = f"{nxt:02d}"
    path = os.path.join(base_dir, f"item_{nn}")
    _ensure_dir(path)
    return nxt, path

# ---- 新增：白名單修正器 ----
def _confusion_whitelist_fix(s: str) -> str:
    if not s:
        return s
    s2 = s
    s2 = re.sub(r'^\$/?-', "SY-", s2)  # 特例：$/-118 → SY-118
    s2 = re.sub(r'^\$/?', "SY", s2)    # 開頭 $/ → SY
    s2 = re.sub(r'^\$', "S", s2)       # 開頭 $ → S
    s2 = re.sub(r'^/', "Y", s2)        # 開頭 / → Y
    return s2

def _first_model_from_output(output_dir: str) -> str:
    try:
        p = os.path.join(output_dir, MODELS_TXT)
        if not os.path.isfile(p):
            print(f"{PRINT_PREFIX} B_NO_MODELS_TXT")
            return "查無"
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.strip() for ln in f.readlines()]
        if not lines:
            print(f"{PRINT_PREFIX} B_EMPTY_MODELS_TXT")
            return "查無"
        start_idx = 0
        if ("找到" in lines[0]) or ("未找" in lines[0]) or lines[0].startswith("#"):
            start_idx = 1
        for i in range(start_idx, len(lines)):
            if lines[i]:
                raw = lines[i]
                fixed = _confusion_whitelist_fix(raw)
                return fixed
        return "查無"
    except Exception as e:
        print(f"{PRINT_PREFIX} B_PARSE_FAIL -> {e}")
        return "查無"

def _limit_list(xs: List[str], n: int) -> List[str]:
    try:
        return list(xs[:max(0, int(n))])
    except Exception:
        return xs

def _call_run_pipeline_compat(input_dir: str, output_dir: str) -> None:
    """
    兼容呼叫：
    - 新版：run_pipeline(input, output, model_dir, gt=None, stop_on_first_hit=True)
    - 舊版：可能只有 (input, output, model_dir) 或 (input, output, model_dir, gt)
    """
    sig = inspect.signature(run_pipeline)
    params = list(sig.parameters.keys())
    print(f"{PRINT_PREFIX} B_SIG params={params}")
    try:
        if "stop_on_first_hit" in params:
            run_pipeline(input_dir, output_dir, MODEL_DIR, gt=None, stop_on_first_hit=True)
            return
        if "gt" in params and len(params) >= 4:
            run_pipeline(input_dir, output_dir, MODEL_DIR, None)
        else:
            run_pipeline(input_dir, output_dir, MODEL_DIR)
    except TypeError as te:
        print(f"{PRINT_PREFIX} B_TYPEWARN -> {te}; fallback plain call")
        run_pipeline(input_dir, output_dir, MODEL_DIR)

def fallback_model_via_AB(driver, referer_url: str, work_root: str) -> str:
    """
    A) 擷取描述區圖片 URL → 下載至 {work_root}/desc_ocr/item_NN/INPUT
    B) 跑 OCR pipeline 於 {work_root}/desc_ocr/item_NN/OUTPUT
    C) 讀 models_found.txt 回傳第一個候選；否則 "查無"
    """
    if driver is None or not isinstance(referer_url, str) or not isinstance(work_root, str):
        print(f"{PRINT_PREFIX} BAD_ARGS")
        return "查無"
    if not _A_OK:
        print(f"{PRINT_PREFIX} A_UNAVAILABLE")
        return "查無"
    if not _B_OK or (run_pipeline is None) or (not MODEL_DIR):
        print(f"{PRINT_PREFIX} B_UNAVAILABLE (run_pipeline={run_pipeline is not None}, MODEL_DIR='{MODEL_DIR}')")
        return "查無"

    # --- 基底與項次目錄 ---
    try:
        base_dir = os.path.join(work_root, SUBDIR_NAME)
        _ensure_dir(base_dir)
        _, item_dir = _next_item_dir(base_dir)   # ex: .../desc_ocr/item_01
        input_dir  = os.path.join(item_dir, INPUT_NAME)
        output_dir = os.path.join(item_dir, OUTPUT_NAME)
        _ensure_dir(input_dir)
        _ensure_dir(output_dir)
        # 規則：INPUT 清空；OUTPUT 不清空（保留歷史）
        _clean_dir(input_dir)
        print(f"{PRINT_PREFIX} IO_READY item='{os.path.basename(item_dir)}' base='{base_dir}'")
    except Exception as e:
        print(f"{PRINT_PREFIX} IO_FAIL -> {e}")
        return "查無"

    # --- A：抓圖 + 下載 ---
    try:
        print(f"{PRINT_PREFIX} A_START")
        urls = grab_desc_image_urls(driver) or []
        print(f"{PRINT_PREFIX} A_URLS n={len(urls)}")
        if not urls:
            return "查無"
        urls = _limit_list(urls, MAX_IMAGES)
        cookie_hdr = build_cookie_header(driver, "shopee.tw") or ""
        saved = download_images(urls, input_dir, referer=referer_url, cookie=cookie_hdr) or []
        print(f"{PRINT_PREFIX} A_OK saved={len(saved)} -> {input_dir}")
        if not saved:
            return "查無"
    except Exception as e:
        print(f"{PRINT_PREFIX} A_ERR -> {e}")
        return "查無"

    # --- B：OCR pipeline ---
    try:
        print(f"{PRINT_PREFIX} B_START MODEL_DIR='{MODEL_DIR}' OUT='{output_dir}'")
        _call_run_pipeline_compat(input_dir, output_dir)
        print(f"{PRINT_PREFIX} B_OK")
    except Exception as e:
        print(f"{PRINT_PREFIX} B_ERR -> {e}")
        return "查無"


    model = _first_model_from_output(output_dir)
    return model or "查無"


def download_desc_images_only(driver, referer_url: str, work_root: str) -> List[str]:
    """
    只下載描述區圖片到 {work_root}/desc_ocr/item_NN/INPUT（不做 OCR、不寫 models_found.txt）
    回傳依穩定順序命名後的本地路徑清單。
    """
    if driver is None or not isinstance(referer_url, str) or not isinstance(work_root, str):
        print(f"{PRINT_PREFIX} BAD_ARGS")
        return []
    if not _A_OK:
        print(f"{PRINT_PREFIX} A_UNAVAILABLE")
        return []

    try:
        base_dir = os.path.join(work_root, SUBDIR_NAME)
        _ensure_dir(base_dir)
        _, item_dir = _next_item_dir(base_dir)
        input_dir = os.path.join(item_dir, INPUT_NAME)
        _ensure_dir(input_dir)
        _clean_dir(input_dir)
        print(f"{PRINT_PREFIX} IO_READY item='{os.path.basename(item_dir)}' base='{base_dir}'")
    except Exception as e:
        print(f"{PRINT_PREFIX} IO_FAIL -> {e}")
        return []

    try:
        print(f"{PRINT_PREFIX} A_START")
        urls = grab_desc_image_urls(driver) or []
        print(f"{PRINT_PREFIX} A_URLS n={len(urls)}")
        if not urls:
            return []
        urls = _limit_list(urls, MAX_IMAGES)
        cookie_hdr = build_cookie_header(driver, "shopee.tw") or ""
        saved = download_images(urls, input_dir, referer=referer_url, cookie=cookie_hdr) or []
        print(f"{PRINT_PREFIX} A_OK saved={len(saved)} -> {input_dir}")
        if not saved:
            return []
    except Exception as e:
        print(f"{PRINT_PREFIX} A_ERR -> {e}")
        return []

    renamed_paths: List[str] = []
    for idx, src in enumerate(saved, start=1):
        ext = os.path.splitext(src)[1].lower() or ".png"
        dst_name = f"desc_{idx:02d}{ext}"
        dst = os.path.join(input_dir, dst_name)
        try:
            if os.path.abspath(src) != os.path.abspath(dst):
                if os.path.exists(dst):
                    os.remove(dst)
                os.rename(src, dst)
            renamed_paths.append(dst)
        except Exception:
            renamed_paths.append(src)
    return renamed_paths

