# shopee_page.py @ v2，修改第一階段前清洗瀏覽器可開關模式
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
T2｜瀏覽器控制 – Shopee 商品頁操作模組

單一職責：
- 附掛既有 Edge (remote debugging 9222)
- 頁面就緒檢查與暖捲
- 讀取商品頁基本資訊（品名、賣家展示名、是否卡驗證）
- 產出三段截圖（頂部 → 商品規格 → 商品描述）

注意：
- 不解析文字、不呼叫 API；僅 Selenium 操作
- 出錯時不 raise 未捕捉例外，回傳空字串或安全預設
- EX 才是 Edge driver 的主導者；本模組只提供附掛與控制
"""
# === 新增：讀取 config 與安全清洗工具 ===
import platform

try:
    from T0_orchestrator_config import (
        CLEAN_EDGE_BEFORE_PRELOGIN,
        EDGE_PROCESS_NAMES,
        TASKKILL_FLAGS,
    )
except Exception:
    # 找不到設定時的安全預設
    CLEAN_EDGE_BEFORE_PRELOGIN = False
    EDGE_PROCESS_NAMES = ["msedge.exe", "msedgedriver.exe"]
    TASKKILL_FLAGS = ["/F", "/T"]


def _kill_edge_processes_safely() -> None:
    """在 Windows 上以 taskkill 清理 Edge / EdgeDriver；非 Windows 直接略過。
    不 raise，僅 print 紀錄（維持容錯原則）。
    """
    try:
        if platform.system().lower() != "windows":
            _log("[T2] 非 Windows，略過清洗瀏覽器。")
            return
        for pname in EDGE_PROCESS_NAMES:
            try:
                cmd = ["taskkill", *TASKKILL_FLAGS, "/IM", pname]
                _log(f"[T2] 嘗試終止程序：{' '.join(cmd)}")
                completed = subprocess.run(
                    cmd, check=False, capture_output=True, text=True, timeout=10
                )
                if completed.returncode == 0:
                    _log(f"[T2] 已終止：{pname}")
                else:
                    msg = completed.stderr.strip() or completed.stdout.strip()
                    _log(f"[T2] 終止 {pname} 回傳碼 {completed.returncode}；訊息：{msg}")
            except Exception as e:
                _log(f"[T2] 終止 {pname} 例外：{e}")
        time.sleep(0.8)  # 稍待釋放檔案鎖
    except Exception as e:
        _log(f"[T2] 清洗瀏覽器流程例外：{e}")




import os
import re
import time
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Tuple, Optional

from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------------
# 環境參數（可依需要覆寫）
# ---------------------
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 你平常登入用的正式 Profile（維持不動）
USER_DATA_DIR  = str(Path.home() / "AppData/Local/Microsoft/Edge/User Data")
PROFILE_DIR    = "Profile 1"

# ✅ 新增：給 9222 專用的獨立資料夾（避開 Profile 鎖）
# 使用 TEMP 下的資料夾，跟你手動成功的方式一致
EDGE9222_USER_DATA_DIR = os.path.join(os.environ.get("TEMP", str(Path.home())), "edge9222-test")

DEBUG_PORT     = 9222
DRIVER_NAME    = "msedgedriver.exe"
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DRIVER_PATH    = os.path.join(BASE_DIR, DRIVER_NAME)

# ---- 等待與截圖定位 ----
MIN_DWELL_SEC = 3.0
WAIT_READY_TIMEOUT = 25
WAIT_SELECTOR_TIMEOUT = 6.0
SCROLL_PAUSE = 0.35
SCROLL_WAIT_SEC = 0.8
TOP_OFFSET = -10
FIRST_H1_OFFSET = 10
SPEC_HEADING_TEXT = "商品規格"
DESC_HEADING_TEXT = "商品描述"
HEADING_CLASS_HINT = "WjNdTR"  # 若頁面 class 有變更仍可退回模糊包含

# ---- 其他 ----
PRODUCT_PATTERNS = (r"/product/(\d+)/(\d+)", r"-i\.(\d+)\.(\d+)")


# ================= Utils =================

def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _find_edge() -> Optional[str]:
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


def _wait_debug_ready(port: int = DEBUG_PORT, timeout: int = 20) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _ping_debug(port):
            return True
        time.sleep(0.25)
    return False


def is_shopee_product_url(url: str) -> bool:
    return bool(url and "shopee.tw" in url and any(re.search(p, url) for p in PRODUCT_PATTERNS))


# ================= Edge：登入 / 9222 / 附掛 =================

def prelogin_normal_mode(open_url: str = "https://shopee.tw") -> None:
    """以一般模式開 Edge，手動登入/通過驗證；按 Enter 後自動關閉。不拋例外。"""
    try:
        # ✅ 新增：進入人工驗證前的「可選清洗」
        if CLEAN_EDGE_BEFORE_PRELOGIN:
            _log("[T2] CLEAN_EDGE_BEFORE_PRELOGIN=True，先清洗 Edge 程序…")
            _kill_edge_processes_safely()
        else:
            _log("[T2] CLEAN_EDGE_BEFORE_PRELOGIN=False，略過清洗。")

        edge = _find_edge()
        if not edge:
            _log("[ERR] 找不到 msedge.exe，請檢查 EDGE_PATHS 或系統 PATH。")
            return
        cmd = [
            edge,
            f"--user-data-dir={USER_DATA_DIR}",
            f"--profile-directory={PROFILE_DIR}",
            "--start-maximized",
            open_url,
        ]
        print("🔹 第一階段（一般模式）：請登入蝦皮並完成驗證。完成後回到此視窗按 Enter。")
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            input("⏎  完成登入後按 Enter 繼續… ")
        finally:
            # ✅ 強化：關閉兩類程序（Edge 與 EdgeDriver），並保持容錯
            for pname in EDGE_PROCESS_NAMES:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/IM", pname],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10
                    )
                except Exception:
                    pass
            time.sleep(2.0)
    except Exception as e:
        _log(f"[T2] prelogin_normal_mode 例外（已吞）：{e}")
        return


def start_edge_9222() -> None:
    """啟動 Edge 於 9222，若已就緒則略過。不拋例外，但會印出關鍵狀態。"""
    try:
        if _ping_debug(DEBUG_PORT):
            _log("Edge 9222 已就緒")
            return

        if CLEAN_EDGE_BEFORE_PRELOGIN:
            _log("[T2] (start_edge_9222) 啟動前清洗 Edge 程序…")
            _kill_edge_processes_safely()

        edge = _find_edge()
        if not edge:
            _log("[ERR] 找不到 msedge.exe，請檢查 EDGE_PATHS 或系統 PATH。")
            return

        # ✅ 關鍵：9222 用獨立 user-data-dir，避免 profile 被鎖
        cmd = [
            edge,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={EDGE9222_USER_DATA_DIR}",
            f'--profile-directory={PROFILE_DIR}',
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=CalculateNativeWinOcclusion",
        ]

        _log(f"[T2] 啟動 Edge 9222：{' '.join(cmd)}")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ok = _wait_debug_ready(DEBUG_PORT, timeout=20)
        if ok:
            _log("[T2] Edge 9222 已啟動並可連線")
        else:
            _log("[ERR] Edge 9222 啟動失敗：20 秒內未偵測到 /json/version（可能被政策擋或程序秒退）")
    except Exception as e:
        _log(f"[ERR] start_edge_9222 例外：{e}")
        return


def attach_driver() -> Optional[webdriver.Edge]:
    """附掛既有 Edge 9222 session，並收斂分頁；失敗回傳 None。"""
    driver_path = DRIVER_PATH
    if not os.path.exists(driver_path):
        found = shutil.which(DRIVER_NAME)
        if found:
            driver_path = found
    if not os.path.exists(driver_path):
        _log("[ERR] 找不到 msedgedriver.exe，請放在與本檔同層或加到 PATH。")
        return None

    try:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
        driver = webdriver.Edge(service=Service(executable_path=driver_path), options=opts)
        try:
            driver.maximize_window(); driver.execute_script("window.focus();")
        except Exception:
            pass
        # 關閉前面所有分頁，保留最後一個
        try:
            handles = driver.window_handles
            for h in handles[:-1]:
                driver.switch_to.window(h)
                try:
                    driver.close()
                except Exception:
                    pass
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass
        return driver
    except Exception:
        return None


# ================= 頁面就緒 / 暖捲 =================

def wait_ready(driver, timeout: float = WAIT_READY_TIMEOUT) -> None:
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.25)
    time.sleep(0.8)


def wait_for_selectors(driver, timeout: float = WAIT_SELECTOR_TIMEOUT) -> bool:
    end = time.time() + float(timeout)
    while time.time() < end:
        try:
            if driver.find_elements(By.CSS_SELECTOR, "h1, h1 span"): return True
            og = driver.find_elements(By.CSS_SELECTOR, 'meta[property="og:title"]')
            if og and og[0].get_attribute("content"): return True
            if driver.find_elements(By.CSS_SELECTOR, '[data-testid="shop-name"], div.fV3TIn, a[href*="/shop/"]'): return True
            if driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha"], div#g-recaptcha, div[aria-label*="驗證"]'): return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def gentle_scroll(driver) -> None:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.33);"); time.sleep(SCROLL_PAUSE)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.66);"); time.sleep(SCROLL_PAUSE)
        driver.execute_script("window.scrollTo(0, 0);"); time.sleep(0.2)
    except Exception:
        pass


# ================= 開頁 / 商品頁偵測 =================

def open_and_prepare(driver, url: str) -> None:
    """開啟頁面並進行暖捲；若非商品頁也不拋例外。"""
    try:
        driver.get(url)
        wait_ready(driver)
        time.sleep(1.0)
        gentle_scroll(driver)
        if not wait_for_selectors(driver, timeout=WAIT_SELECTOR_TIMEOUT):
            # 再嘗試一次硬導向
            try:
                driver.execute_script("window.location.assign(arguments[0])", url)
            except Exception:
                driver.get(url)
            wait_ready(driver); gentle_scroll(driver); wait_for_selectors(driver, timeout=WAIT_SELECTOR_TIMEOUT)
    except Exception:
        pass


def is_on_product_page(driver) -> bool:
    try:
        return driver.execute_script(r"""
            try {
              const url = location.href;
              if (/\/product\/\d+\/\d+/.test(url)) return true;
              const link = document.querySelector('link[rel="canonical"]');
              if (link && /\/product\/\d+\/\d+/.test(link.href)) return true;
              const h1 = document.querySelector('h1, h1 span');
              const name = h1 && h1.innerText ? h1.innerText.trim() : "";
              if (name && name.length > 3) return true;
              const og = document.querySelector('meta[property="og:title"]');
              if (og && og.content && og.content.trim().length > 3 && !/蝦皮購物/.test(og.content)) return true;
              return false;
            } catch(e){ return false; }
        """)
    except Exception:
        return False


# ================= 讀頁面基本資訊 =================

def read_in_page(driver) -> Tuple[str, str, bool]:
    """快速讀：品名、賣家展示名、是否驗證頁。失敗時回 ("", "", False)。"""
    js = r"""
    const out = {name:"", seller:"", isVerify:false, dbg:{url:"", title:"", hint:"", why:[]}};

    try {
      // --- basic dbg ---
      out.dbg.url = (location && location.href) ? location.href : "";
      out.dbg.title = (document && document.title) ? document.title : "";

      // --- name ---
      let name = "";
      const h1 = document.querySelector('h1, h1 span');
      if (h1 && h1.innerText) name = h1.innerText.trim();
      if (!name) {
        const og = document.querySelector('meta[property="og:title"]');
        if (og && og.content) name = og.content.trim();
      }
      out.name = name || "";

      // --- seller ---
      let seller = "";
      const cand = [
        '[data-testid="shop-name"]',
        'div.fV3TIn',
        'a[href*="/shop/"]',
        'a[data-sqe="link"][href*="/shop/"]',
        'div.seller-name, span.seller-name'
      ];
      for (const sel of cand) {
        const el = document.querySelector(sel);
        if (el && el.innerText && el.innerText.trim()) { seller = el.innerText.trim(); break; }
      }
      out.seller = seller || "";

      // --- verify detection (strong signals only) ---
      const url = out.dbg.url || "";
      const title = out.dbg.title || "";

      // (1) URL / Title strong keywords
      const strongUrlTitle = /(captcha|recaptcha|hcaptcha|arkose|challenge|verify|verification|human)/i;
      if (strongUrlTitle.test(url) || strongUrlTitle.test(title)) {
        out.dbg.why.push("url_or_title");
      }

      // (2) DOM: captcha widgets / iframes / common verify containers
      const hasCaptchaDom = !!(
        document.querySelector('iframe[src*="recaptcha"], iframe[src*="captcha"], iframe[src*="hcaptcha"]') ||
        document.querySelector('div#g-recaptcha, div.g-recaptcha, div.h-captcha, iframe[title*="recaptcha"]') ||
        document.querySelector('[aria-label*="驗證"], [id*="captcha"], [class*="captcha"], [class*="recaptcha"], [class*="hcaptcha"]')
      );
      if (hasCaptchaDom) out.dbg.why.push("captcha_dom");

      // (3) Text: strong phrases ONLY (avoid generic "驗證")
      const text = (document.body && document.body.innerText) ? document.body.innerText : "";
      const strongText = /(請完成驗證|請證明你不是機器人|verify you are human|robot check|are you human|驗證失敗)/i;
      if (strongText.test(text)) out.dbg.why.push("strong_text");

      // decide
      out.isVerify = (out.dbg.why.length > 0);

      // attach hint for debugging
      if (out.isVerify) {
        const hint = (text || "").replace(/\s+/g, " ").trim();
        out.dbg.hint = hint.slice(0, 220);
      }
    } catch(e) {}

    return out;
    """
    try:
        d = driver.execute_script(js)
        name = d.get("name", "") if isinstance(d, dict) else ""
        seller = d.get("seller", "") if isinstance(d, dict) else ""
        is_verify = bool(d.get("isVerify")) if isinstance(d, dict) else False

        # ✅ 只有判定驗證頁才印 debug，避免刷屏
        if is_verify and isinstance(d, dict):
            dbg = d.get("dbg", {}) or {}
            _log(f"[T2][VERIFY] url={dbg.get('url','')}")
            _log(f"[T2][VERIFY] title={dbg.get('title','')}")
            _log(f"[T2][VERIFY] why={dbg.get('why','')}")
            _log(f"[T2][VERIFY] hint={dbg.get('hint','')}")
        return name, seller, is_verify
    except Exception:
        return "", "", False


# ================= 三段截圖 =================
FIRST_H1_SELECTOR = "h1.vR6K3w"  # 優先特定類別；失敗退回任意 h1


def _scroll_to_h1_and_wait(driver, selector: str = FIRST_H1_SELECTOR, offset: int = FIRST_H1_OFFSET) -> bool:
    try:
        els = driver.find_elements(By.CSS_SELECTOR, selector)
        el = els[0] if els else None
        if not el:
            els2 = driver.find_elements(By.CSS_SELECTOR, "h1, h1 span")
            el = els2[0] if els2 else None
        if not el:
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'start'});", el)
        driver.execute_script("window.scrollBy(0, -arguments[0]);", offset)
        time.sleep(SCROLL_WAIT_SEC)
        return True
    except Exception:
        return False


def _find_heading(driver, text: str):
    try:
        return WebDriverWait(driver, 2).until(
            EC.presence_of_element_located((By.XPATH, f"//h2[normalize-space()='{text}']"))
        )
    except Exception:
        pass
    try:
        return driver.find_element(By.XPATH, f"//h2[contains(.,'{text}') and contains(@class,'{HEADING_CLASS_HINT}')]")
    except Exception:
        return None


def _scroll_to_heading_and_wait(driver, text: str) -> bool:
    el = _find_heading(driver, text)
    if el:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'start'});", el)
            driver.execute_script("window.scrollBy(0, arguments[0]);", -TOP_OFFSET)
            time.sleep(SCROLL_WAIT_SEC)
            return True
        except Exception:
            return False
    return False


def capture_top_spec_desc(driver, base_png_path: str) -> Tuple[str, str, str]:
    """
    產出三張：
      1) 顶部：對齊 h1 後位移補償 → base.png
      2) 商品規格：錨點 h2=商品規格（miss → 45%）→ base_spec.png
      3) 商品描述：錨點 h2=商品描述（miss → 80%）→ base_desc.png
    任何錯誤均以 fallback 方式產出截圖檔並回傳路徑。
    """
    folder = os.path.dirname(base_png_path)
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass

    # 放大以提升可讀性
    try:
        driver.execute_script("document.body.style.zoom='1.25'")
    except Exception:
        pass
    time.sleep(0.15)

    # 1) 首張
    try:
        aligned = _scroll_to_h1_and_wait(driver)
        if not aligned:
            driver.execute_script("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.10));")
            time.sleep(SCROLL_WAIT_SEC)
    except Exception:
        pass
    top_png = base_png_path
    try:
        driver.save_screenshot(top_png)
    except Exception:
        # 若失敗，仍回傳「預期路徑」
        pass

    # 2) 商品規格
    spec_png = base_png_path.replace(".png", "_spec.png")
    try:
        if not _scroll_to_heading_and_wait(driver, SPEC_HEADING_TEXT):
            driver.execute_script("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.45));")
            time.sleep(SCROLL_WAIT_SEC)
        driver.save_screenshot(spec_png)
    except Exception:
        pass

    # 3) 商品描述
    desc_png = base_png_path.replace(".png", "_desc.png")
    try:
        if not _scroll_to_heading_and_wait(driver, DESC_HEADING_TEXT):
            driver.execute_script("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.80));")
            time.sleep(SCROLL_WAIT_SEC)
        driver.save_screenshot(desc_png)
    except Exception:
        pass

    # 回頂
    try:
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass

    return top_png, spec_png, desc_png


# ================= 小幫手 =================

def parse_shop_item_id(url: str) -> Tuple[str, str]:
    for p in PRODUCT_PATTERNS:
        m = re.search(p, url)
        if m:
            return m.group(1), m.group(2)
    return "", ""


__all__ = [
    # 對外 API
    "prelogin_normal_mode",
    "start_edge_9222",
    "attach_driver",
    "open_and_prepare",
    "read_in_page",
    "capture_top_spec_desc",
    # 可能會用到的工具
    "parse_shop_item_id",
    "is_shopee_product_url",
    "wait_ready",
]
