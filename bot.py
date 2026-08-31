#!/usr/bin/env python3
"""LG V2 BOT — Pure HTTP, JSON logs, never stops."""

import json
import os
import re
import signal
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
from lxml import html

try:
    from websockets.sync.client import connect as ws_connect
    HAS_WS = True
except ImportError:
    HAS_WS = False


OTP_TIMEOUT = float(os.environ.get("OTP_TIMEOUT_S", "90"))
OTP_POLL_INTERVAL = max(0.1, float(os.environ.get("OTP_POLL_INTERVAL_S", "0.25")))
# When set, the bot prints the exact data it would send to SaveBiddingData
# (including image URLs) instead of actually submitting the bid.
DRY_RUN = os.environ.get("BOT_DRY_RUN", "0") == "1"
# Re-run a full multi-page grid scan at least this often, even if page 1
# hasn't changed (catches items added on later pages).
FULL_SCAN_EVERY = int(os.environ.get("FULL_SCAN_EVERY", "20"))


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
        self.attempted_count = 0
        self.otp_received = 0
        self._detected_serials: set[str] = set()
        self._booked_serials: set[str] = set()

        self.min_price: float | None = None
        self.max_price: float | None = None
        self.dp_percent: float | None = None
        self.filter_by_price: bool = True
        self.filter_by_dp: bool = True
        self.serial_numbers: set[str] = set()
        self.scan_interval: float = 2.0
        self.email: str = ""
        self.app_password: str = ""
        self._ws: _WSPusher | None = None

        # LG keeps the session OTP-authorized after a successful ValidateOtp, so
        # once authorized we can fire SaveBiddingData directly without a new OTP
        # cycle. Cleared when LG replies "not authorized"/"expired".
        self._session_authorized = False
        # Cached inventory from the last full multi-page scan, keyed by page-1
        # serial set, so we skip re-fetching every grid page each iteration.
        self._cached_inventory: list[dict] = []
        self._cached_page1_serials: set[str] = set()
        self._last_full_scan_iter = 0

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
        self.filter_by_price = c.get("filterByPrice", True)
        self.filter_by_dp = c.get("filterByDp", True)
        self.serial_numbers = set(c.get("serialNumbers", []))
        self.scan_interval = max(0.5, c.get("scanInterval", 2000) / 1000.0)
        self.email = c.get("email", "")
        self.app_password = c.get("appPassword", "")
        _log("info", f"Profile: {c['name']} | URL: {self.url}")
        _log("info", f"Price: {'ON' if self.filter_by_price else 'OFF'} ₹{self.min_price}–₹{self.max_price} | DP: {'ON' if self.filter_by_dp else 'OFF'} ≥ {self.dp_percent}%")
        if self.serial_numbers:
            _log("info", f"Serial filter: {len(self.serial_numbers)} serial(s) exact match")
        _log("info", f"Scan: {self.scan_interval}s | Email: {self.email or 'none'}")

    def _send_kpi(self, response_time_ms: int) -> None:
        payload = {
            "worker_id": self.worker_id,
            "status": "running",
            "detected": self.detected_count,
            "booked": self.bids_placed,
            "failed": self.failed_count,
            "attempted": self.attempted_count,
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

        def price(s: str) -> float:
            try:
                return float((s or "").replace(",", "").strip())
            except (ValueError, TypeError):
                return 0.0

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
                    "basePrice": price(row.get("base_price")),
                    "batchno": row.get("batchno", ""),
                    "imageName": row.get("image_name", ""),
                    "dmgImage": row.get("dmg_image", ""),
                    "imageUrl": row.get("image_url", ""),
                    "dmgImageUrl": row.get("dmg_image_url", ""),
                    "defect": row.get("defect", ""),
                    "biddingStart": self._to_iso(row["from_date"]),
                    "biddingEnd": self._to_iso(row["to_date"]),
                    "dealerPrice": price(row["dealer_price"]),
                    "discountPct": price((row["dp"] or "").rstrip("%")),
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

    def _report_bookings(self, rows: list[dict]) -> None:
        """Report many bookings to the API concurrently (avoids N serial posts)."""
        if not self.api_url or not rows:
            return
        with ThreadPoolExecutor(max_workers=min(32, len(rows))) as pool:
            list(pool.map(self._report_booking, rows))

    def _submit_authorized(self, inventory: list[dict]) -> str:
        """Fast path: LG session is already OTP-authorized.

        Fires SaveBiddingData directly — no begin/save/validate OTP cycle. If LG
        rejects with a session/expiry error the caller re-runs the full OTP flow.

        Returns "ok", "fail" (real submit error), or "session_expired".
        """
        if DRY_RUN:
            for i, r in enumerate(inventory):
                _log("dryrun", json.dumps({
                    "item": i + 1,
                    "serial_no": r.get("serial_no"),
                    "model_code": r.get("model_code"),
                    "image_url": r.get("image_url"),
                    "dmg_image_url": r.get("dmg_image_url"),
                    "base_price": r.get("base_price"),
                    "dealer_price": r.get("dealer_price"),
                    "dp": r.get("dp"),
                    "batchno": r.get("batchno"),
                    "defect": r.get("defect"),
                }))
            _log("dryrun", f"DRY RUN — skipping SaveBiddingData for {len(inventory)} item(s)")
            return "ok"

        submit_msg = self.save_bidding_data(inventory)
        _log("info", f"SaveBiddingData: {submit_msg or 'no response'}")
        if submit_msg is None:
            return "fail"
        if not self._save_result_ok(submit_msg):
            if re.search(r"not authorized|expired|session", submit_msg, re.I):
                _log("warn", f"Session expired ({submit_msg[:120]}) — will re-run OTP")
                return "session_expired"
            _log("error", f"LG rejected submission: {submit_msg}")
            return "fail"
        self._report_bookings(inventory)
        for row in inventory:
            self._booked_serials.add(row["serial_no"])
        _log("success", f"Reported {len(inventory)} booking(s) to API (authorized fast path)")
        return "ok"

    def _filter_inventory(self, inventory: list[dict]) -> list[dict]:
        def price(s: str) -> float:
            return float(s.replace(",", "").strip()) if s.strip() else 0.0

        def dp(s: str) -> float:
            return float(s.strip().rstrip("%")) if s.strip() else 0.0

        filtered = []
        for r in inventory:
            # Serial number exact-match filter (when list is non-empty, only match those serials)
            if self.serial_numbers and r["serial_no"] not in self.serial_numbers:
                continue

            # Price filter (only when toggled ON)
            if self.filter_by_price:
                p = price(r["dealer_price"])
                if self.min_price is not None and p < self.min_price:
                    _log("scan", f"Reject {r['serial_no'][:15]}: price ₹{r['dealer_price']} < min ₹{self.min_price}")
                    continue
                if self.max_price is not None and p > self.max_price:
                    _log("scan", f"Reject {r['serial_no'][:15]}: price ₹{r['dealer_price']} > max ₹{self.max_price}")
                    continue

            # Discount % filter (only when toggled ON)
            if self.filter_by_dp:
                d = dp(r["dp"])
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
        dropdowns = {}
        for select in tree.xpath('//select'):
            name = select.get("name")
            if not name:
                continue
            options = select.xpath('./option')
            chosen = None
            for opt in options:
                if opt.get("selected") is not None:
                    chosen = opt.get("value")
                    break
            if chosen is None and options:
                chosen = options[0].get("value")
            dropdowns[name] = chosen or "--Select--"
        if dropdowns:
            self.dropdowns = dropdowns
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

    GRID_ID = "gvNGSIDetails"
    GRID_TARGET = "ctl00$ContentPlaceHolder1$gvNGSIDetails"

    @staticmethod
    def parse_pager(tree: html.HtmlElement) -> tuple[int, list[str], dict[int, str]]:
        """Return (current_page, page_args, label_to_arg) from the grid's pager row.

        ASP.NET GridView renders the pager as <a href="...__doPostBack(...Page$N...)">1</a>
        for non-current pages and a <span>N</span> for the current page. We return the
        raw `Page$N` arguments from the links (server-generated, so no guessing about
        0- vs 1-based indexing), the current page number, and a mapping from visible
        page label to its postback argument.
        """
        table = tree.xpath(f'//table[@id="{LGBot.GRID_ID}"]')
        if not table:
            return 1, [], {}
        pager = table[0].xpath(
            './/tr[contains(concat(" ", normalize-space(@class), " "), " paging ")]'
        )
        if not pager:
            return 1, [], {}
        current = 1
        for s in pager[0].xpath('.//span'):
            t = (s.text or "").strip()
            if t.isdigit():
                current = int(t)
        args: list[str] = []
        labels: dict[int, str] = {}
        for a in pager[0].xpath('.//a[contains(@href, "Page$")]'):
            m = re.search(r"Page\$(\d+)", a.get("href", ""))
            label = (a.text or "").strip()
            if m:
                arg = f"Page${m.group(1)}"
                args.append(arg)
                if label.isdigit():
                    labels[int(label)] = arg
        return current, args, labels

    @staticmethod
    def _derive_page_arg(current: int, labels: dict[int, str]) -> str:
        """Derive the postback arg for the *current* page from the observed links.

        Link labels are the visible page numbers; their hrefs carry the raw `Page$N`
        args. The constant offset (arg_number - label) reveals the grid's indexing
        scheme, so we can rebuild the arg for the page we're standing on. Falls back
        to `Page$<current>` when nothing can be derived.
        """
        offsets = []
        for label, arg in labels.items():
            m = re.search(r"Page\$(\d+)", arg)
            if m:
                offsets.append(int(m.group(1)) - label)
        if offsets:
            offset = offsets[0]
            return f"Page${current + offset}"
        return f"Page${current}"

    def parse_inventory(self, tree: html.HtmlElement) -> list[dict]:
        table = tree.xpath(f'//table[@id="{LGBot.GRID_ID}"]')
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
                return (els[0].text_content() or "").strip() if els else ""

            # Check both attachment <a> links and <img> @src attributes.
            full_img_href = tr.xpath('.//a[contains(@id, "lnkImgAttachment")]/@href | .//img/@src')
            dmg_img_href = tr.xpath('.//a[contains(@id, "lnkDmgImgAttachment")]/@href')

            image_url = self._resolve_img(full_img_href[0]) if full_img_href else ""
            if dmg_img_href:
                dmg_url = self._resolve_img(dmg_img_href[0])
            elif len(full_img_href) > 1:
                dmg_url = self._resolve_img(full_img_href[1])
            else:
                dmg_url = ""

            inventory.append({
                "checkbox_name": cb[0].get("name"),
                "base_price":    g('.//*[contains(@id,"lblfulbaseprice")]'),
                "batchno":       g('.//*[contains(@id,"lblbatchnum")]'),
                "image_name":    g('.//*[contains(@id,"lblImageName")]'),
                "dmg_image":     g('.//*[contains(@id,"lblDmgImg2Name")]'),
                "image_url":     image_url,
                "dmg_image_url": dmg_url,
                "serial_no":     g('.//*[contains(@id,"lblsrno")]'),
                "model_code":    g('.//*[contains(@id,"lblModelCode")]'),
                "product":       g('.//*[contains(@id,"lblProduct")]'),
                "branch":        g('.//*[contains(@id,"lblBranch")]'),
                "bill_branch":   g('.//*[contains(@id,"lblBill_Branch") or contains(@id,"lblBillBranch")]'),
                "category":      g('.//*[contains(@id,"lblDiscCategory")]'),
                "from_date":     g('.//*[contains(@id,"lblfromdate")]'),
                "to_date":       g('.//*[contains(@id,"lbltodate")]'),
                "dealer_price":  g('.//*[contains(@id,"lbldealerp")]'),
                "dp":            g('.//*[contains(@id,"lbldp")]'),
                "defect":        g('.//*[contains(@id,"lbltypeofdefect")]'),
            })
        return inventory

    def _resolve_img(self, src: str) -> str:
        """Resolve an image src or href to an absolute URL against the page URL."""
        if not src:
            return ""
        src = src.strip().replace("\\", "/")
        try:
            from urllib.parse import urljoin
            return urljoin(self.url, src)
        except Exception:
            return src

    def _fetch_page(self, page_arg: str) -> html.HtmlElement | None:
        """Fire the __doPostBack pager link for the given Page$N arg."""
        data = {
            "__VIEWSTATE": self.viewstate,
            "__EVENTVALIDATION": self.eventvalidation,
            "__VIEWSTATEGENERATOR": self.generator,
            "__EVENTTARGET": self.GRID_TARGET,
            "__EVENTARGUMENT": page_arg,
            "__LASTFOCUS": "",
            **self.dropdowns,
        }
        resp = self._post(data, f"PAGE {page_arg}")
        if resp is None:
            return None
        try:
            tree = html.fromstring(resp.content)
        except Exception:
            return None
        self._extract_hidden(tree)
        return tree

    def collect_pages(self, tree: html.HtmlElement) -> list[tuple[str | None, list[dict]]]:
        """Parse every grid page, returning (page_arg_or_None, rows) per page.

        The first entry is the page the initial tree already shows; its page_arg
        is derived from the observed pager links so callers can re-navigate to it.
        The grid is left on the last page fetched — callers must re-navigate before
        submitting (each page's checkboxes only exist in that page's viewstate).
        """
        current, page_args, labels = self.parse_pager(tree)
        _log("scan", f"Grid page {current} | {len(self.parse_inventory(tree))} item(s) on current page")
        initial_arg = None if not page_args else self._derive_page_arg(current, labels)
        pages: list[tuple[str | None, list[dict]]] = [(initial_arg, self.parse_inventory(tree))]
        seen: set[str] = {initial_arg}
        pending = [a for a in page_args if a != initial_arg]
        while pending:
            arg = pending.pop(0)
            if arg in seen:
                continue
            seen.add(arg)
            tree2 = self._fetch_page(arg)
            if tree2 is None:
                _log("warn", f"Failed to load grid page {arg}")
                continue
            rows = self.parse_inventory(tree2)
            _log("scan", f"Grid page {arg}: {len(rows)} item(s)")
            pages.append((arg, rows))
            _, more_args, more_labels = self.parse_pager(tree2)
            for a in more_args:
                if a not in seen and a not in pending:
                    pending.append(a)
        return pages

    def collect_inventory(self, tree: html.HtmlElement) -> list[dict]:
        """Flatten all grid pages into one de-duped inventory list."""
        all_rows = []
        for _, rows in self.collect_pages(tree):
            all_rows.extend(rows)
        return self._dedup_inventory(all_rows)

    @staticmethod
    def _dedup_inventory(rows: list[dict]) -> list[dict]:
        seen: set[str] = set()
        deduped = []
        for r in rows:
            key = r["serial_no"]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        return deduped

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
            _log("warn", f"{endpoint} non-json response: {resp.text[:500]}")
            return None
        _log("info", f"{endpoint} raw d: {str(data.get('d'))[:600]}")
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
            def num(s: str) -> int | float:
                try:
                    v = float((s or "").replace(",", "").strip())
                except ValueError:
                    return 0
                # LG expects the exact portal value: "50" stays 50, "420000"
                # stays 420000. A trailing .0 on a whole number is rejected.
                return int(v) if v.is_integer() else v
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
        # The API answers "lock busy" with HTTP 409. That is NOT an error for
        # this worker — treat it as busy so we never count it as a failure.
        if resp.status_code == 409:
            return {"status": "busy"}
        try:
            return resp.json()
        except Exception:
            _log("warn", f"otp/{method} HTTP {resp.status_code} | non-json response")
            return None

    # LG returns the `d` string from SaveBiddingData. Empty/normal text means
    # accepted; errors come back as human text like
    # "You are not Authorized for this Activity..!"
    SAVE_ERR = re.compile(
        r"not authorized|not allowed|failed|error|invalid|expired|already|"
        r"not selected|please select|can't|cannot|rejected|try again",
        re.I,
    )

    def _save_result_ok(self, submit_msg: str | None) -> bool:
        """Decide if a SaveBiddingData response is a success or an LG error."""
        if not submit_msg:
            return True  # empty `d` = success
        return not self.SAVE_ERR.search(submit_msg)

    def fetch_otp(self, inventory: list[dict], trigger_rows: list[dict] | None = None) -> str:
        """Run the OTP flow and submit the given items.

        `inventory` holds every item to submit — all go into a single
        SaveBiddingData call (it is a stateless page method, so batching across
        grid pages is safe). `trigger_rows` (defaults to `inventory`) are the
        rows used for the btnSave postback that emails the OTP; they must belong
        to the currently displayed grid page so their checkbox names are valid
        in that page's __VIEWSTATE.

        Returns one of:
          "ok"   — OTP validated and bids submitted
          "busy" — another worker holds the OTP lock (NOT a failure; retry later)
          "fail" — a real error (IMAP/HTTP/validation/submit)
        """
        if not self.ship_to:
            _log("warn", "No ship-to code; skipping OTP flow")
            return "fail"
        _log("action", f"OTP flow for {len(inventory)} item(s) — begin")

        begin = self._otp_api("begin", {
            "worker_id": self.worker_id,
            "ship_to": self.ship_to,
            "email": self.email,
            "app_password": self.app_password,
            "item_count": len(inventory),
        })
        if not begin:
            return "fail"
        if begin.get("status") == "busy":
            _log("warn", "OTP lock held by another worker — retrying next iteration")
            return "busy"
        req_id = begin.get("otp_request_id")
        if not req_id:
            _log("error", f"OTP begin failed: {begin}")
            return "fail"

        # Click Submit → portal emails the OTP (never submit the OTP itself).
        # `trigger_rows` must only contain rows from the currently displayed
        # grid page so the checkbox names are valid in the page's __VIEWSTATE.
        trigger = trigger_rows if trigger_rows is not None else inventory
        resp = self.save(trigger)
        if resp is None:
            _log("error", "Submit failed; cancelling OTP request")
            self._otp_api("cancel", {"otp_request_id": req_id})
            return "fail"

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
                        return "fail"

                    _log("info", "OTP validated successfully")
                    if DRY_RUN:
                        # Print everything we captured (image URLs included) instead
                        # of actually submitting to SaveBiddingData.
                        for i, r in enumerate(inventory):
                            _log("dryrun", json.dumps({
                                "item": i + 1,
                                "serial_no": r.get("serial_no"),
                                "model_code": r.get("model_code"),
                                "branch": r.get("branch"),
                                "product": r.get("product"),
                                "category": r.get("category"),
                                "base_price": r.get("base_price"),
                                "dealer_price": r.get("dealer_price"),
                                "dp": r.get("dp"),
                                "batchno": r.get("batchno"),
                                "defect": r.get("defect"),
                                "image_name": r.get("image_name"),
                                "dmg_image": r.get("dmg_image"),
                                "image_url": r.get("image_url"),
                                "dmg_image_url": r.get("dmg_image_url"),
                                "from_date": r.get("from_date"),
                                "to_date": r.get("to_date"),
                            }))
                        _log("dryrun", f"DRY RUN — skipping SaveBiddingData for {len(inventory)} item(s)")
                        self._session_authorized = True
                        self._otp_api("end", {"otp_request_id": req_id})
                        return "ok"

                    submit_msg = self.save_bidding_data(inventory)
                    _log("info", f"SaveBiddingData: {submit_msg or 'no response'}")
                    if submit_msg is None:
                        self._otp_api("cancel", {"otp_request_id": req_id})
                        return "fail"
                    if not self._save_result_ok(submit_msg):
                        _log("error", f"LG rejected submission: {submit_msg}")
                        self._otp_api("cancel", {"otp_request_id": req_id})
                        return "fail"

                    # OTP validated + submit accepted → LG session stays
                    # authorized; reuse it next iteration (no new OTP cycle).
                    self._session_authorized = True
                    self._report_bookings(inventory)
                    for row in inventory:
                        self._booked_serials.add(row["serial_no"])
                    _log("success", f"Reported {len(inventory)} booking(s) to API")
                    self._otp_api("end", {"otp_request_id": req_id})
                    return "ok"
                if status in ("timeout", "error", "cancelled"):
                    _log("warn", f"OTP fetch ended: {status}")
                    break
            time.sleep(OTP_POLL_INTERVAL)

        _log("warn", "OTP not received in time")
        self._otp_api("cancel", {"otp_request_id": req_id})
        return "fail"

    def _run_full_otp(self, matched: list[dict], pages: list) -> str:
        """Full OTP path for the given matched items.

        Finds a grid page containing a match (so the btnSave postback that emails
        the OTP has valid checkboxes in that page's __VIEWSTATE), then runs the
        OTP flow over the whole matched set in a single SaveBiddingData call.

        Returns "ok", "busy", or "fail" (see fetch_otp).
        """
        matched_serials = {r["serial_no"] for r in matched}
        trigger_rows: list[dict] = []
        for page_arg, rows in pages:
            page_matched = [r for r in rows if r["serial_no"] in matched_serials]
            if not page_matched:
                continue
            if page_arg is not None:
                tree_pg = self._fetch_page(page_arg)
                if tree_pg is None:
                    _log("warn", f"Failed to re-open grid page {page_arg} for submit")
                    return "fail"
            trigger_rows = page_matched
            break

        if not trigger_rows:
            _log("warn", "No grid page with a matching checkbox found for OTP trigger")
            return "fail"

        result = self.fetch_otp(matched, trigger_rows=trigger_rows)
        if result == "ok":
            _log("success", f"Booked {len(matched)} item(s) in a single SaveBiddingData call")
        elif result == "busy":
            _log("warn", "OTP lock busy — retrying next iteration")
        else:
            _log("error", f"OTP flow failed for {len(matched)} item(s)")
        return result

    # ───────── forever loop ─────────

    def run_forever(self) -> None:
        _log("info", f"Bot started | URL: {self.url} | Worker: {self.worker_id or 'legacy'}")
        if self.filter_id:
            _log("info", f"Filter: {self.filter_id}")

        stopping = False

        def _on_stop(signum, frame):
            nonlocal stopping
            stopping = True
            _log("info", f"Received {signal.Signals(signum).name} — stopping | booked={self.bids_placed} detected={self.detected_count} failed={self.failed_count}")

        signal.signal(signal.SIGTERM, _on_stop)
        signal.signal(signal.SIGINT, _on_stop)

        while True:
            if stopping:
                _log("info", "Bot stopped gracefully")
                break
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

                # Fast scan: when the first grid page is unchanged we already have
                # the full inventory cached, so we skip re-fetching every grid
                # page. A full scan still runs on a change or every FULL_SCAN_EVERY
                # iterations (catches items added on later pages).
                page1 = self.parse_inventory(tree)
                page1_serials = {r["serial_no"] for r in page1}
                # Only reuse the cache on the authorized fast path (which needs no
                # trigger page). When an OTP cycle is required we must do a full
                # scan so the btnSave trigger has valid checkboxes for a real page.
                cache_hit = (
                    self._session_authorized
                    and self._cached_inventory
                    and page1_serials == self._cached_page1_serials
                    and (self.iter - self._last_full_scan_iter) < FULL_SCAN_EVERY
                )
                if cache_hit:
                    inventory = self._cached_inventory
                    pages: list = []
                    _log("scan", f"Grid page-1 unchanged — reusing cached {len(inventory)} item(s)")
                else:
                    pages = self.collect_pages(tree)
                    inventory = [r for _, rows in pages for r in rows]
                    inventory = self._dedup_inventory(inventory)
                    self._cached_inventory = inventory
                    self._cached_page1_serials = page1_serials
                    self._last_full_scan_iter = self.iter
                    _log("scan", f"Found {len(inventory)} item(s) across grid")

                if not inventory:
                    _log("scan", "No items on page, sending search…")
                    tree2 = self.search()
                    if tree2 is not None:
                        pages = self.collect_pages(tree2)
                        inventory = self._dedup_inventory([r for _, rows in pages for r in rows])
                        self._cached_inventory = inventory
                        self._cached_page1_serials = {r["serial_no"] for r in inventory}
                        self._last_full_scan_iter = self.iter
                        _log("scan", f"Found {len(inventory)} item(s) after search")
                    else:
                        _log("warn", "Search failed")

                if inventory:
                    # Never re-submit items already booked successfully.
                    pending = [r for r in inventory if r["serial_no"] not in self._booked_serials]
                    if len(pending) != len(inventory):
                        _log("scan", f"Skipping {len(inventory) - len(pending)} already-booked item(s)")
                    matched = self._filter_inventory(pending)
                    _log("scan", f"{len(matched)} of {len(inventory)} item(s) match price/DP filter")

                    if matched:
                        for r in matched:
                            self._detected_serials.add(r["serial_no"])
                        self.detected_count = len(self._detected_serials)
                        for i, r in enumerate(matched):
                            _log("detect", f"Match #{i+1}: {r['serial_no'][:15]} | {r['model_code']} | ₹{r['dealer_price']} | {r['dp']}%")

                        # Fast path: LG keeps the session OTP-authorized after the
                        # first successful cycle, so once authorized we submit the
                        # whole matched set with a single SaveBiddingData call —
                        # no begin/save/OTP/validate. SaveBiddingData is a stateless
                        # page method (full selectedRows array), so it has no page
                        # or checkbox requirement.
                        if self._session_authorized:
                            result = self._submit_authorized(matched)
                            if result == "ok":
                                self.bids_placed += len(matched)
                                self.attempted_count += len(matched)
                                _log("success", f"Booked {len(matched)} item(s) in a single SaveBiddingData call")
                            elif result == "session_expired":
                                # Session died (LG side) — not a booking failure.
                                # If we have a real grid page we re-run OTP now;
                                # otherwise next iteration does a full scan + OTP.
                                self._session_authorized = False
                                _log("warn", "Session expired — re-running full OTP cycle")
                                if pages:
                                    result = self._run_full_otp(matched, pages)
                                    if result == "ok":
                                        self.bids_placed += len(matched)
                                        self.attempted_count += len(matched)
                                    elif result != "busy":
                                        self.failed_count += len(matched)
                                        self.attempted_count += len(matched)
                                        _log("error", f"OTP flow failed for {len(matched)} item(s)")
                                else:
                                    _log("warn", "Retrying with a full OTP cycle next iteration")
                            else:
                                self.failed_count += len(matched)
                                self.attempted_count += len(matched)
                                _log("error", f"Authorized submit failed for {len(matched)} item(s)")
                        else:
                            result = self._run_full_otp(matched, pages)
                            if result == "ok":
                                self.bids_placed += len(matched)
                                self.attempted_count += len(matched)
                                _log("success", f"Booked {len(matched)} item(s) in a single SaveBiddingData call")
                            elif result == "busy":
                                _log("warn", "OTP lock busy — retrying next iteration")
                            else:
                                self.failed_count += len(matched)
                                self.attempted_count += len(matched)
                                _log("error", f"OTP flow failed for {len(matched)} item(s)")
                    else:
                        _log("scan", "No items match filter criteria")
                else:
                    _log("scan", "No inventory found")

            except Exception as e:
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
    service_key = (os.environ.get("SERVICE_KEY", "") or "").strip()

    # Each k8s replica shares the release name as WORKER_ID; append the pod
    # hostname so every worker has a unique identity (KPI keys + OTP lock).
    # Keep it short — the OTP lock 'holder' column is VARCHAR(255).
    pod_name = os.environ.get("HOSTNAME", "").strip()
    if pod_name:
        pod_tail = pod_name.split("-")[-2:]  # e.g. 865f46857b-6ctkf
        suffix = "-".join(pod_tail)
        if suffix and suffix not in (worker_id or ""):
            worker_id = f"{worker_id or 'bot'}-{suffix}"
        worker_id = (worker_id or "bot")[:128]

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
