import customtkinter
import tkinter as tk
from Main import LG_SCRAP
from tkinter.scrolledtext import ScrolledText
from ttkbootstrap import Style
import threading
from datetime import datetime
import time


class LG_UI(LG_SCRAP):

    def __init__(self):
        super().__init__()
        self.root = customtkinter.CTk(fg_color="#212121")
        self.root.geometry("500x400")
        self.root.title("LG Scrapper")
        self.index=0
        self.scrolled_text=None
        self.flag=True

    
    
    def get_time(self):
        now = datetime.now()
        formatted_time = now.strftime("%a %I:%M:%S %p")

        return formatted_time
        
    
    def start_thread(self,entry):
        if(self.flag):
            self.flag=False
            self.setup_config(entry.get())
            backend = threading.Thread(target=self.get_link)
            backend.start()
            self.update_ui()
            



    def home_page(self):
        print("let's get started")
        title= customtkinter.CTkLabel(master=self.root, text="LG Tracker", text_color="#E0E0E0",font=("Arial",15))
        title.place(relx=0.4)
        home_frame=customtkinter.CTkFrame(master=self.root, width=400, height=400,fg_color="#212121")
        home_frame.place(rely=0.2)

        url_label=customtkinter.CTkLabel(master=home_frame, text="Enter the URL",text_color="#E0E0E0",font=("Arial",15))
        url_label.grid(row=0,column=0,sticky=tk.W,padx=5)

        entry = customtkinter.CTkEntry(master=home_frame, placeholder_text="URL or web address",width=350,height=25)
        entry.grid(row=0,column=1,sticky=tk.W,padx=20)

        button = customtkinter.CTkButton(master=self.root, text="Start", command=lambda:self.start_thread(entry),fg_color="green",hover_color="#00c4a0")
        button.place(relx=0.05,rely=0.3)

        stbutton = customtkinter.CTkButton(master=self.root, text="Stop", command=lambda:self.swap_flag(),fg_color="red",hover_color="#de0077")
        stbutton.place(relx=0.6,rely=0.3)

        self.scrolled_text = ScrolledText(self.root, wrap=tk.WORD)
        self.scrolled_text.place(relx=0.1, rely=0.4, relwidth=0.8, relheight=0.5) 

    def update_ui(self):
        try:
            curr_time = self.get_time()
            if 'data_found' in self.data[self.index]:
                value = curr_time + '\t' +"Success :"+self.data[self.index]['data_found']+'\n\n'
                self.scrolled_text.insert(tk.END, value)
                self.index += 1
            elif 'Error' in self.data[self.index] or 'Info' in self.data[self.index]:
                err_value = curr_time + '\t' +"Warning"+ self.data[self.index]['Error']+'\n\n'
                self.scrolled_text.insert(tk.END, str(err_value))

                inf_value = curr_time + '\t' + "Info"+self.data[self.index+1]['Info']+'\n\n'
                self.scrolled_text.insert(tk.END, str(inf_value))
                self.index += 2
        except:
            pass
        self.root.after(100, self.update_ui)
    
    def start_app(self):
        self.home_page()
        self.root.mainloop()





        



lg_ui=LG_UI()
lg_ui.start_app()
