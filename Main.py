from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from mail import get_latest_mail_from,extract_otp,delete_mail_by_id
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
class LG_SCRAP:
    def __init__(self, url, stop_event, min_amount=0, max_amount=0,percentage=[]):
        self.url = url
        self.stop_event = stop_event
        self.options = Options()
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")
        # self.options.add_argument("--start-maximized")
        self.flag = False
        self.max_amount = max_amount
        self.min_amount = min_amount
        self.percentage = percentage

    def start_scrap(self):
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Firefox(service=service,options=self.options)
        wait = WebDriverWait(driver, 10) 
        email_retry=0
        driver.get(self.url)
        driver.implicitly_wait(20)

        while not self.stop_event.is_set(): 
            try:
                driver.execute_script(""" 
                    var confirmDialog = window.confirm;
                    window.confirm = function(){ return true; };
                """)
                if(self.flag):
                    self.stop_event.set()  

            except Exception as e:
                print(f"⚠️ JavaScript Execution Error: {e}")

            try:
                element = driver.execute_script("""
                    const tables = document.getElementsByTagName('table');
                    return tables.length > 0 ? tables[0] : null;
                """)

                if element:
                    js_array = ",".join(map(str, self.percentage))
                    script = f"""
                        const tables = document.getElementsByTagName('table')[0];
                        var rows = tables ? tables.getElementsByTagName("tr") : [];
                        var checkboxesToClick = [];

                        rows = Array.from(rows);

                        rows.forEach(function(row,index) {{
                            if (index==0) return;

                            var price = row.querySelectorAll('td')[11];
                            var percentage = row.querySelectorAll('td')[12];

                            if (price) {{
                                var spanElement = price.querySelector('span').innerHTML.replace(',', '');
                                var spanPercentage = percentage.querySelector('span').innerHTML;

                                var percentValue = parseFloat(spanPercentage);
                                spanElement = parseFloat(spanElement);

                                // ⭐ CORRECT ARRAY INJECTION ⭐
                                if ([{js_array}].includes(percentValue)) {{
                                    if (spanElement && spanElement >= {self.min_amount} && spanElement <= {self.max_amount}) {{
                                        var checkbox = row.querySelector('input[type="checkbox"]');
                                        checkboxesToClick.push(checkbox);
                                    }}
                                }}
                            }}
                        }});

                        console.log(checkboxesToClick);

                        checkboxesToClick.forEach(function(checkbox) {{
                            checkbox.click();
                        }});
                    """

                    print(script)
                    driver.execute_script(script)
                    # import pdb;pdb.set_trace()


                    driver.execute_script("document.getElementById('btnSave').click();")
                    input_field = wait.until(
                        EC.presence_of_element_located((By.ID, "otpInput"))
                    )
                    otp=""
                    while(email_retry<5):
                        msg=get_latest_mail_from()
                        if(msg):
                            otp=extract_otp(msg["body"])
                            break
                        email_retry+=1
                    import pdb;pdb.set_trace()
                    
                    if(otp):
                        input_field.send_keys(otp)
                        driver.execute_script("document.querySelector(\"input[title='Place Order']\").click();")
                        self.flag = True
                        print("✅ Checkboxes selected and saved successfully!")
                    

                driver.refresh()
            except Exception as e:
                print(f"🔴 Scraping Error: {e}")
                driver.refresh()

        driver.quit()  

def run_scraper(url, count, min_amount, max_amount,percentage):
    stop_event = threading.Event()  
    with ThreadPoolExecutor(max_workers=count) as executor:
        executor.map(lambda _: LG_SCRAP(url, stop_event, min_amount, max_amount,percentage).start_scrap(), range(count))

if __name__ == "__main__":
    # url = input("Enter the URL: ")
    # min_amount=input("Enter the minimum amount: ")
    # max_amount=input("Enter the maximum amount: ")
    # percentage=(input("Enter the percentage seperate by comma (50,70): ")).split(",")
    # tab_count = int(input("Enter the number of tabs: "))

    url="https://www.lg4all.com/POD/NGSI_CustomerBiddingInput.aspx?ReturnUrl=%2fpod%2f%3fCode%3dIN055255001H&Code=IN055255001H"
    min_amount=60490 
    max_amount=60491 
    tab_count=1
    percentage=[50,70]
    run_scraper(url, tab_count, min_amount, max_amount,percentage)





