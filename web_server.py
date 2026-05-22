from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import db
import uvicorn
import os
import subprocess
import shlex
import signal
from pathlib import Path

app = FastAPI()

# Path to the sync manager lock file
LOCK_FILE = "/tmp/data-sync-manager.lock"

def trigger_sync():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid_str = f.read().strip()
                if pid_str:
                    pid = int(pid_str)
                    os.kill(pid, signal.SIGUSR1)
        except Exception as e:
            print(f"Failed to trigger sync: {e}")

# Mount static files
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class PairCreate(BaseModel):
    source: str
    destination: str
    exclude: str = None

@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/status")
def get_status():
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT dp.id, dp.source, dp.destination, 
               ls.status, ls.global_pct, ls.current_file, ls.file_pct, ls.speed, ls.eta, ls.last_updated, ls.error_message, ls.ignored_files
        FROM directory_pairs dp
        LEFT JOIN live_status ls ON dp.id = ls.pair_id
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/history")
def get_history():
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sync_sessions ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/api/config")
def get_config():
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM global_config WHERE key='ssh_host'")
    row = cursor.fetchone()
    ssh_host = row['value'] if row else ""
    
    cursor.execute("SELECT * FROM directory_pairs")
    pairs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"ssh_host": ssh_host, "pairs": pairs}

@app.get("/api/browse/local")
def browse_local(path: str = "/"):
    try:
        if not os.path.isabs(path):
            path = "/"
        
        entries = []
        # Add parent directory option if not at root
        if path != "/":
            entries.append({"name": "..", "path": str(Path(path).parent), "is_dir": True})
            
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_dir():
                    entries.append({"name": entry.name, "path": entry.path, "is_dir": True})
        
        return {"current_path": path, "entries": sorted(entries, key=lambda x: x["name"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/browse/remote")
def browse_remote(path: str = ""):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM global_config WHERE key='ssh_host'")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=400, detail="SSH Host not configured")
    
    ssh_host = row['value']
    
    try:
        # If path is empty, get home directory
        if not path:
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh_host, "pwd"]
            path = subprocess.check_output(cmd).decode().strip()

        # List directories only
        # -F adds / to directories, -1 lists one per line, -A includes hidden (except . and ..)
        quoted_path = shlex.quote(path)
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh_host, f"ls -1FA {quoted_path}"]
        output = subprocess.check_output(cmd).decode().splitlines()
        
        entries = []
        if path != "/":
            entries.append({"name": "..", "path": str(Path(path).parent), "is_dir": True})
            
        for line in output:
            if line.endswith('/'):
                name = line[:-1]
                entries.append({
                    "name": name,
                    "path": os.path.join(path, name),
                    "is_dir": True
                })
        
        return {"current_path": path, "entries": sorted(entries, key=lambda x: x["name"])}
    except subprocess.CalledProcessError as e:
        error_msg = e.output.decode() if e.output else str(e)
        raise HTTPException(status_code=500, detail=f"SSH Error: {error_msg}")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Server Error: {type(e).__name__}: {str(e)}")

def is_safe_path(path):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        restricted = [
            "/",
            "/home",
            os.path.expanduser("~"),
            "/root",
            "/etc",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/usr",
            "/boot",
            "/var"
        ]
        return abs_path not in restricted
    except Exception:
        return False

@app.post("/api/config/pair")
def add_pair(pair: PairCreate):
    if not pair.source or not pair.destination:
        raise HTTPException(status_code=400, detail="Source and destination required")
    
    if not is_safe_path(pair.destination):
        raise HTTPException(status_code=400, detail=f"Destination {pair.destination} is a protected system directory.")

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO directory_pairs (source, destination, exclude) VALUES (?, ?, ?)", 
                 (pair.source, pair.destination, pair.exclude))
    conn.commit()
    conn.close()
    trigger_sync()
    return {"status": "success"}

@app.delete("/api/config/pair/{pair_id}")
def delete_pair(pair_id: int):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM directory_pairs WHERE id=?", (pair_id,))
    cursor.execute("DELETE FROM live_status WHERE pair_id=?", (pair_id,))
    conn.commit()
    conn.close()
    trigger_sync()
    return {"status": "success"}

@app.post("/api/config/pair/{pair_id}/approve_wipe")
def approve_wipe(pair_id: int):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE directory_pairs SET wipe_approved=1 WHERE id=?", (pair_id,))
    conn.commit()
    conn.close()
    trigger_sync()
    return {"status": "success"}

@app.post("/api/config/host")
def set_host(data: dict):
    ssh_host = data.get("ssh_host")
    if not ssh_host:
        raise HTTPException(status_code=400, detail="SSH Host required")
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO global_config (key, value) VALUES ('ssh_host', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ssh_host,))
    conn.commit()
    conn.close()
    trigger_sync()
    return {"status": "success"}

if __name__ == "__main__":
    db.init_db()
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=False)
