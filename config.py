import os

# --- 全局路径配置 ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(APP_DIR, "userdata")
NETSCAPE_TEMP = os.path.join(APP_DIR, "bili_netscape_temp.txt")
LAST_LOGIN_COOKIE = os.path.join(APP_DIR, "last_login_cookie.json")
ERROR_LOG = os.path.join(APP_DIR, "error_log.txt")
HISTORY_FILE = "history.json"
COOKIE_FILE = "cookies.json"
BACKUP_FILENAMES = {
    "history": ["bili_history.json", "history.json"],
    "cookie": ["bili_cookies.json", "cookies.json"]
}

# --- 界面配色方案 (THEME) ---
# 格式: ("白天颜色", "黑夜颜色")
THEME = {
    # === 容器背景 ===
    "app_bg":      ("#F3F3F3", "#121212"),  # 窗口背景
    "panel_bg":    ("#FFFFFF", "#1E1E1E"),  # 侧边栏/面板背景
    "list_bg":     ("#F2F2F2", "#181818"),  # 列表槽背景
    
    # === 卡片交互 ===
    "card_normal": ("#FFFFFF", "#252526"),  # 默认卡片
    "card_hover":  ("#E0E0E0", "#2D2D2D"),  # 悬停
    "card_selected": ("#FFF0F5", "#37373D"),# 选中背景 (card_sel 别名)
    "card_sel":    ("#FFF0F5", "#37373D"),  # 兼容键名
    "card_done":   ("#E8F5E9", "#1A241A"),  # 已完成背景
    
    # === 状态指示条 ===
    "strip_normal": ("#FFFFFF", "#252526"), # 隐藏(同背景色)
    "strip_done":   ("#4CAF50", "#4CAF50"), # 完成绿
    "strip_selected": ("#FB7299", "#FB7299"), # 选中粉
    
    # === 边框颜色 ===
    "border":      ("#D0D0D0", "#333333"),  # 通用边框
    "border_normal": ("#D0D0D0", "#333333"),
    "border_hover":  ("#999999", "#444444"),
    "border_done":   ("#4CAF50", "#2ECC71"),
    "border_selected": ("#FB7299", "#FB7299"), # (border_sel 别名)
    "border_sel":    ("#FB7299", "#FB7299"),   # 兼容键名
    "border_hidden": ("#FFFFFF", "#252526"),   # 隐藏时的占位色(同背景)
    
    # === 字体颜色 ===
    "text_main":   ("#333333", "#E0E0E0"),  # 主标题
    "text_sub":    ("#757575", "#858585"),  # 副标题
    "text_done":   ("#2E7D32", "#81C784"),  # 完成状态文字
    "text_log":    ("#555555", "#CCCCCC"),  # 终端日志
    "text_logs":   ("#555555", "#CCCCCC"),  # 兼容键名
    
    # === 按钮与输入框 ===
    "btn_primary": ("#FB7299", "#FB7299"),  # 主按钮
    "primary":     ("#FB7299", "#FB7299"),  # 兼容键名
    "btn_primary_hover": ("#FF8EB3", "#FF8EB3"),
    "primary_h":   ("#FF8EB3", "#FF8EB3"),  # 兼容键名
    
    "btn_danger":  ("#E57373", "#E57373"),  # 删除/取消
    "danger":      ("#E57373", "#E57373"),  # 兼容键名
    
    "success":     ("#4CAF50", "#4CAF50"),
    "neutral":     ("#95A5A6", "#95A5A6"),
    
    "input_bg":    ("#FFFFFF", "#343638"),
    "input_border":("#C0C0C0", "#565B5E")
}
