import requests
import urllib3

# Disable certificate warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Replace with your camera's IP
CAMERA_IP = "192.168.1.X"

# Default username and your recovery code (found in Protect -> Settings -> System -> Advanced)
USERNAME = "ubnt"
PASSWORD = "REPLACE_WITH_RECOVERY_CODE"

session = requests.Session()

login_url = f"https://{CAMERA_IP}/api/auth/login"
payload = {
    "username": USERNAME,
    "password": PASSWORD
}

print("[*] Logging in...")
resp = session.post(login_url, json=payload, verify=False)

if resp.status_code == 200:
    print("[+] Login successful!")
    print("[*] Cookies:", session.cookies.get_dict())
else:
    print(f"[!] Login failed: {resp.status_code}")
    print(resp.text)
