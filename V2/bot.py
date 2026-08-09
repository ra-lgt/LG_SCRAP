#!/usr/bin/env python3
"""LG V2 BOT — Pure HTTP, JSON logs, never stops."""

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime

import httpx
from lxml import html

try:
    from websockets.sync.client import connect as ws_connect
    HAS_WS = True
except ImportError:
    HAS_WS = False


OTP_TIMEOUT = float(os.environ.get("OTP_TIMEOUT_S", "90"))
OTP_POLL_INTERVAL = float(os.environ.get("OTP_POLL_INTERVAL_S", "2"))


def _log(level: str, message: str) -> None:
    print(json.dumps({"level": level, "message": message}))


class _WSPusher:
    """Lazy persistent WebSocket to the API for KPI pushes, with auto-reconnect.

    Falls back to the caller when no connection can be established, so the bot
    never blocks or dies on a dead socket.
    """

    def __init__(self, api_url: str, service_key: str):
        self.api_url = api_url
        self.service_key = service_key
        self._ws = None

    def _url(self) -> str:
        base = self.api_url.replace("http://", "ws://").replace("https://", "wss://")
        return f"{base}/ws/bot?service_key={self.service_key}"

    def send(self, message: dict) -> bool:
        if not HAS_WS:
            return False
        if self._ws is None:
            try:
                self._ws = ws_connect(self._url(), open_timeout=5, send_timeout=5)
            except Exception:
                self._ws = None
                return False
        try:
            self._ws.send(json.dumps(message))
            return True
        except Exception:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
            return False


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
        self.otp_received = 0
        self._detected_serials: set[str] = set()

        self.min_price: float | None = None
        self.max_price: float | None = None
        self.dp_percent: float | None = None
        self.scan_interval: float = 2.0
        self.email: str = ""
        self.app_password: str = ""
        self._ws: _WSPusher | None = None

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
        payload = {
            "worker_id": self.worker_id,
            "status": "running",
            "detected": self.detected_count,
            "booked": self.bids_placed,
            "failed": self.failed_count,
            "otp_received": self.otp_received,
            "response_time_ms": response_time_ms,
            "iteration": self.iter,
            "filter_id": self.filter_id or "",
        }
        if not self.api_url:
            return
        if self._ws is None:
            self._ws = _WSPusher(self.api_url, self.service_key)
        if self._ws.send({"type": "kpi", "data": payload}):
            return
        try:
            httpx.post(
                f"{self.api_url}/api/bot/report",
                json=payload,
                headers={"X-SERVICE-KEY": self.service_key},
                timeout=3,
            )
        except Exception:
            pass

    @staticmethod
    def _to_iso(date_str: str) -> str:
        """Convert '22-Jan-2026' (portal format) to '2026-01-22' (ISO 8601)."""
        try:
            return datetime.strptime(date_str.strip(), "%d-%b-%Y").isoformat()
        except (ValueError, TypeError):
            return date_str.strip()

    def _report_booking(self, row: dict) -> None:
        if not self.api_url:
            return
        try:
            resp = httpx.post(
                f"{self.api_url}/api/bot/bookings",
                json={
                    "serialNo": row["serial_no"],
                    "modelCode": row["model_code"],
                    "product": row["product"],
                    "branch": row["branch"],
                    "billBranch": row["bill_branch"],
                    "category": row["category"],
                    "biddingStart": self._to_iso(row["from_date"]),
                    "biddingEnd": self._to_iso(row["to_date"]),
                    "dealerPrice": float(row["dealer_price"].replace(",", "").strip()),
                    "discountPct": float((row["dp"] or "").replace("%", "").strip() or 0),
                    "status": "confirmed",
                    "workerId": self.worker_id,
                    "filterId": self.filter_id or "",
                },
                headers={"X-SERVICE-KEY": self.service_key},
                timeout=5,
            )
            if resp.status_code != 200:
                _log("warn", f"Booking report HTTP {resp.status_code} | {resp.text[:200]}")
        except Exception as e:
            _log("warn", f"Booking report failed: {e}")

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
                _log("scan", f"Reject {r['serial_no'][:15]}: price ₹{r['dealer_price']} < min ₹{self.min_price}")
                continue
            if self.max_price is not None and p > self.max_price:
                _log("scan", f"Reject {r['serial_no'][:15]}: price ₹{r['dealer_price']} > max ₹{self.max_price}")
                continue
            if self.dp_percent is not None and d < self.dp_percent:
                _log("scan", f"Reject {r['serial_no'][:15]}: DP {d}% < {self.dp_percent}%")
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
            options = select[0].xpath('./option')
            chosen = None
            for opt in options:
                if opt.get("selected") is not None:
                    chosen = opt.get("value")
                    break
            if chosen is None and options:
                chosen = options[0].get("value")
            self.dropdowns[ctl_name] = chosen or "--Select--"
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

    def _page_method_url(self, endpoint: str) -> str:
        """Build the page-method URL exactly as the browser does.

        ASP.NET page methods are POSTed to <page-dir>/<page-file>/<method> where
        <page-file> comes from PageMethods.set_path(...) in the served HTML.
        E.g. page https://host/pod/?Code=X with set_path("NGSI_CustomerBiddingInput.aspx")
        -> https://host/pod/NGSI_CustomerBiddingInput.aspx/ValidateOtp.
        """
        base = self.url.split("?")[0].rstrip("/")
        if base.lower().endswith(".aspx"):
            directory = base.rsplit("/", 1)[0]
        else:
            directory = base
        page = ""
        if self.last_html:
            m = re.search(r'PageMethods\.set_path\("([^"]+)"\)', self.last_html)
            if m:
                page = m.group(1).lstrip("/")
        if not page:
            page = "NGSI_CustomerBiddingInput.aspx"
        return f"{directory}/{page}/{endpoint}"

    def _page_method(self, endpoint: str, payload: dict) -> str | None:
        """Call an ASP.NET AJAX page method ({page}/{method}).

        Returns response.d (the .NET-serialized string), or None on transport /
        HTTP error. Content-Type must be application/json and the session cookie
        from self.sess is reused.
        """
        if not self.url:
            return None
        url = self._page_method_url(endpoint)
        try:
            resp = self.sess.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
        except Exception as e:
            _log("warn", f"page method {endpoint} failed: {e}")
            return None
        _log("action", f"{endpoint} HTTP {resp.status_code} | {len(resp.content):,}B")
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        return data.get("d")

    def validate_otp(self, otp: str) -> str | None:
        """POST ValidateOtp page method. Returns 'SUCCESS' (str) or error text."""
        if not self.ship_to:
            _log("warn", "No ship-to code; cannot validate OTP")
            return None
        return self._page_method("ValidateOtp", {
            "enteredOtp": otp,
            "shipToCode": self.ship_to,
        })

    def save_bidding_data(self, inventory: list[dict]) -> str | None:
        """POST SaveBiddingData page method with the selected rows.

        Mirrors the page's saveBiddingData() JS: only checked rows are sent, so
        we pass every filtered row (the bot checks them all in save()).
        """
        selected = []
        for r in inventory:
            def num(s: str) -> float:
                try:
                    return float((s or "").replace(",", "").strip())
                except ValueError:
                    return 0.0
            selected.append({
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
        return self._page_method("SaveBiddingData", {"selectedRows": selected})

    def _otp_api(self, method: str, payload: dict) -> dict | None:
        if not self.api_url:
            _log("error", "No API URL — cannot run OTP flow")
            return None
        try:
            resp = httpx.post(
                f"{self.api_url}/api/bot/otp/{method}",
                json=payload,
                headers={"X-SERVICE-KEY": self.service_key},
                timeout=30,
            )
        except Exception as e:
            _log("warn", f"otp/{method} failed: {e}")
            return None
        try:
            return resp.json()
        except Exception:
            _log("warn", f"otp/{method} HTTP {resp.status_code} | non-json response")
            return None

    def fetch_otp(self, inventory: list[dict]) -> bool:
        if not self.ship_to:
            _log("warn", "No ship-to code; skipping OTP flow")
            return False
        _log("action", f"OTP flow for {len(inventory)} item(s) — begin")

        begin = self._otp_api("begin", {
            "worker_id": self.worker_id,
            "ship_to": self.ship_to,
            "email": self.email,
            "app_password": self.app_password,
            "item_count": len(inventory),
        })
        if not begin:
            return False
        if begin.get("status") == "busy":
            _log("warn", "OTP lock held by another worker — retrying next iteration")
            return False
        req_id = begin.get("otp_request_id")
        if not req_id:
            _log("error", f"OTP begin failed: {begin}")
            return False

        # Click Submit → portal emails the OTP (never submit the OTP itself)
        resp = self.save(inventory)
        if resp is None:
            _log("error", "Submit failed; cancelling OTP request")
            self._otp_api("cancel", {"otp_request_id": req_id})
            return False

        body_html = re.search(r"<body[^>]*>(.*?)</body>", self.last_html, re.S)
        raw = body_html.group(1) if body_html else self.last_html
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
        real_otp_input = bool(re.search(r'id="[^"]*[Oo][Tt][Pp][^"]*"[^>]*type="text"|type="text"[^>]*id="[^"]*[Oo][Tt][Pp]', raw))
        m = re.search(r"(?:Enter OTP|enter your otp|OTP has been sent|already|already selected|not selected|please select|invalid|expired|sent to your|submitted successfully|not allowed)", txt, re.I)
        hint = m.group(0) if m else txt[-500:]
        _log("info", f"Submit page: OTP-input={real_otp_input} | {hint}")
        if real_otp_input:
            mm = re.search(r".{0,200}Enter OTP.{0,400}", txt, re.I)
            if mm:
                _log("info", f"Submit page OTP dialog: {mm.group(0)}")

        deadline = time.monotonic() + OTP_TIMEOUT
        while time.monotonic() < deadline:
            res = self._otp_api("fetch", {"otp_request_id": req_id})
            if res:
                status = res.get("status")
                if status == "done":
                    otp = res.get("otp")
                    self.otp_received += 1
                    _log("otp", f"OTP={otp} items={len(inventory)} worker={self.worker_id}")
                    if res.get("raw_snippet"):
                        _log("info", f"OTP email: {res['raw_snippet']}")

                    validated = self.validate_otp(otp)
                    if validated != "SUCCESS":
                        _log("error", f"OTP validation failed: {validated or 'HTTP error'}")
                        self._otp_api("cancel", {"otp_request_id": req_id})
                        self.failed_count += 1
                        return False

                    _log("info", "OTP validated successfully")
                    submit_msg = self.save_bidding_data(inventory)
                    _log("info", f"SaveBiddingData: {submit_msg or 'no response'}")
                    if submit_msg is None:
                        self._otp_api("cancel", {"otp_request_id": req_id})
                        self.failed_count += 1
                        return False

                    for row in inventory:
                        self._report_booking(row)
                    self.bids_placed += len(inventory)
                    _log("success", f"Reported {len(inventory)} booking(s) to API")
                    self._otp_api("end", {"otp_request_id": req_id})
                    return True
                if status in ("timeout", "error", "cancelled"):
                    _log("warn", f"OTP fetch ended: {status}")
                    break
            time.sleep(OTP_POLL_INTERVAL)

        _log("warn", "OTP not received in time")
        self._otp_api("cancel", {"otp_request_id": req_id})
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
                        for r in matched:
                            self._detected_serials.add(r["serial_no"])
                        self.detected_count = len(self._detected_serials)
                        for i, r in enumerate(matched):
                            _log("detect", f"Match #{i+1}: {r['serial_no'][:15]} | {r['model_code']} | ₹{r['dealer_price']} | {r['dp']}%")
                        ok = self.fetch_otp(matched)
                        if ok:
                            _log("success", f"OTP received for {len(matched)} item(s)")
                        else:
                            self.failed_count += 1
                            _log("error", "OTP fetch failed or timed out for this batch")
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
