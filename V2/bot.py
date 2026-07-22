#!/usr/bin/env python3
"""
LG V2 BOT — Pure HTTP (No Browser)
====================================
Feeds on ASP.NET WebForms like a protocol predator.

Flow:
  1. GET initial page     → extract __VIEWSTATE, __EVENTVALIDATION, dropdown values
  2. POST Search          → server returns inventory table in HTML
  3. Parse table (lxml)   → extract checkbox names, row data from every <tr>
  4. POST btnSave         → __doPostBack with all checkboxes=on → OTP triggered
  5. Done (OTP handling comes later)
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from lxml import html


class LGBot:
    DROP_NAMES = {
        "ddlProduct": "ctl00$ContentPlaceHolder1$ddlProduct",
        "ddlModelCode": "ctl00$ContentPlaceHolder1$ddlModelCode",
        "ddlBranch": "ctl00$ContentPlaceHolder1$ddlBranch",
        "ddlbillingbranchnew": "ctl00$ContentPlaceHolder1$ddlbillingbranchnew",
    }

    def __init__(self, url: str):
        self.url = url
        self.parsed = urlparse(url)
        self.base = f"{self.parsed.scheme}://{self.parsed.netloc}"

        self.sess = httpx.Client(
            http2=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            },
            follow_redirects=True,
            timeout=60.0,
        )

        # ——— state ———
        self.viewstate: str | None = None
        self.eventvalidation: str | None = None
        self.generator: str = "95C5CE92"
        self.dropdowns: dict[str, str] = {}
        self.last_html: str = ""

    # ───────────────────────── helpers ─────────────────────────

    def _extract_hidden(self, tree: html.HtmlElement) -> None:
        vs = tree.xpath('//*[@id="__VIEWSTATE"]/@value')
        ev = tree.xpath('//*[@id="__EVENTVALIDATION"]/@value')
        vg = tree.xpath('//*[@id="__VIEWSTATEGENERATOR"]/@value')
        if vs:
            self.viewstate = vs[0]
        if ev:
            self.eventvalidation = ev[0]
        if vg:
            self.generator = vg[0]

    def _parse_dropdowns(self, tree: html.HtmlElement) -> None:
        for name, ctl_name in self.DROP_NAMES.items():
            select = tree.xpath(f'//select[@id="{name}"]')
            if not select:
                continue
            options = select[0].xpath('.//option/@value')
            chosen = "--Select--"
            for opt in options:
                if opt != "--Select--":
                    chosen = opt
                    break
            self.dropdowns[ctl_name] = chosen

    def _request(self, data: dict, label: str = "") -> httpx.Response:
        t0 = time.perf_counter()
        try:
            resp = self.sess.post(self.url, data=data)
        except httpx.TimeoutException:
            print(f"       ↳ {label} TIMEOUT after {(time.perf_counter()-t0)*1000:.0f}ms")
            raise
        wall = (time.perf_counter() - t0) * 1000
        elapsed_ms = resp.elapsed.total_seconds() * 1000
        self.last_html = resp.text
        print(f"       ↳ {label} HTTP {resp.status_code} {resp.http_version} | "
              f"TTFB={elapsed_ms:.0f}ms wall={wall:.0f}ms "
              f"body={len(resp.content):,}B")
        resp.raise_for_status()
        return resp

    # ───────────────────────── steps ─────────────────────────

    def get_initial(self) -> html.HtmlElement:
        """GET the page → capture ViewState + dropdown values."""
        t0 = time.perf_counter()
        resp = self.sess.get(self.url)
        dt = (time.perf_counter() - t0) * 1000
        print(f"       ↳ GET HTTP {resp.status_code} {resp.http_version} | wall={dt:.0f}ms body={len(resp.content):,}B")
        resp.raise_for_status()
        self.last_html = resp.text
        tree = html.fromstring(resp.content)
        self._extract_hidden(tree)
        self._parse_dropdowns(tree)
        return tree

    def search(self) -> html.HtmlElement:
        """POST Search → server returns inventory table."""
        data = {
            "__VIEWSTATE": self.viewstate,
            "__EVENTVALIDATION": self.eventvalidation,
            "__VIEWSTATEGENERATOR": self.generator,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "ctl00$ContentPlaceHolder1$btnSearch": "Search",
            **self.dropdowns,
        }
        resp = self._request(data, label="SEARCH")
        tree = html.fromstring(resp.content)
        self._extract_hidden(tree)
        return tree

    @staticmethod
    def parse_inventory(tree: html.HtmlElement) -> list[dict]:
        """Return list of row dicts from #gvNGSIDetails."""
        table = tree.xpath('//table[@id="gvNGSIDetails"]')
        if not table:
            return []

        rows = table[0].xpath('.//tr[position()>1]')
        inventory = []

        for tr in rows:
            cb = tr.xpath('.//input[@type="checkbox"]')
            if not cb:
                continue

            def g(xpath_expr: str) -> str:
                els = tr.xpath(xpath_expr)
                return (els[0].text or "").strip() if els else ""

            inventory.append({
                "checkbox_name": cb[0].get("name"),
                "base_price":    g('.//*[contains(@id,"lblfulbaseprice_gv")]'),
                "batchno":       g('.//*[contains(@id,"lblbatchnum_gv")]'),
                "image_name":    g('.//*[contains(@id,"lblImageName")]'),
                "dmg_image":     g('.//*[contains(@id,"lblDmgImg2Name")]'),
                "serial_no":     g('.//*[contains(@id,"lblsrno_gv")]'),
                "model_code":    g('.//*[contains(@id,"lblModelCode_gv")]'),
                "product":       g('.//*[contains(@id,"lblProduct_gv")]'),
                "branch":        g('.//*[contains(@id,"lblBranch_gv")]'),
                "bill_branch":   g('.//*[contains(@id,"lblBill_Branch")]'),
                "category":      g('.//*[contains(@id,"lblDiscCategory_gv")]'),
                "from_date":     g('.//*[contains(@id,"lblfromdate_gv")]'),
                "to_date":       g('.//*[contains(@id,"lbltodate_gv")]'),
                "dealer_price":  g('.//*[contains(@id,"lbldealerp_gv")]'),
                "dp":            g('.//*[contains(@id,"lbldp_gv")]'),
                "defect":        g('.//*[contains(@id,"lbltypeofdefect_gv")]'),
            })

        return inventory

    def save(self, inventory: list[dict]) -> html.HtmlElement:
        """POST __doPostBack(btnSave) with every checkbox=on."""
        data = {
            "__VIEWSTATE": self.viewstate,
            "__EVENTVALIDATION": self.eventvalidation,
            "__VIEWSTATEGENERATOR": self.generator,
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnSave",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            **self.dropdowns,
        }
        for row in inventory:
            data[row["checkbox_name"]] = "on"

        resp = self._request(data, label="SAVE")
        tree = html.fromstring(resp.content)
        self._extract_hidden(tree)
        return tree


    # ───────────────────────── run ─────────────────────────

    def run(self) -> None:
        t0 = time.perf_counter()

        # ——— Step 1: GET initial state ———
        print("[1/4] GET initial page …")
        self.get_initial()
        print(f"       ViewState       → {self.viewstate[:50]}…")
        print(f"       EventValidation → {self.eventvalidation[:50]}…")
        print(f"       Dropdowns       → {self.dropdowns}")

        # ——— Step 2: Try parsing inventory from GET (session might have cached data) ———
        print("[2/4] Check GET response for inventory …")
        tree = html.fromstring(self.last_html.encode())  # re-parse GET response
        inventory = self._safe_parse(tree, "get_response.html")
        searched = False

        if not inventory:
            print("       No inventory in GET → POST Search …")
            t1 = time.perf_counter()
            tree = self.search()
            dt = (time.perf_counter() - t1) * 1000
            print(f"       Search took {dt:.0f} ms")
            searched = True
            inventory = self._safe_parse(tree, "search_response.html")

        if not inventory:
            print("       ⚠ No inventory found after search either.")
            return

        print(f"       Found {len(inventory)} row(s)")
        for i, r in enumerate(inventory):
            print(f"       [{i+1}] {r['serial_no']}  |  {r['model_code']}  |  "
                  f"₹{r['dealer_price']}  |  DP {r['dp']}%  |  cb={r['checkbox_name']}")

        # ——— Step 3: POST Save ———
        print(f"[3/3] POST btnSave (selecting all {len(inventory)} rows) …")
        t1 = time.perf_counter()
        tree = self.save(inventory)
        dt = (time.perf_counter() - t1) * 1000
        print(f"       Save took {dt:.0f} ms")

        # ——— Quick sanity ———
        msg_nodes = tree.xpath('//script[contains(text(),"alert")]')
        for node in msg_nodes:
            txt = node.text or ""
            m = re.search(r'alert\(["\'](.+?)["\']', txt)
            if m:
                msg = m.group(1)
                if "error" in msg.lower() or "fail" in msg.lower():
                    print(f"       ❌ Server alert: {msg}")
                else:
                    print(f"       ℹ Server message: {msg}")

        total = (time.perf_counter() - t0) * 1000
        print(f"\n✅ Done in {total:.0f} ms total")

    def _safe_parse(self, tree, debug_name: str) -> list[dict]:
        try:
            inv = self.parse_inventory(tree)
            return inv
        except Exception as e:
            Path(debug_name).write_text(self.last_html, encoding="utf-8")
            print(f"       ⚠ Parse error: {e} → saved {debug_name}")
            return []


# ═══════════════════════════════════════════════════════════════

def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else input("Enter URL: ").strip()
    if not url:
        print("URL required")
        sys.exit(1)
    LGBot(url).run()


if __name__ == "__main__":
    main()
