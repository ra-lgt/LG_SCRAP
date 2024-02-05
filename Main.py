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
        self.flag=True
        
        
        
    def start_scrap(self):
        if(self.flag):
            return
       
        try:
            self.driver.execute_script("""
        var confirmDialog = window.confirm;
        window.confirm = function(){
            return true;
        };
    """)
        except:
            pass
        try:      
            element = self.driver.execute_script("""
        return document.getElementsByClassName('GridViewStyle')[0];
    """)
            if(element):
                
                self.driver.execute_script("Array.from(document.querySelectorAll('input[type=checkbox]')).forEach(el => el.checked = true);")
            
                self.driver.execute_script("""
                                            document.getElementById('ContentPlaceHolder1_btnSave').click();
                                            """)
                self.data.append({'data_found':'data found'})
                print("submitted successfully:)")
            
        except:
            self.driver.refresh()
            self.data.append({'Error':'No Data Found'})
            self.data.append({'Info':'Starting to Refresh'})
            self.start_scrap()
        self.start_scrap()
        
        

        
        
