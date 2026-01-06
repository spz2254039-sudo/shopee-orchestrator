# T0_orchestrator__v4.py — v1-only (no relative imports, no build_cookie_header dependency)
# Flow: T1 parse → (opt) T2 open/shot → T3 API (opt retry) → (opt) T4 model fallback → T5/T6/T7 outputs

from typing import List, Dict, Tuple, Optional
import os, re, time, datetime

# ---- Config (keep your v1 config file) ----
from T0_orchestrator_config import OrchestratorFlags, OrchestratorParams, FLAGS, PARAMS

# ---- T1 v1: parsing & paths ----
from T1_case_parse_v1 import (
    gather_all_segments,
    build_output_filename_from_segments_v2,
    ensure_case_dir_by_wordname,
)

# ---- T2 v1: browser & page ops (parse_shop_item_id 在 vxx 放這裡) ----
from T2_shopee_page__v2_ import (
    prelogin_normal_mode, start_edge_9222, attach_driver,
    wait_ready, gentle_scroll, read_in_page,
    capture_top_spec_desc, parse_shop_item_id,
)

# ---- T3 v1: API (不假設有 build_cookie_header) ----
from T3_shopee_api__v1 import (
    fetch_get_pc, fetch_get_pc_via_page,
    extract_title, extract_seller_account, extract_bsmi, extract_model,
)

# ---- T4 v1: model fallback (若你還沒升到 v1，先註解掉這段) ----
HAS_T4 = False
try:
    from T4_shopee_model_fallback__v4 import fallback_model_via_AB, download_desc_images_only
    HAS_T4 = True
except Exception:
    HAS_T4 = False

# ---- T5/T6/T7 v1: outputs ----
try:
    from T5_report_word__v5 import init_doc, insert_segment_with_results
    HAS_T5_V1 = True
except Exception:
    HAS_T5_V1 = False
    from T5_report_word__v5 import render_word  # 若你沒有 v0_compat，就改成 v0.1.0 的檔名或自行刪除這段

try:
    from T6_report_mail__v3 import write_outlook_draft_eml_html
    HAS_T6_V1 = True
except Exception:
    HAS_T6_V1 = False
    from T6_report_mail__v3 import write_outlook_draft_eml_html  # 同上，若無可先關掉 T6

try:
    from T7_report_xml__v1 import write_bianzhen_xml_file, write_reply_xml_file
    HAS_T7_V1 = True
except Exception:
    HAS_T7_V1 = False
    from T7_report_xml__v1 import write_bianzhen_xml_file, write_reply_xml_file  # 同上


class ShopeeDocOrchestrator:
    def __init__(self, flags: OrchestratorFlags, params: OrchestratorParams):
        self.f = flags
        self.p = params

    # ---- helpers ----
    def _safe(self, fn, *a, **kw):
        try:
            return True, fn(*a, **kw)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[WARN] {fn.__name__} failed: {e}")
            return False, None

    def _sanitize_filename(self, name: str) -> str:
        name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "")
        name = name.strip(" .")
        return name or datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".docx"

    def _ensure_case_paths(self, segments: List[Dict]) -> Tuple[str, str, str]:
        ok, word_filename = self._safe(build_output_filename_from_segments_v2, segments)
        word_filename = self._sanitize_filename(word_filename if ok and word_filename else "")
        ok, case_dir = self._safe(ensure_case_dir_by_wordname, self.p.base_dir, word_filename)
        if not ok or not case_dir:
            case_dir = os.path.join(self.p.base_dir, "CASE_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            os.makedirs(case_dir, exist_ok=True)
        out_docx = os.path.join(case_dir, word_filename)
        return word_filename, case_dir, out_docx

    def _cookie_header_local(self, driver, domain_hint: str = "shopee.tw") -> str:
        """
        用 driver.get_cookies() 組 Cookie header；若失敗回空字串。
        """
        try:
            if not driver:
                return ""
            pairs = []
            for c in driver.get_cookies():
                d = (c or {}).get("domain", "") or ""
                if domain_hint in d:
                    name = (c or {}).get("name", "")
                    val  = (c or {}).get("value", "")
                    if name:
                        pairs.append(f"{name}={val}")
            return "; ".join(pairs)
        except Exception:
            return ""

    def _api_fetch_with_retry(self, driver, shop_id: str, item_id: str) -> Optional[dict]:
        """
        v1 介面下的容錯策略：
        1) 直連 fetch_get_pc（若接受 cookie 參數就帶，否則不帶）
        2) 若拿不到 data → 若啟用 T2 且有 driver → 改走 fetch_get_pc_via_page
        """
        tries = 1 + max(0, int(getattr(self.p, "api_retry", 1)))
        sleep_s = float(getattr(self.p, "api_retry_sleep", 0.6))

        cookie_str = self._cookie_header_local(driver, "shopee.tw")

        # 直連
        for i in range(tries):
            try:
                payload = None
                try:
                    payload = fetch_get_pc(shop_id, item_id, cookie_str)  # 若簽名支援 cookie
                except TypeError:
                    payload = fetch_get_pc(shop_id, item_id)             # 不支援 cookie 的舊簽名

                if payload and isinstance(payload, dict) and payload.get("data"):
                    return payload
            except Exception as e:
                print(f"[T3] fetch_get_pc failed: {e}")

            if i < tries - 1:
                time.sleep(sleep_s)

        # via_page（需要頁面上下文）
        if self.f.use_T2_page and driver:
            for i in range(tries):
                try:
                    payload = fetch_get_pc_via_page(driver, shop_id, item_id)
                    if payload and isinstance(payload, dict) and payload.get("data"):
                        return payload
                except Exception as e:
                    print(f"[T3] fetch_get_pc_via_page failed: {e}")

                if i < tries - 1:
                    time.sleep(sleep_s)

        return None

    # ---- main web enrichment ----
    def _enrich_segments_via_web(self, driver, segments: List[Dict], case_dir: str):
        global_idx = 1
        first_done = False

        non_interactive = bool(getattr(self.p, "non_interactive", False))

        for seg in segments:
            seg_results = []
            for url in (seg.get("urls") or []):
                shop_id, item_id = parse_shop_item_id(url)
                if not shop_id:
                    print(f"[SKIP] 非商品頁：{url}")
                    continue

                # T2：開頁/暖捲/讀頁（可關）
                name, seller, is_verify = "", "", False
                if self.f.use_T2_page and driver:
                    try:
                        driver.get(url)
                        wait_ready(driver); gentle_scroll(driver)
                        name, seller, is_verify = read_in_page(driver)

                        if is_verify:
                            # ✅ B 模式：完全無互動 → 直接 skip
                            if non_interactive:
                                print("[WARN] 驗證頁遭遇（non_interactive=True），本筆直接 skip。")
                                seg_results.append({
                                    "api_title": name or "商品名稱未找到",
                                    "bsmi": "查無", "model_no": "查無",
                                    "seller_account": "",
                                    "name": name or "", "seller": seller or "",
                                    "shop_id": shop_id, "url": url, "pngs": [],
                                    "desc_imgs": [],
                                })
                                continue

                            # ✅ 非 B 模式：依 verify_strategy
                            strat = getattr(self.p, "verify_strategy", "manual")
                            if strat == "manual":
                                input("🛑 驗證頁，請在瀏覽器完成後按 Enter 繼續…")
                                wait_ready(driver); gentle_scroll(driver)
                                name, seller, is_verify = read_in_page(driver)
                            else:
                                print("[WARN] 驗證頁遭遇，依策略 skip 本筆。")
                                seg_results.append({
                                    "api_title": name or "商品名稱未找到",
                                    "bsmi": "查無", "model_no": "查無",
                                    "seller_account": "",
                                    "name": name or "", "seller": seller or "",
                                    "shop_id": shop_id, "url": url, "pngs": [],
                                    "desc_imgs": [],
                                })
                                continue

                        if not first_done:
                            time.sleep(max(0.0, float(getattr(self.p, "first_url_manual_dwell_sec", 3.0))))
                            first_done = True
                    except Exception as e:
                        print(f"[WARN] T2 open/read failed: {e}")

                # T3：API
                api_title = ""; seller_acc = ""; bsmi = "查無"; model_no = "查無"
                if self.f.use_T3_api:
                    payload = self._api_fetch_with_retry(driver if self.f.use_T2_page else None, shop_id, item_id)
                    if payload:
                        api_title  = (extract_title(payload) or "").strip()
                        seller_acc = (extract_seller_account(payload) or "").strip()
                        bsmi       = (extract_bsmi(payload) or "查無").strip()
                        model_no   = (extract_model(payload) or "查無").strip()

                # T2：截圖（可關）
                pngs = []
                if self.f.use_T2_page and driver and not is_verify:
                    shots_dir = os.path.join(case_dir, getattr(self.p, "screenshots_dirname", "screenshots"))
                    os.makedirs(shots_dir, exist_ok=True)
                    base = os.path.join(shots_dir, f"attach_batch_{global_idx}.png")
                    ok, triple = self._safe(capture_top_spec_desc, driver, base)
                    if ok and triple:
                        pngs = list(triple)
                    global_idx += 1

                # T4：型號備援（可關）
                desc_imgs = []
                if self.f.use_T4_model_fallback and HAS_T4 and driver:
                    t4_mode = (getattr(self.p, "t4_mode", "ocr") or "ocr").strip().lower()
                    if model_no == "查無":
                        if t4_mode == "ocr":
                            ok, m = self._safe(fallback_model_via_AB, driver, url, case_dir)
                            if ok and m and m != "查無":
                                model_no = m
                        elif t4_mode == "download_only":
                            ok, imgs = self._safe(download_desc_images_only, driver, url, case_dir)
                            if ok and imgs:
                                desc_imgs = list(imgs)

                seg_results.append({
                    "api_title": api_title or (name or "商品名稱未找到"),
                    "bsmi": bsmi or "查無",
                    "model_no": model_no or "查無",
                    "seller_account": seller_acc or "",
                    "name": name or "",
                    "seller": seller or "",
                    "shop_id": shop_id,
                    "url": url,
                    "pngs": pngs,
                    "desc_imgs": desc_imgs,
                })
            seg["results"] = seg_results

    # ---- outputs ----
    def _emit_outputs(self, segments: List[Dict], case_dir: str, out_docx: str):
        if self.f.use_T5_word:
            if HAS_T5_V1:
                doc = init_doc()
                sec = doc.sections[0]
                image_width_emu = sec.page_width - sec.left_margin - sec.right_margin
                for seg in segments:
                    insert_segment_with_results(doc, seg, image_width_emu)
                    doc.add_paragraph("")  # 段間空行
                doc.save(out_docx)
                print(f"[OK] Word -> {out_docx}")
            else:
                p = render_word(segments, out_docx)
                print(f"[OK] Word -> {p}")

        if self.f.use_T6_mail:
            eml = write_outlook_draft_eml_html(segments, case_dir)
            print(f"[OK] Mail Draft -> {eml}")

        if self.f.use_T7_xml:
            p1 = write_bianzhen_xml_file(segments, case_dir)
            p2 = write_reply_xml_file(segments, case_dir)
            print(f"[OK] XML -> {p1} | {p2}")

    # ---- entrypoint ----
    def orchestrate(self, raw_text: str):
        if not (raw_text or "").strip():
            print("❌ 無輸入"); return

        if self.f.use_T1_parse:
            ok, segments = self._safe(gather_all_segments, raw_text)
            if not ok or not segments:
                print("❌ 未解析到任何段落"); return
        else:
            print("⚠️ 未啟用 T1，請自行提供 segments 結構"); return

        word_filename, case_dir, out_docx = self._ensure_case_paths(segments)

        driver = None
        if self.f.use_T2_page:
            non_interactive = bool(getattr(self.p, "non_interactive", False))
            strat = getattr(self.p, "verify_strategy", "manual")

            # ✅ B 模式：完全無互動 → 不跑 prelogin
            # ✅ 非 B 模式：只有 manual 才跑 prelogin
            if (not non_interactive) and (strat == "manual"):
                self._safe(prelogin_normal_mode, "https://shopee.tw")

            self._safe(start_edge_9222)
            ok, driver = self._safe(attach_driver)
            driver = driver if ok else None

            # ✅ attach 失敗：B 模式直接關掉 T2，避免後續 invalid session 噴滿
            if not driver and non_interactive:
                print("🟠 無法附掛 9222，本次關閉 T2（non_interactive=True）。")
                self.f.use_T2_page = False

        self._enrich_segments_via_web(driver, segments, case_dir)
        self._emit_outputs(segments, case_dir, out_docx)

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        print(f"📂 CASE DIR = {case_dir}")


if __name__ == "__main__":
    orch = ShopeeDocOrchestrator(FLAGS, PARAMS)
    demo_text = """收文號：1150050030
來文日期：1150104
來文機關：意見信箱-李志唐
來文號：2026010400018
受文者：經濟部標準檢驗局台南分局
附件數：1
附件檔名：2026010400018檢舉說明.png

主旨：市場監督 李志唐 (申請流水號：1150104000018) 有貼商品安全標章，品質不符規定,未使用或盜用他人字號，還有現有字號用在非對應商品 網路平台

說明：

[蝦皮, 行李箱, https://shopee.tw/product/116438146/29342947004] [蝦皮, 行李箱, https://shopee.tw/product/66271707/26752166501] [蝦皮, 行李箱, https://shopee.tw/product/66271707/26105443846] [蝦皮, 行李箱, https://shopee.tw/product/66271707/24332013824] [蝦皮, 行李箱, https://shopee.tw/product/66271707/27704516637]

正本：經濟部標準檢驗局

"""
    orch.orchestrate(demo_text)
