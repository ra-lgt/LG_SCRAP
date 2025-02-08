from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from concurrent.futures import ThreadPoolExecutor
import time

class LG_SCRAP:
    def __init__(self, url):
        self.url = url
        self.options = Options()
        self.options.add_argument("--start-maximized")

    def start_scrap(self):
        driver = webdriver.Chrome(options=self.options)
        driver.get(self.url)
        driver.implicitly_wait(20)
        flag=False

        while(True):
            if(flag):
                break

            try:
                driver.execute_script("""
                    var confirmDialog = window.confirm;
                    window.confirm = function(){ return true; };
                """)
            except:
                pass

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
                            var td12 = row.querySelectorAll('td')[11];
                            if (td12) {
                                var spanElement = td12.querySelector('span');
                                if (spanElement) {
                                    var checkbox = row.querySelector('input[type="checkbox"]');
                                    checkboxesToClick.push(checkbox);
                                }
                            }
                        });
                        checkboxesToClick.forEach(function(checkbox) { checkbox.click(); });
                    """
                    driver.execute_script(script)
                    driver.execute_script("document.getElementById('ContentPlaceHolder1_btnSave').click();")
                    flag=True
                    driver.refresh()

                print("✅ Checkboxes selected successfully!")
            except Exception as e:
                print("🔴 Error:", str(e))
                driver.refresh()


def run_scraper(url, count):
    with ThreadPoolExecutor(max_workers=count) as executor:
        executor.map(lambda _: LG_SCRAP(url).start_scrap(), range(count))

if __name__ == "__main__":
    url=input("Enter the URL: ")
    tab_count=int(input("Enter the number of tabs: "))
    # url = "https://www.lg4all.com/POD/NGSI_CustomerBiddingInput.aspx?ReturnUrl=%2fpod%2f%3fCode%3dIN053139001H&Code=IN053139001H"
    # tab_count = 3

    run_scraper(url, tab_count)
