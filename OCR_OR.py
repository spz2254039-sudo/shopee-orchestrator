# OCR_OR1.py｜最小改動：交叉讀圖 + 抽到型號即早停
# 來源：沿用你現版 OCR_OR.py 的架構/常數/輸出，只在 list_images 與 run_pipeline 內做極小補丁

import os
import sys
import gc
import csv
import re
import time
from typing import List, Tuple
from collections import OrderedDict

# ===== 低記憶體：鎖定數學庫/torch 執行緒，降低常駐記憶體 =====
import os as _os
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
_os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
try:
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.set_grad_enabled(False)
except Exception:
    pass

import numpy as np
from PIL import Image
import easyocr
import tracemalloc
import ctypes
from ctypes import wintypes

# -----------------------
# 硬路徑與固定參數（無 CLI）
# -----------------------
DEFAULT_INPUT  = r"C:\Users\pg.hsu\Desktop\chatgpt\vs code\look auto\myenv\DEFAULT_INPUT"
DEFAULT_OUTPUT = r"C:\Users\pg.hsu\Desktop\chatgpt\vs code\look auto\myenv\DEFAULT_OUTPUT"
DEFAULT_MODEL  = r"C:\Users\pg.hsu\.EasyOCR\model"

LANGS = ['ch_tra', 'en']   # 與現版一致
MAX_LONG_EDGE = 1000       # 與現版一致

REDLINE_MB     = 2000.0
EDGE_PRIMARY   = MAX_LONG_EDGE
EDGE_FALLBACK  = 832
DETAIL_PRIMARY = 1

def ensure_dir(p: str):
    if not os.path.exists(p):
        os.makedirs(p, exist_ok=True)

def print_config(input_dir, output_dir, model_dir):
    banner = (
        "\n=== OCR 配置 ===\n"
        f"INPUT_DIR : {input_dir}\n"
        f"OUTPUT_DIR: {output_dir}\n"
        f"MODEL_DIR : {model_dir}\n"
        f"LANGS     : {LANGS}\n"
        f"LONG_EDGE : {MAX_LONG_EDGE}\n"
        f"REDLINE_MB: {REDLINE_MB}\n"
        f"FALLBACK  : {EDGE_FALLBACK}\n"
        f"DETAIL    : {DETAIL_PRIMARY}\n"
        "=============\n"
    )
    print(banner)

def resolve_paths_from_defaults():
    ts = time.strftime("run_%Y%m%d_%H%M%S")
    out = os.path.join(DEFAULT_OUTPUT, ts)
    ensure_dir(out)

    if not os.path.isdir(DEFAULT_INPUT):
        print(f"[ERR] 輸入資料夾不存在：{DEFAULT_INPUT}")
        sys.exit(1)
    if not os.path.isdir(DEFAULT_MODEL) or not any(f.lower().endswith(".pth") for f in os.listdir(DEFAULT_MODEL)):
        print(f"[WARN] 模型目錄看起來不完整：{DEFAULT_MODEL}")

    print_config(DEFAULT_INPUT, out, DEFAULT_MODEL)
    return DEFAULT_INPUT, out, DEFAULT_MODEL

INPUT_DIR, OUTPUT_DIR, MODEL_DIR = resolve_paths_from_defaults()

# ---------- [MINIMAL PATCH #1]：交叉讀圖順序 ----------
def _interleave_ends(seq: List[str]) -> List[str]:
    """回傳 [0, -1, 1, -2, 2, -3, ...] 順序的序列副本"""
    out = []
    i, j = 0, len(seq) - 1
    while i <= j:
        out.append(seq[i]); i += 1
        if i <= j:
            out.append(seq[j]); j -= 1
    return out

def list_images(folder: str) -> List[str]:
    exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(exts)]
    files.sort()
    return _interleave_ends(files)   # ← 改這一行：交叉順序

def load_and_preprocess(path: str, max_long_edge: int) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("L")
        if max(im.size) > max_long_edge:
            im.thumbnail((max_long_edge, max_long_edge), Image.LANCZOS)
        arr = np.asarray(im, dtype=np.uint8)
    return arr

def avg_prob(results: List[Tuple]) -> float:
    if not results:
        return 0.0
    probs = [r[2] for r in results if len(r) >= 3 and isinstance(r[2], (float, int))]
    return float(sum(probs) / len(probs)) if probs else 0.0

# -----------------------
# Windows RSS（Working Set）
# -----------------------
class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]
psapi = ctypes.WinDLL("Psapi.dll")
kernel32 = ctypes.WinDLL("Kernel32.dll")
GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
GetCurrentProcess = kernel32.GetCurrentProcess
GetProcessMemoryInfo.restype = wintypes.BOOL

def rss_mb() -> float:
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        cb = ctypes.sizeof(counters)
        counters.cb = cb
        ok = GetProcessMemoryInfo(GetCurrentProcess(), ctypes.byref(counters), cb)
        return counters.WorkingSetSize / (1024 * 1024) if ok else -1.0
    except Exception:
        return -1.0

# -----------------------
# 型號抽取（與現版一致）
# -----------------------
CTX_KEYWORDS = r"(?:商品型號|型號|Model)"
# 放寬：加入 \$ 讓 $ 不會被切掉
MODEL_TOKEN  = r"[A-Za-z0-9\-\_\/\.\+\$]{2,24}"
NEG_UNIT_RE  = r"(?:mm|cm|mAh|mah|w|v|hz|°c|kg)\b"
NEG_MAT_SET  = {"ABS", "PVC", "PP"}

def extract_models_from_text(text: str) -> List[str]:
    ctx_pat      = re.compile(rf"{CTX_KEYWORDS}[^\n]{{0,40}}\n?[^\n]{{0,80}}", re.IGNORECASE)
    token_pat    = re.compile(MODEL_TOKEN)
    neg_unit_pat = re.compile(NEG_UNIT_RE, re.IGNORECASE)

    found = OrderedDict()
    for ctx in ctx_pat.findall(text):
        m = token_pat.search(ctx)
        if not m:
            continue
        token = m.group(0)
        if neg_unit_pat.search(token):
            continue
        if token.upper() in NEG_MAT_SET:
            continue
        found.setdefault(token, True)
    return list(found.keys())

def score_summary(models: List[str], merged_text: str, gt: str = None) -> str:
    NEG_UNIT = re.compile(NEG_UNIT_RE, re.IGNORECASE)
    NEG_MAT  = {m.upper() for m in NEG_MAT_SET}
    noise_unit = [m for m in models if NEG_UNIT.search(m)]
    noise_mat  = [m for m in models if m.upper() in NEG_MAT]
    lines = []
    lines.append(f"[CANDIDATES] {models if models else '無'}")
    lines.append(f"[NOISE] units_like={len(noise_unit)}  mats_like={len(noise_mat)}")
    if gt:
        hit1 = 1 if (models and models[0] == gt) else 0
        tp = sum(1 for m in models if m == gt)
        fp = len(models) - tp
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        lines.append(f"[GT] {gt}")
        lines.append(f"Hit@1 = {hit1}")
        lines.append(f"Precision = {precision:.2f}")
    return "\n".join(lines)

# -----------------------
# 主流程（與現版一致，但加入早停）
# -----------------------
def run_pipeline(input_dir: str, output_dir: str, model_dir: str, gt: str = None):
    t0 = time.time()
    ensure_dir(output_dir)

    merged_txt_path = os.path.join(output_dir, "merged_ocr.txt")
    csv_path        = os.path.join(output_dir, "images_ocr.csv")
    failed_path     = os.path.join(output_dir, "failed_slices.txt")
    log_path        = os.path.join(output_dir, "run.log")
    model_out_path  = os.path.join(output_dir, "models_found.txt")
    score_log_path  = os.path.join(output_dir, "score.log")

    images = list_images(input_dir)
    if not images:
        print(f"[WARN] 找不到圖片：{input_dir}")
        return

    tracemalloc.start()
    print("[INFO] 初始化 EasyOCR（CPU / 本地模型）...")
    reader = easyocr.Reader(LANGS, gpu=False, model_storage_directory=model_dir, download_enabled=False)

    failed = []
    peak_py_mb_overall = 0.0

    for p in [merged_txt_path, model_out_path, score_log_path, failed_path, log_path, csv_path]:
        if os.path.exists(p):
            try: os.remove(p)
            except: pass

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fcsv, \
         open(log_path, "w", encoding="utf-8") as flog, \
         open(merged_txt_path, "w", encoding="utf-8") as fmerged:

        writer = csv.writer(fcsv)
        writer.writerow(["index", "filename", "text_len", "avg_prob", "rss_mb_now", "py_peak_mb_now"])

        # ---------- [MINIMAL PATCH #2]：早停所需的狀態 ----------
        merged_so_far = ""
        model_found_early = None

        for idx, img_path in enumerate(images, 1):
            t1 = time.time()
            name = os.path.basename(img_path)
            try:
                rss_before = rss_mb()
                use_edge = EDGE_PRIMARY if (rss_before >= 0 and rss_before < REDLINE_MB) else EDGE_FALLBACK
                use_detail = DETAIL_PRIMARY if (rss_before >= 0 and rss_before < REDLINE_MB) else 0

                arr = load_and_preprocess(img_path, use_edge)
                results = reader.readtext(arr, detail=use_detail, paragraph=False)

                if use_detail == 1:
                    try:
                        texts = [r[1] for r in results if len(r) >= 2 and isinstance(r[1], str)]
                        ap = avg_prob(results)
                    except MemoryError:
                        del arr, results
                        gc.collect()
                        arr = load_and_preprocess(img_path, EDGE_FALLBACK)
                        results = reader.readtext(arr, detail=0, paragraph=False)
                        texts = [t for t in results if isinstance(t, str)]
                        ap = 0.0
                else:
                    texts = [t for t in results if isinstance(t, str)]
                    ap = 0.0

                text_joined = " ".join(texts).strip()

                # 流式寫入 & 早停嘗試
                fmerged.write(f"[{name}]\n{text_joined}\n\n")
                merged_so_far += f"[{name}]\n{text_joined}\n\n"

                # ---------- [MINIMAL PATCH #3]：即時抽型號並早停 ----------
                early_models = extract_models_from_text(merged_so_far)
                if early_models and not model_found_early:
                    with open(model_out_path, "w", encoding="utf-8") as fm:
                        fm.write("找到的型號：\n")
                        for m in early_models:
                            fm.write(m + "\n")
                    print("model =", early_models[0])
                    model_found_early = early_models[0]
                    fcsv.flush(); flog.flush(); fmerged.flush()
                    break  # 直接停止後續圖片 OCR

                current_rss = rss_mb()
                _, py_peak = tracemalloc.get_traced_memory()
                py_peak_mb = py_peak / (1024*1024)
                peak_py_mb_overall = max(peak_py_mb_overall, py_peak_mb)

                writer.writerow([idx, name, len(text_joined), f"{ap:.4f}", f"{current_rss:.2f}", f"{py_peak_mb:.2f}"])

                del arr, results, texts
                gc.collect()

                cost = time.time() - t1
                flog.write(f"[OK] {idx:04d} {name} in {cost:.2f}s, avg_prob={ap:.4f}, rss={current_rss:.2f}MB, py_peak={py_peak_mb:.2f}MB\n")
                print(f"[OK] {idx:04d} {name} ({cost:.2f}s, avg_prob={ap:.4f}, rss={current_rss:.2f}MB, py_peak={py_peak_mb:.2f}MB)")

                if (idx % 10) == 0:
                    fcsv.flush(); flog.flush(); fmerged.flush()

            except Exception as e:
                failed.append(name)
                flog.write(f"[ERR] {idx:04d} {name} -> {repr(e)}\n")
                print(f"[ERR] {idx:04d} {name}: {e}")
                try: del arr
                except: pass
                gc.collect()

    # 若未在迴圈中早停命中，維持原本收尾行為
    if not os.path.exists(model_out_path) or os.path.getsize(model_out_path) == 0:
        merged_text = open(merged_txt_path, "r", encoding="utf-8", errors="ignore").read()
        models = extract_models_from_text(merged_text)
        with open(model_out_path, "w", encoding="utf-8") as fm:
            fm.write("找到的型號：\n" if models else "未找到型號\n")
            for m in models:
                fm.write(m + "\n")
        if models:
            print("model =", models[0])
        else:
            print("model = NA")

    if failed:
        with open(failed_path, "w", encoding="utf-8") as ff:
            ff.write("\n".join(failed))

    _, py_peak_final = tracemalloc.get_traced_memory()
    peak_py_mb_overall = max(peak_py_mb_overall, py_peak_final / (1024*1024))
    tracemalloc.stop()

    cost_all = time.time() - t0

    merged_text = open(merged_txt_path, "r", encoding="utf-8", errors="ignore").read()
    models = extract_models_from_text(merged_text)
    summary = score_summary(models, merged_text, gt=gt)
    with open(score_log_path, "w", encoding="utf-8") as fs:
        fs.write(f"== SCORE {time.strftime('%Y-%m-%d %H:%M:%S')} ==\n")
        fs.write(summary + "\n== END ==\n")

    print(f"\n[SUMMARY] 圖片數：{len(list_images(input_dir))}，失敗：{len(failed)}，總耗時：{cost_all:.2f}s")
    print(f"[OUTPUT] merged_ocr.txt -> {merged_txt_path}")
    print(f"[OUTPUT] images_ocr.csv -> {csv_path}")
    print(f"[OUTPUT] models_found.txt -> {model_out_path}")
    print(f"[OUTPUT] score.log -> {score_log_path}")
    if failed:
        print(f"[OUTPUT] failed_slices.txt -> {failed_path}")

# -----------------------
if __name__ == "__main__":
    run_pipeline(INPUT_DIR, OUTPUT_DIR, MODEL_DIR, gt=None)
