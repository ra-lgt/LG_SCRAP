from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from concurrent.futures import ThreadPoolExecutor
import threading
import time

class LG_SCRAP:
    def __init__(self, url, stop_event, min_amount=0, max_amount=0):
        self.url = url
        self.stop_event = stop_event
        self.options = Options()
        self.options.add_argument("--start-maximized")
        self.flag = False
        self.max_amount = max_amount
        self.min_amount = min_amount

    def start_scrap(self):
        driver = webdriver.Chrome(options=self.options)
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
                element = driver.execute_script("return document.getElementsByClassName('GridViewStyle')[0];")
                if element:
                    script = """
                        var rows = document.querySelectorAll('.GridViewStyle tr');
                        var checkboxesToClick = [];
                        var flag = true;
                        rows.forEach(function(row) {
                            if (flag) {
                                flag = false;
                                return;
                            }
                            var price = row.querySelectorAll('td')[11];
                            if (price) {
                                var spanElement = price.querySelector('span').innerHTML.replace(',','');
                                spanElement=parseFloat(spanElement);
                                console.log(spanElement)
                                if (spanElement && spanElement >= """ + str(self.min_amount) + """ && spanElement <= """ + str(self.max_amount) + """) {
                                    var checkbox = row.querySelector('input[type="checkbox"]');
                                    checkboxesToClick.push(checkbox);
                                }
                            }
                        });
                        checkboxesToClick.forEach(function(checkbox) { checkbox.click(); });
                    """
                    driver.execute_script(script)

                    driver.execute_script("document.getElementById('ContentPlaceHolder1_btnSave').click();")
                    self.flag = True
                    print("✅ Checkboxes selected and saved successfully!")
                    

                driver.refresh()
            except Exception as e:
                print(f"🔴 Scraping Error: {e}")
                driver.refresh()

        driver.quit()  

def run_scraper(url, count, min_amount, max_amount):
    stop_event = threading.Event()  
    with ThreadPoolExecutor(max_workers=count) as executor:
        executor.map(lambda _: LG_SCRAP(url, stop_event, min_amount, max_amount).start_scrap(), range(count))

if __name__ == "__main__":
    url = input("Enter the URL: ")
    min_amount=input("Enter the minimum amount: ")
    max_amount=input("Enter the maximum amount: ")
    tab_count = int(input("Enter the number of tabs: "))
    run_scraper(url, tab_count, min_amount, max_amount)





