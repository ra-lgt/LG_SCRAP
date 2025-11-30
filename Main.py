import threading
import time
from concurrent.futures import ThreadPoolExecutor

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from mail import get_latest_mail_from, extract_otp, delete_mail_by_id


# 🔐 Only ONE thread should handle OTP popup
OTP_LOCK = threading.Lock()


class LG_SCRAP:
    def __init__(self, url, min_amount=0, max_amount=0, percentage=[]):
        self.url = url
        self.options = Options()
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")

        self.driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"),
            options=self.options
        )

        self.wait = WebDriverWait(self.driver, 15)
        self.max_amount = max_amount
        self.min_amount = min_amount
        self.percentage = percentage

    # Run after OTP success
    def close_all_popup(self):
        try:
            for _ in range(30):  # try 30 times to close confirm dialogs
                self.driver.execute_script("window.confirm = () => true;")
                time.sleep(0.2)
        except:
            pass

    def start_scrap(self):
        thread_id = threading.get_ident()
        print(f"[THREAD {thread_id}] 🚗 Browser Started")

        self.driver.get(self.url)
        print(f"[THREAD {thread_id}] 🌍 Page Loaded")

        while True:
            try:
                # Auto confirm popups
                self.driver.execute_script("window.confirm = () => true;")
            except:
                pass

            try:
                # Check if table exists
                table_exists = self.driver.execute_script(
                    "return document.getElementsByTagName('table').length > 0;"
                )

                if table_exists:
                    print(f"[THREAD {thread_id}] 📊 Table Found")

                    js_array = ",".join(map(str, self.percentage))

                    # JS to select checkboxes
                    script = f"""
                        const table = document.getElementsByTagName('table')[0];
                        var rows = Array.from(table.getElementsByTagName('tr'));
                        var clickList = [];

                        rows.forEach((row, idx) => {{
                            if (idx === 0) return;

                            let price = row.querySelectorAll('td')[11];
                            let percent = row.querySelectorAll('td')[12];

                            if (price) {{
                                let priceVal = parseFloat(price.querySelector('span').innerHTML.replace(',', ''));
                                let percVal = parseFloat(percent.querySelector('span').innerHTML);

                                if ([{js_array}].includes(percVal)) {{
                                    if (priceVal >= {self.min_amount} && priceVal <= {self.max_amount}) {{
                                        let cb = row.querySelector("input[type='checkbox']");
                                        if (cb) clickList.push(cb);
                                    }}
                                }}
                            }}
                        }});

                        clickList.forEach(cb => cb.click());
                        return clickList.length;
                    """

                    clicked = self.driver.execute_script(script)
                    print(f"[THREAD {thread_id}] ☑️ Selected {clicked} rows")

                    if clicked > 0:
                        print(f"[THREAD {thread_id}] 💾 Clicking SAVE")
                        self.driver.execute_script("document.getElementById('btnSave').click()")

                        # Acquire OTP LOCK
                        if OTP_LOCK.acquire(blocking=False):
                            print(f"[THREAD {thread_id}] 🔐 OTP_LOCK acquired")

                            try:
                                input_field = self.wait.until(
                                    EC.presence_of_element_located((By.ID, "otpInput"))
                                )
                                print(f"[THREAD {thread_id}] ⏳ Waiting for OTP Mail")

                                otp = None
                                for _ in range(8):
                                    msg = get_latest_mail_from("LG_GRADE_A_SALES@lge.com")
                                    if msg:
                                        otp = extract_otp(msg["body"])
                                        if otp:
                                            print(f"[THREAD {thread_id}] 🔢 OTP: {otp}")
                                            break
                                    time.sleep(2)
                                import pdb;pdb.set_trace()

                                if otp:
                                    input_field.send_keys(otp)

                                    print(f"[THREAD {thread_id}] 📤 Submitting OTP")
                                    self.driver.execute_script(
                                        "document.querySelector(\"input[title='Place Order']\").click();"
                                    )

                                    delete_mail_by_id(msg["latest_id"])

                                    print(f"[THREAD {thread_id}] 🧹 Closing popups…")
                                    self.close_all_popup()

                                else:
                                    print(f"[THREAD {thread_id}] ❌ OTP not found")

                            finally:
                                OTP_LOCK.release()
                                print(f"[THREAD {thread_id}] 🔓 OTP_LOCK released")

                # Continue pagination and loop
                self.driver.refresh()

            except Exception as e:
                print(f"[THREAD {thread_id}] ❌ Error: {e}")
                self.driver.refresh()


def run_scraper(url, count, min_amount, max_amount, percentage):
    print(f"\n🚀 Starting {count} thread(s) scraper\n")

    with ThreadPoolExecutor(max_workers=count) as executor:
        for _ in range(count):
            executor.submit(
                LG_SCRAP(url, min_amount, max_amount, percentage).start_scrap
            )


if __name__ == "__main__":
    url = "https://www.lg4all.com/POD/NGSI_CustomerBiddingInput.aspx?ReturnUrl=%2fpod%2f%3fCode%3dIN055255001H&Code=IN055255001H"
    min_amount =45999 
    max_amount =46000 
    percentage = [50, 70]

    run_scraper(url, count=1, min_amount=min_amount, max_amount=max_amount, percentage=percentage)
