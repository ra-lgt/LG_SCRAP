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
            
            if(self.flag):
                break
        
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
                flag=True      
                element = self.driver.execute_script("""
        return document.getElementsByClassName('GridViewStyle')[0];
    """)
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
    var td12 = row.querySelectorAll('td')[11]; // Get the 12th table data element (index starts from 0)
    if (td12) { // Check if td12 is not undefined
        var spanElement = td12.querySelector('span'); // Find the span element within the td12
        if (spanElement) { // Check if spanElement is not undefined
            var number = spanElement.textContent.replace(',', ''); // Extract the number from the span element
            if (number >= '40000' && number <= '70000') {
                var checkbox = row.querySelector('input[type="checkbox"]');
                checkboxesToClick.push(checkbox);
            }
        }
    }
});
checkboxesToClick.forEach(function(checkbox) {
    checkbox.click();
});
    """
                    self.driver.execute_script(script)

                self.driver.execute_script("""
                    document.getElementById('ContentPlaceHolder1_btnSave').click();
                """)
                self.data.append({'data_found': 'data found'})
                print("submitted successfully:)")
                
            except Exception as e:
                self.driver.refresh()
                self.data.append({'Error':'No Data Found'})
                self.data.append({'Info':'Starting to Refresh'})
        
        

        
        
