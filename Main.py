from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.chrome.options import Options



class LG_SCRAP:
    def __init__(self):
        pass
    
    def setup_config(self,url):
        options = Options()
        self.driver = webdriver.Chrome(options=options)
        self.url=url
        self.data=[]
        self.flag=False
    
    def get_link(self):
        self.driver.get(self.url)
        self.driver.implicitly_wait(20)
        self.start_scrap()
    
    def swap_flag(self):
        print("process stopped")
        self.flag=True
        
        
        
    def start_scrap(self):
        while(True):
            self.driver.refresh()
            
        
        

        
        
