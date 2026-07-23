#!/usr/bin/env python3
"""LG V2 BOT — Pure HTTP, JSON logs, never stops."""

import json
import os
import sys
import time
from urllib.parse import urlparse

import httpx
from lxml import html


def _log(level: str, message: str) -> None:
    print(json.dumps({"level": level, "message": message}))


class LGBot:
    DROP_NAMES = {
        "ddlProduct": "ctl00$ContentPlaceHolder1$ddlProduct",
        "ddlModelCode": "ctl00$ContentPlaceHolder1$ddlModelCode",
        "ddlBranch": "ctl00$ContentPlaceHolder1$ddlBranch",
        "ddlbillingbranchnew": "ctl00$ContentPlaceHolder1$ddlbillingbranchnew",
    }

    def __init__(
        self,
        url: str | None = None,
        api_url: str | None = None,
        filter_id: str | None = None,
        worker_id: str | None = None,
        service_key: str | None = None,
    ):
        self.url = url or ""
        self.api_url = api_url
        self.filter_id = filter_id
        self.worker_id = worker_id or ""
        self.service_key = service_key or ""

        self.sess = httpx.Client(
            http2=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
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
        self.viewstate: str | None = None
        self.eventvalidation: str | None = None
        self.generator: str = "95C5CE92"
        self.dropdowns: dict[str, str] = {}
        self.ship_to: str = ""
        self.last_html: str = ""
        self.iter = 0
        self.bids_placed = 0
        self.detected_count = 0
        self.failed_count = 0

        self.min_price: float | None = None
        self.max_price: float | None = None
        self.dp_percent: float | None = None
        self.scan_interval: float = 2.0
        self.email: str = ""
        self.app_password: str = ""

    def fetch_config(self) -> None:
        if not self.api_url or not self.filter_id:
            return
        _log("info", f"Fetching config for filter {self.filter_id}")
        resp = httpx.get(
            f"{self.api_url}/api/bot/filter-profiles/{self.filter_id}",
            headers={"X-SERVICE-KEY": self.service_key},
            timeout=15,
        )
        if resp.status_code != 200:
            _log("error", f"Config fetch failed: HTTP {resp.status_code}")
            sys.exit(1)
        c = resp.json()
        self.url = c["url"]
        self.min_price = c["priceMin"]
        self.max_price = c["priceMax"]
        self.dp_percent = c["dpPercent"]
        self.scan_interval = max(0.5, c.get("scanInterval", 2000) / 1000.0)
        self.email = c.get("email", "")
        self.app_password = c.get("appPassword", "")
        _log("info", f"Profile: {c['name']} | URL: {self.url}")
        _log("info", f"Price range: ₹{self.min_price}–₹{self.max_price} | DP ≥ {self.dp_percent}%")
        _log("info", f"Scan: {self.scan_interval}s | Email: {self.email or 'none'}")

    def _send_kpi(self, response_time_ms: int) -> None:
        if not self.api_url:
            return
        try:
            httpx.post(
                f"{self.api_url}/api/bot/report",
                json={
                    "worker_id": self.worker_id,
                    "status": "running",
                    "detected": self.detected_count,
                    "booked": self.bids_placed,
                    "failed": self.failed_count,
                    "response_time_ms": response_time_ms,
                    "iteration": self.iter,
                    "filter_id": self.filter_id or "",
                },
                headers={"X-SERVICE-KEY": self.service_key},
                timeout=3,
            )
        except Exception:
            pass

    def _report_booking(self, row: dict) -> None:
        if not self.api_url:
            return
        try:
            httpx.post(
                f"{self.api_url}/api/bot/bookings",
                json={
                    "serialNo": row["serial_no"],
                    "modelCode": row["model_code"],
                    "product": row["product"],
                    "branch": row["branch"],
                    "billBranch": row["bill_branch"],
                    "category": row["category"],
                    "biddingStart": row["from_date"],
                    "biddingEnd": row["to_date"],
                    "dealerPrice": float(row["dealer_price"].replace(",", "")),
                    "discountPct": float(row["dp"]),
                    "status": "confirmed",
                    "workerId": self.worker_id,
                    "filterId": self.filter_id or "",
                },
                headers={"X-SERVICE-KEY": self.service_key},
                timeout=5,
            )
        except Exception:
            pass

    def _filter_inventory(self, inventory: list[dict]) -> list[dict]:
        if self.min_price is None and self.max_price is None and self.dp_percent is None:
            return inventory

        def price(s: str) -> float:
            return float(s.replace(",", "").strip()) if s.strip() else 0.0

        def dp(s: str) -> float:
            return float(s.strip().rstrip("%")) if s.strip() else 0.0

        filtered = []
        for r in inventory:
            p = price(r["dealer_price"])
            d = dp(r["dp"])
            if self.min_price is not None and p < self.min_price:
                continue
            if self.max_price is not None and p > self.max_price:
                continue
            if self.dp_percent is not None and d < self.dp_percent:
                continue
            filtered.append(r)
        return filtered

    # ───────── helpers ─────────

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
        _log("info", f"Extracted viewstate {len(self.viewstate or ''):,} | ev {len(self.eventvalidation or ''):,}")

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
        labels = {k.split("$")[-1]: v for k, v in self.dropdowns.items()}
        _log("info", f"Dropdowns: {labels}")

    def _post(self, data: dict, label: str = "") -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            resp = self.sess.post(self.url, data=data)
        except Exception as e:
            _log("warn", f"{label} failed: {e}")
            return None
        wall = (time.perf_counter() - t0) * 1000
        elapsed_ms = resp.elapsed.total_seconds() * 1000
        self.last_html = resp.text
        _log("action", f"{label} HTTP {resp.status_code} | {elapsed_ms:.0f}ms | {len(resp.content):,}B")
        if resp.status_code != 200:
            return None
        return resp

    def _get(self) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            resp = self.sess.get(self.url)
        except Exception as e:
            _log("warn", f"GET failed: {e}")
            return None
        dt = (time.perf_counter() - t0) * 1000
        self.last_html = resp.text
        _log("action", f"GET HTTP {resp.status_code} | {dt:.0f}ms | {len(resp.content):,}B")
        return resp if resp.status_code == 200 else None

    # ───────── steps ─────────

    def get_initial(self) -> html.HtmlElement | None:
        resp = self._get()
        if resp is None:
            return None
        try:
            tree = html.fromstring(resp.content)
        except Exception:
            return None
        self._extract_hidden(tree)
        self._parse_dropdowns(tree)
        st = tree.xpath('//*[@id="txtcode"]/@value')
        self.ship_to = st[0] if st else ""
        return tree

    def search(self) -> html.HtmlElement | None:
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
        resp = self._post(data, "SEARCH")
        if resp is None:
            return None
        try:
            tree = html.fromstring(resp.content)
        except Exception:
            return None
        self._extract_hidden(tree)
        return tree

    @staticmethod
    def parse_inventory(tree: html.HtmlElement) -> list[dict]:
        table = tree.xpath('//table[@id="gvNGSIDetails"]')
        if not table:
            return []
        rows = table[0].xpath('.//tr[position()>1]')
        inventory = []
        for tr in rows:
            cb = tr.xpath('.//input[@type="checkbox"]')
            if not cb:
                continue

            def g(xp: str) -> str:
                els = tr.xpath(xp)
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

    def save(self, inventory: list[dict]) -> httpx.Response | None:
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
        resp = self._post(data, "SAVE")
        if resp is None:
            return None
        try:
            tree = html.fromstring(resp.content)
            self._extract_hidden(tree)
        except Exception:
            pass
        return resp

    @property
    def _pm_base(self) -> str:
        p = urlparse(self.url)
        path = p.path.rstrip("/")
        if path.endswith(".aspx"):
            base = path
        else:
            base = path + "/NGSI_CustomerBiddingInput.aspx"
        return f"{p.scheme}://{p.netloc}{base}"

    def _build_row_data(self, inventory: list[dict]) -> list[dict]:
        def num(s: str) -> float:
            s = s.replace(",", "").strip()
            return float(s) if s else 0.0
        rows = []
        for r in inventory:
            rows.append({
                "Branch": r["branch"],
                "Serial_No": r["serial_no"],
                "Image_Name": r["image_name"],
                "DmgImage_Name": r["dmg_image"],
                "ModelCode": r["model_code"],
                "Product": r["product"],
                "Disc_Category": r["category"],
                "Bidding_StartDate": r["from_date"],
                "Bidding_EndDate": r["to_date"],
                "Base_Price": num(r["base_price"]),
                "batchno": r["batchno"],
                "dp": num(r["dp"]),
                "Created_By": self.ship_to,
                "Ship_To_Code": self.ship_to,
            })
        return rows

    def _call_pm(self, method: str, body: dict) -> dict | None:
        url = f"{self._pm_base}/{method}"
        t0 = time.perf_counter()
        try:
            resp = self.sess.post(url, json=body, headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
            })
        except Exception as e:
            _log("warn", f"{method} failed: {e}")
            return None
        wall = (time.perf_counter() - t0) * 1000
        try:
            data = resp.json()
            result = str(data.get("d", "N/A"))[:120]
            _log("action", f"{method} HTTP {resp.status_code} | {wall:.0f}ms | {result}")
            return data
        except Exception:
            _log("warn", f"{method} HTTP {resp.status_code} | {wall:.0f}ms | non-json response")
            return {"d": None}

    def _try_all(self, inventory: list[dict]) -> bool:
        rows = self._build_row_data(inventory)
        _log("action", f"Booking {len(rows)} item(s) — trying S1→S2→S3")

        _log("action", "Strategy S1: SaveBiddingData direct")
        r1 = self._call_pm("SaveBiddingData", {"selectedRows": rows})
        if r1 and r1.get("d") and "SUCCESS" in str(r1.get("d")).upper():
            d = str(r1["d"])[:120]
            _log("success", f"S1 success: {d}")
            self.bids_placed += 1
            return True
        _log("info", "S1 skipped, trying S2")

        _log("action", "Strategy S2: btnSave → SaveBiddingData")
        self.save(inventory)
        r2 = self._call_pm("SaveBiddingData", {"selectedRows": rows})
        if r2 and r2.get("d") and "SUCCESS" in str(r2.get("d")).upper():
            d = str(r2["d"])[:120]
            _log("success", f"S2 success: {d}")
            self.bids_placed += 1
            return True
        _log("info", "S2 skipped, trying S3")

        _log("action", "Strategy S3: btnSave → ValidateOtp → SaveBiddingData")
        r3_v = self._call_pm("ValidateOtp", {"enteredOtp": "000000", "shipToCode": self.ship_to})
        r3_s = self._call_pm("SaveBiddingData", {"selectedRows": rows})
        if r3_s and r3_s.get("d") and "SUCCESS" in str(r3_s.get("d")).upper():
            d = str(r3_s["d"])[:120]
            _log("success", f"S3 success: {d}")
            self.bids_placed += 1
            return True
        _log("error", "All strategies failed for this batch")
        return False

    # ───────── forever loop ─────────

    def run_forever(self) -> None:
        _log("info", f"Bot started | URL: {self.url} | Worker: {self.worker_id or 'legacy'}")
        if self.filter_id:
            _log("info", f"Filter: {self.filter_id}")

        while True:
            self.iter += 1
            t_start = time.perf_counter()
            _log("info", f"Iteration {self.iter} | booked={self.bids_placed} detected={self.detected_count} failed={self.failed_count}")

            try:
                tree = self.get_initial()
                if tree is None:
                    _log("warn", "GET page failed, retry in 2s")
                    time.sleep(2)
                    continue
                _log("info", f"Ship-to: {self.ship_to[:20]}")

                inventory = self.parse_inventory(tree)
                _log("scan", f"Found {len(inventory)} item(s) on page")

                if not inventory:
                    _log("scan", "No items on page, sending search…")
                    tree2 = self.search()
                    if tree2 is not None:
                        inventory = self.parse_inventory(tree2)
                        _log("scan", f"Found {len(inventory)} item(s) after search")
                    else:
                        _log("warn", "Search failed")

                if inventory:
                    matched = self._filter_inventory(inventory)
                    _log("scan", f"{len(matched)} of {len(inventory)} item(s) match price/DP filter")

                    if matched:
                        self.detected_count += len(matched)
                        for i, r in enumerate(matched):
                            _log("detect", f"Match #{i+1}: {r['serial_no'][:15]} | {r['model_code']} | ₹{r['dealer_price']} | {r['dp']}%")
                        ok = self._try_all(matched)
                        if ok:
                            _log("success", f"Booked {len(matched)} item(s)")
                            for r in matched:
                                self._report_booking(r)
                        else:
                            self.failed_count += 1
                            _log("error", "Booking failed for all items in this iteration")
                    else:
                        _log("scan", "No items match filter criteria")
                else:
                    _log("scan", "No inventory found")

            except Exception as e:
                self.failed_count += 1
                _log("error", f"Iteration {self.iter} error: {e}")

            elapsed = time.perf_counter() - t_start
            elapsed_ms = int(elapsed * 1000)
            _log("info", f"Completed in {elapsed_ms}ms")

            sleep = max(0.1, self.scan_interval - elapsed)
            time.sleep(sleep)

            self._send_kpi(elapsed_ms)


# ═══════════════════════════════════════════════════════════════════

def main():
    args = [a for a in sys.argv[1:] if a]
    forever = "--forever" in args

    api_url = None
    filter_id = None
    worker_id = None
    url = None

    for a in args:
        if a.startswith("--api-url="):
            api_url = a.split("=", 1)[1]
        elif a.startswith("--filter-id="):
            filter_id = a.split("=", 1)[1]
        elif a.startswith("--worker-id="):
            worker_id = a.split("=", 1)[1]
        elif a.startswith("http://") or a.startswith("https://"):
            url = a

    api_url = api_url or os.environ.get("API_URL")
    filter_id = filter_id or os.environ.get("FILTER_ID")
    worker_id = worker_id or os.environ.get("WORKER_ID")
    service_key = os.environ.get("SERVICE_KEY", "")

    if api_url and filter_id:
        if not worker_id:
            import uuid
            worker_id = f"bot-{uuid.uuid4().hex[:8]}"
        bot = LGBot(
            api_url=api_url,
            filter_id=filter_id,
            worker_id=worker_id,
            service_key=service_key,
        )
        bot.fetch_config()
    else:
        if not url:
            for a in args:
                if a.startswith("http://") or a.startswith("https://"):
                    url = a
                    break
        if not url:
            url = input("Enter URL: ").strip()
        if not url:
            print("URL required")
            sys.exit(1)
        bot = LGBot(url=url)

    bot.run_forever()


if __name__ == "__main__":
    main()
