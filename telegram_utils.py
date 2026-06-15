import requests
import os
import sys
import certifi
import datetime

BOT_TOKEN = "8560044076:AAE9tHcAuBMY4U521Ynnrf6Y9VIUzahIr0A"
CHAT_ID = "7809021498"

def get_log_path():
    """Determines the log file path in the user's data directory."""
    data_dir = os.environ.get('NEXUS_DATA_PATH')
    if not data_dir:
        # Fallback if env var not set
        if getattr(sys, 'frozen', False):
             data_dir = os.path.dirname(sys.executable)
        else:
             data_dir = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(data_dir, 'nexus_debug.log')

def log_debug(message):
    """Writes a message to the debug log."""
    try:
        log_file = get_log_path()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"Failed to write to log: {e}")

def get_cert_path():
    """Returns the path to the CA bundle, handling frozen environments."""
    try:
        if getattr(sys, 'frozen', False):
            # If frozen, look for the bundle in the temporary folder or alongside the exe
            # PyInstaller often puts it in _MEIPASS/certifi/cacert.pem or similar
            # But 'certifi.where()' usually points to the library location. 
            # We will rely on certifi.where() if it works, otherwise fallback.
            base_path = sys._MEIPASS
            cert_path = os.path.join(base_path, 'certifi', 'cacert.pem')
            if os.path.exists(cert_path):
                return cert_path
            
            # Fallback to local
            return certifi.where()
        else:
            return certifi.where()
    except Exception as e:
        log_debug(f"Error getting cert path: {e}")
        return True # Fallback to system default or insecure if needed (but requests default is True)

def send_telegram_message(text):
    """Sends a text message to the configured Telegram Chat ID."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    try:
        # log_debug(f"Attempting to send message: {text[:20]}...")
        cert = get_cert_path()
        response = requests.post(url, json=payload, verify=cert)
        response.raise_for_status()
        # log_debug("Message sent successfully.")
        return True
    except Exception as e:
        log_debug(f"Error sending Telegram message: {e}")
        return False

def send_telegram_document(file_path, caption=None):
    """Sends a document (file) to the configured Telegram Chat ID."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    
    if not os.path.exists(file_path):
        log_debug(f"File not found for Telegram upload: {file_path}")
        return False
        
    try:
        log_debug(f"Attempting to send document: {file_path}")
        cert = get_cert_path()
        with open(file_path, 'rb') as f:
            files = {'document': f}
            data = {'chat_id': CHAT_ID}
            if caption:
                data['caption'] = caption
            
            response = requests.post(url, data=data, files=files, verify=cert)
            response.raise_for_status()
            log_debug("Document sent successfully.")
            return True
    except Exception as e:
        log_debug(f"Error sending Telegram document: {e}")
        return False
