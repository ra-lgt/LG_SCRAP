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


OTP_LOCK = threading.Lock()

URL_REFRESH_EVENT = threading.Event()


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

        self.wait = WebDriverWait(self.driver, 10)
        self.max_amount = max_amount
        self.min_amount = min_amount
        self.percentage = percentage

    def close_all_popup(self):
        try:
            for _ in range(25):
                self.driver.execute_script("window.confirm = () => true;")
                time.sleep(0.1)
        except:
            pass

    def navigate_new_tab(self):
        # Open new tab
        self.driver.execute_script(f"window.open('{self.url}', '_blank');")
        
        # Switch to newest tab
        new_tab = self.driver.window_handles[-1]
        self.driver.switch_to.window(new_tab)

        print(f"[THREAD {threading.get_ident()}] 🔄 Switched to NEW TAB")



    def detect_no_data_popup(self):
        try:
            msg = self.driver.execute_script(
                "return window.lastAlertMessage || ''"
            )
            if "No data found" in msg:
                print(f"[THREAD {threading.get_ident()}] ❗ END OF PAGINATION DETECTED")
                return True
        except:
            pass
        return False

    def start_scrap(self):
        thread_id = threading.get_ident()
        print(f"[THREAD {thread_id}] 🚗 Browser Started")

        self.driver.get(self.url)

        while True:

            # If ANY thread ordered refresh
            if URL_REFRESH_EVENT.is_set():
                print(f"[THREAD {thread_id}] 👀 Received refresh signal → switching tab")
                self.navigate_new_tab()
                URL_REFRESH_EVENT.clear()

            try:
                self.driver.execute_script("window.confirm = () => true;")
            except:
                pass

            # Detect “No data found”
            if self.detect_no_data_popup():
                URL_REFRESH_EVENT.set()
                continue

            try:
                js_array = ",".join(map(str, self.percentage))

                script = f"""
                    const table = document.getElementsByTagName('table')[0];
                    if (!table) return -1;

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

                if clicked == 0:
                    print(f"[THREAD {thread_id}] ⏭ No rows matched → refreshing...")
                    self.driver.refresh()
                    continue

                if clicked == -1:
                    continue

                print(f"[THREAD {thread_id}] ☑️ Selected {clicked} rows")

                if clicked > 0:
                    print(f"[THREAD {thread_id}] 💾 Saving")
                    if OTP_LOCK.acquire(blocking=False):

                        print(f"[THREAD {thread_id}] 🔐 OTP_LOCK acquired")

                        self.driver.execute_script("document.getElementById('btnSave').click()")

                        try:
                            input_field = self.wait.until(
                                EC.presence_of_element_located((By.ID, "otpInput"))
                            )

                            print(f"[THREAD {thread_id}] 📩 Waiting for OTP")
                            otp = None

                            for _ in range(5):
                                msg = get_latest_mail_from("LG_GRADE_A_SALES@lge.com")
                                if msg:
                                    otp = extract_otp(msg["body"])
                                    if otp:
                                        break
                                time.sleep(2)

                            if otp:
                                print(f"[THREAD {thread_id}] 🔢 OTP → {otp}")
                                input_field.send_keys(otp)

                                self.driver.execute_script(
                                    "document.querySelector(\"input[title='Place Order']\").click();"
                                )

                                delete_mail_by_id(msg['mail'],msg["latest_id"])

                                print(f"[THREAD {thread_id}] ✔ OTP SUCCESS → Restarting tab")
                                self.close_all_popup()

                                URL_REFRESH_EVENT.set()

                        finally:
                            OTP_LOCK.release()
                            print(f"[THREAD {thread_id}] 🔓 OTP_LOCK released")

                self.driver.refresh()

            except Exception as e:
                print(f"[THREAD {thread_id}] ❌ Error: {e}")
                self.driver.refresh()


def run_scraper(url, count, min_amount, max_amount, percentage):
    print(f"\n🚀 Starting {count} threads\n")

    with ThreadPoolExecutor(max_workers=count) as executor:
        for _ in range(count):
            executor.submit(
                LG_SCRAP(url, min_amount, max_amount, percentage).start_scrap
            )


if __name__ == "__main__":
    url=input("Enter URL: ")
    min_amount=int(input("Enter min amount: "))
    max_amount=int(input("Enter max amount: "))
    tab_count=int(input("Enter tab count: "))
    percentage=[int(x) for x in input("Enter percentage: ").split(",")]

    run_scraper(url, count=tab_count, min_amount=min_amount, max_amount=max_amount, percentage=percentage)
