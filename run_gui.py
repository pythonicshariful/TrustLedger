import sys
import threading
import time
import os
import subprocess
import webbrowser
import json
# Removed top-level tkinter imports to prevent macOS crash
# Will import locally in select_data_folder
from app import create_app

# Global flag to signal the server is ready
server_ready = threading.Event()

def get_app_data_path():
    """Get the path to the application data directory in AppData (Windows) or Application Support (Mac)."""
    if os.name == 'nt':
        app_data = os.getenv('APPDATA')
        if not app_data:
            app_data = os.path.expanduser("~")
        path = os.path.join(app_data, 'ShopnoBilash')
    elif os.name == 'posix':
        # Mac or Linux
        if sys.platform == 'darwin':
             path = os.path.expanduser('~/Library/Application Support/ShopnoBilash')
        else:
             # Linux, maybe XDG_CONFIG_HOME
             path = os.path.expanduser('~/.config/shopno-bilash')
    else:
        path = os.path.expanduser("~/ShopnoBilash")
        
    os.makedirs(path, exist_ok=True)
    return path

def get_launcher_config():
    """Read the launcher config to find the user's selected data folder."""
    config_path = os.path.join(get_app_data_path(), 'launcher_config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_launcher_config(config):
    """Save the launcher config."""
    config_path = os.path.join(get_app_data_path(), 'launcher_config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f)

def select_data_folder():
    """Open a dialog to select the data folder."""
    if sys.platform == 'darwin':
        # Use native macOS folder selection to avoid Tkinter crash
        script = 'tell application "System Events" to return POSIX path of (choose folder with prompt "Select Data Folder for Shopno Bilash")'
        try:
            # osascript returns path with trailing newline
            result = subprocess.check_output(['osascript', '-e', script], text=True).strip()
            return result
        except subprocess.CalledProcessError:
            return None # User Cancelled
        except Exception as e:
            print(f"Error selecting folder on Mac: {e}")
            return None
    
    # Windows/Linux Fallback to Tkinter
    import tkinter as tk
    from tkinter import filedialog
    
    root = tk.Tk()
    root.withdraw() # Hide the main window

    # Make sure the dialog is top-most
    root.attributes('-topmost', True)
    
    folder_selected = filedialog.askdirectory(title="Select Data Folder for Shopno Bilash")
    root.destroy()
    return folder_selected

def start_server():
    app = create_app()
    # Disable the reloader so we don't start two instances or restart unexpectedly
    # Threaded=True is important for handling multiple requests
    app.run(host='127.0.0.1', port=8080, threaded=True, use_reloader=False)

def open_browser_fallback(url):
    """
    Try to open the browser in 'app mode' (no address bar) if possible.
    Falls back to default browser.
    """
    # Common paths for Edge and Chrome on Windows
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    
    # Add Mac paths
    if sys.platform == 'darwin':
        edge_paths = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
        chrome_paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

    # Try Edge first
    for path in edge_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen([path, f"--app={url}"])
                return
            except Exception:
                pass
                
    # Try Chrome
    for path in chrome_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen([path, f"--app={url}"])
                return
            except Exception:
                pass
                
    # Fallback to default browser
    webbrowser.open(url)

if __name__ == '__main__':
    # 1. Check Configuration
    config = get_launcher_config()
    data_path = config.get('data_path')

    if not data_path or not os.path.exists(data_path):
        # First run or path moved/deleted
        data_path = select_data_folder()
        if not data_path:
            # User cancelled
            sys.exit()
        
        # Save persistence
        config['data_path'] = data_path
        save_launcher_config(config)

    # 3. Set Environment Variable for app.py to pick up
    os.environ['NEXUS_DATA_PATH'] = data_path

    # 4. Start Server
    # Start Flask in a separate thread
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()

    # Wait a brief moment for the server to initialize
    time.sleep(1.5)
    
    url = 'http://127.0.0.1:8080'

    # 5. Launch GUI
    try:
        import webview
        
        icon_path = None
        if getattr(sys, 'frozen', False):
             icon_path = os.path.join(sys._MEIPASS, 'nexus-river-view-600x866.ico')
        elif os.path.exists('nexus-river-view-600x866.ico'):
             icon_path = os.path.abspath('nexus-river-view-600x866.ico')

        if os.name == 'nt':
            import ctypes
            myappid = 'sharifulnrv.software.shopnobilash.1.0'
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except:
                pass

        window = webview.create_window(
            'Shopno Bilash', 
            url, 
            width=1280, 
            height=800, 
            resizable=True
        )
        
        webview.start(icon=icon_path)
        
    except Exception as e:
        print(f"GUI Error: {e}")
        open_browser_fallback(url)
        # Keep main thread alive for headless/browser mode
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)
