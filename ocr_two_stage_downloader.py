# -*- coding: utf-8 -*-
"""
二階段瀏覽器 + 商品描述圖片 URL 擷取 + 極小下載器

用途：
1) 第一階段人工登入 Edge（關完瀏覽器，清鎖）；
2) 第二階段以 9222 remote debugging 由 Selenium 接手；
3) 前往指定 Shopee 商品頁，僅在【商品描述】區塊抓 <picture> 內圖片 URL（優先 2x）；
4) 下載到 OCR 腳本的 INPUT_DIR；
5) （選擇）呼叫 OCR_OR1.py 的 run_pipeline() 直接跑 OCR。

需求：
- 與 OCR_OR1.PY 放在同一資料夾（本檔會 import 其中的 run_pipeline, INPUT_DIR, OUTPUT_DIR, MODEL_DIR）。
- Windows + Edge + 對應版 msedgedriver.exe（放同層或在 PATH）。
- Python 套件：selenium, easyocr（OCR 在 OCR_OR1 內）。

使用：
- 將 TEST_GRAB_URLS_ONLY = False、AUTO_DOWNLOAD_AND_OCR = True，即可：登入→抓 URL→下載→跑 OCR。
- 預設只做抓 URL（不下載不 OCR）。
"""
import os
import re
import sys
import time
import random
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# ==== 匯入你既有的 OCR 腳本 ====
try:
    from OCR_OR import run_pipeline, INPUT_DIR, OUTPUT_DIR, MODEL_DIR  # 需與本檔同層
except Exception as e:
    print("[WARN] 無法從 OCR_OR1 匯入：", e)
    # 若匯入失敗，提供備援的硬路徑（請依你的環境調整）
    INPUT_DIR  = r"C:\Users\pg.hsu\Desktop\chatgpt\vs code\look auto\myenv\DEFAULT_INPUT"
    OUTPUT_DIR = r"C:\Users\pg.hsu\Desktop\chatgpt\vs code\look auto\myenv\DEFAULT_OUTPUT"
    MODEL_DIR  = r"C:\Users\pg.hsu\.EasyOCR\model"
    run_pipeline = None
    

# ---- 你要求的硬路徑常數 ----
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
USER_DATA_DIR  = str(Path.home() / "AppData/Local/Microsoft/Edge/User Data")
PROFILE_DIR    = "Profile 1"
DEBUG_PORT     = 9222
DRIVER_NAME    = "msedgedriver.exe"
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DRIVER_PATH    = os.path.join(BASE_DIR, DRIVER_NAME)

# ---- 行為開關 ----
TEST_GRAB_URLS_ONLY   = False   # 只抓 URL 並列印（預設）
AUTO_DOWNLOAD_AND_OCR = True  # 抓到就下載到 INPUT_DIR，並（若可）呼叫 OCR
CLEAR_INPUT_BEFORE_DL = True   # 下載前清空 INPUT_DIR（避免舊檔混入）

# ========= Selenium 依賴 =========
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

WAIT_READY_TIMEOUT    = 25
WAIT_SELECTOR_TIMEOUT = 8.0
SCROLL_PAUSE          = 0.35

# ================= 工具 =================

def _find_edge():
    for p in EDGE_PATHS:
        if shutil.which(p) or os.path.exists(p):
            return p
    return None

def _ping_debug(port: int = DEBUG_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.getcode() == 200
    except Exception:
        return False

def _wait_debug_ready(port: int = DEBUG_PORT, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _ping_debug(port):
            return True
        time.sleep(0.25)
    return False

def _delete_singleton_locks():
    prof_dir = os.path.join(USER_DATA_DIR, PROFILE_DIR)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(prof_dir, name))
        except Exception:
            pass

# ========== 二階段瀏覽器 ==========

def prelogin_normal_mode(open_url: str = "https://shopee.tw") -> None:
    """第一階段：一般模式開 Edge 讓你登入，按 Enter 後暴力關閉並清鎖檔。"""
    edge = _find_edge()
    if not edge:
        raise FileNotFoundError("找不到 Edge，請檢查 EDGE_PATHS。")
    cmd = [edge,
           f'--user-data-dir={USER_DATA_DIR}',
           f'--profile-directory={PROFILE_DIR}',
           "--start-maximized",
           open_url]
    print("🔹 第一階段：請在 Edge 視窗完成蝦皮登入/驗證，完成後回到此視窗按 Enter…")
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        input("⏎ 確認登入完成後按 Enter（將暴力關閉 Edge）… ")
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "msedge.exe", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.2)
        _delete_singleton_locks()
        time.sleep(0.5)

def start_edge_9222() -> None:
    if _ping_debug(DEBUG_PORT):
        print("✅ Edge 9222 已就緒")
        return
    edge = _find_edge()
    if not edge:
        raise FileNotFoundError("找不到 Edge，請檢查 EDGE_PATHS。")
    cmd = [edge,
           f"--remote-debugging-port={DEBUG_PORT}",
           f'--user-data-dir={USER_DATA_DIR}',
           f'--profile-directory={PROFILE_DIR}',
           "--no-first-run",
           "--no-default-browser-check",
           "--disable-background-mode",
           "--start-maximized"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_debug_ready(DEBUG_PORT, timeout=20):
        raise RuntimeError("偵錯埠 9222 未就緒")

def attach_driver():
    driver_path = DRIVER_PATH
    if not os.path.exists(driver_path):
        found = shutil.which(DRIVER_NAME)
        if found:
            driver_path = found
    if not os.path.exists(driver_path):
        raise FileNotFoundError("找不到 msedgedriver.exe（同層或 PATH）。")
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    drv = webdriver.Edge(service=Service(executable_path=driver_path), options=opts)
    # 收斂分頁
    try:
        for h in drv.window_handles[:-1]:
            drv.switch_to.window(h); drv.close()
        drv.switch_to.window(drv.window_handles[-1])
    except Exception:
        pass
    return drv

# =========== 頁面操作 ===========

def wait_ready(driver, timeout=WAIT_READY_TIMEOUT):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.25)
    time.sleep(0.6)

def gentle_scroll(driver):
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.33);")
        time.sleep(SCROLL_PAUSE)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.66);")
        time.sleep(SCROLL_PAUSE)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.2)
    except Exception:
        pass

# 解析 srcset 取 2x（找不到則取最後一個，通常最大）

def _pick_2x(srcset_text: str):
    if not srcset_text:
        return None
    parts = [p.strip() for p in srcset_text.split(",") if p.strip()]
    for p in parts:
        if p.endswith(" 2x"):
            return p.rsplit(" ", 1)[0]
    return parts[-1].rsplit(" ", 1)[0] if parts else None

# 只在 h2=商品描述 的 section 裡抓 <picture> 內圖片

def grab_desc_image_urls(driver, timeout=12):
    from selenium.common.exceptions import TimeoutException
    section = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, '//section[.//h2[normalize-space()="商品描述"]]'))
    )
    pics = section.find_elements(By.TAG_NAME, "picture")
    urls = []
    for pic in pics:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pic)
        time.sleep(random.uniform(0.25, 0.6))
        try:
            srcset_webp = pic.find_element(By.XPATH, './/source[@type="image/webp"]').get_attribute("srcset") or ""
        except Exception:
            srcset_webp = ""
        img = pic.find_element(By.TAG_NAME, "img")
        t_end = time.time() + 2.0
        srcset_img = img.get_attribute("srcset") or ""
        src_img    = img.get_attribute("src") or ""
        while time.time() < t_end and not (srcset_webp or srcset_img or src_img):
            time.sleep(0.1)
            try:
                srcset_webp = pic.find_element(By.XPATH, './/source[@type="image/webp"]').get_attribute("srcset") or ""
            except Exception:
                pass
            srcset_img = img.get_attribute("srcset") or ""
            src_img    = img.get_attribute("src") or ""
        url = _pick_2x(srcset_webp) or _pick_2x(srcset_img) or src_img
        if url:
            low = url.lower()
            if not any(k in low for k in ("/icon", "/icons", "sprite", "emoji", "favicon", "logo", "badge")):
                urls.append(url)
    return urls

# =========== 極小下載器 ===========

MIME_EXT = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def build_cookie_header(driver, domain_hint="shopee.tw") -> str:
    try:
        cookies = driver.get_cookies()
    except Exception:
        return ""
    pairs = []
    for c in cookies:
        d = (c.get("domain") or "")
        if domain_hint in d or "susercontent.com" in d:
            pairs.append(f"{c['name']}={c['value']}")
    return "; ".join(pairs)


def _ext_from_url_or_mime(url: str, hdrs: dict) -> str:
    # 先看 URL path 副檔名
    path = urlparse(url).path
    base = os.path.basename(path)
    if "." in base:
        ext = "." + base.split(".")[-1]
        if len(ext) <= 5:
            return ext
    # 再看 Content-Type
    ctype = hdrs.get("Content-Type") or hdrs.get("content-type") or ""
    for k, v in MIME_EXT.items():
        if k in ctype:
            return v
    return ".img"


def download_images(urls, dest_dir, referer: str = "https://shopee.tw/", cookie: str = ""):
    os.makedirs(dest_dir, exist_ok=True)
    if CLEAR_INPUT_BEFORE_DL:
        for f in os.listdir(dest_dir):
            try:
                os.remove(os.path.join(dest_dir, f))
            except Exception:
                pass
    saved = []
    for i, url in enumerate(urls, 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": referer,
        })
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
                hdrs = {k: v for k, v in resp.getheaders()}
                ext  = _ext_from_url_or_mime(url, hdrs)
        except Exception as e:
            print(f"[DL-ERR] {i:02d} {url} -> {e}")
            continue
        fname = f"{i:02d}_desc{ext}"
        fpath = os.path.join(dest_dir, fname)
        try:
            with open(fpath, "wb") as f:
                f.write(data)
            print(f"[DL] {fname}  {len(data)/1024:.1f} KB")
            saved.append(fpath)
        except Exception as e:
            print(f"[WRITE-ERR] {fname} -> {e}")
    return saved

# =========== 主程式 ===========

def run_two_stage_and_process(test_url: str):
    # 第一階段：人工登入（暴力關閉）
    prelogin_normal_mode("https://shopee.tw")
    # 第二階段：起 9222 並附掛
    start_edge_9222()
    driver = attach_driver()
    try:
        driver.get(test_url)
        wait_ready(driver); gentle_scroll(driver)
        urls = grab_desc_image_urls(driver)
        print("\n=== 商品描述區塊圖片 URL（優先 2x / 過濾 icon） ===")
        if not urls:
            print("（沒抓到，可能尚未載入或頁面落在驗證頁）")
        for i, u in enumerate(urls, 1):
            print(f"{i:02d}. {u}")
        if TEST_GRAB_URLS_ONLY:
            return urls, []
        # 下載
        cookie_hdr = build_cookie_header(driver)
        saved = download_images(urls, INPUT_DIR, referer=test_url, cookie=cookie_hdr)
        return urls, saved
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    # 測試商品頁（請改成你要測的 URL）
    TEST_URL = "https://shopee.tw/product/1541867478/29938463826"

    urls, saved = run_two_stage_and_process(TEST_URL)

    if not TEST_GRAB_URLS_ONLY and AUTO_DOWNLOAD_AND_OCR:
        if run_pipeline is None:
            print("[WARN] 找不到 run_pipeline；請確認 OCR_OR1.PY 與本檔同層，或手動啟動 OCR。")
        else:
            print("\n▶ 開始 OCR：", INPUT_DIR)
            run_pipeline(INPUT_DIR, OUTPUT_DIR, MODEL_DIR, gt=None)
    else:
        if TEST_GRAB_URLS_ONLY:
            print("\n（目前 TEST_GRAB_URLS_ONLY=True：僅輸出 URL，不下載、不跑 OCR）")
        elif not saved:
            print("\n（未下載任何圖片，略過 OCR）")
