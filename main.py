# main.py
from manager import BiliManager
from ui import App

if __name__ == "__main__":
    bili_manager = BiliManager()
    app = App(bili_manager)
    app.mainloop()
