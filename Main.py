from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.alert import Alert



class LG_SCAP:
    
    def __init__(self,url):
        self.driver = webdriver.Firefox()
        self.url=url
    
    def get_link(self):
        self.driver.get(self.url)
        self.driver.implicitly_wait(20)
        self.start_scrap()
        
        
        
    def start_scrap(self):
       
        try:
            wait = WebDriverWait(self.driver, 1)  # Wait up to 10 seconds for the alert
            alert = wait.until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert.accept()
        except:
            pass
        try:
            while(True):
                
                element = WebDriverWait(self.driver, 1).until(
                            EC.presence_of_element_located((By.CLASS_NAME, "GridViewStyle"))
                            )
                self.driver.execute_script("Array.from(document.querySelectorAll('input[type=checkbox]')).forEach(el => el.checked = true);")
            
                button=WebDriverWait(self.driver,1).until(
                            EC.presence_of_element_located((By.ID,'ContentPlaceHolder1_btnSave'))
                            )
                button.click()
        except:
            self.driver.refresh()
            print("NO Data.....")
            print('refreshing.....')
            self.start_scrap()
        self.start_scrap()
        
        

        
        

lg_scrap=LG_SCAP("https://www.lg4all.com/POD/NGSI_CustomerBiddingInput.aspx?ReturnUrl=%2fpod%2f%3fCode%3dIN039283001H&Code=IN039283001H")
lg_scrap.get_link()