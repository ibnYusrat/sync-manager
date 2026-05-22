import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "sync_data.db"
CONFIG_FILE = BASE_DIR / "sync_config.json"

def get_connection():
    # check_same_thread=False is needed for FastAPI handling requests in different threads
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Configuration Table (ssh_host and pairs)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS directory_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            destination TEXT NOT NULL,
            exclude TEXT
        )
    ''')
    
    # Migration: Add exclude column if it doesn't exist
    cursor.execute("PRAGMA table_info(directory_pairs)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'exclude' not in columns:
        cursor.execute("ALTER TABLE directory_pairs ADD COLUMN exclude TEXT")
    if 'wipe_approved' not in columns:
        cursor.execute("ALTER TABLE directory_pairs ADD COLUMN wipe_approved INTEGER DEFAULT 0")
    
    # Historical Sessions Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            source TEXT,
            destination TEXT,
            duration TEXT,
            total_files TEXT,
            total_size TEXT,
            transferred_size TEXT
        )
    ''')
    
    # Live Status Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_status (
            pair_id INTEGER PRIMARY KEY,
            status TEXT, -- 'running', 'idle', 'error'
            global_pct REAL,
            current_file TEXT,
            file_pct TEXT,
            speed TEXT,
            eta TEXT,
            last_updated REAL,
            error_message TEXT,
            ignored_files TEXT
        )
    ''')
    
    # Migration: Add ignored_files column if it doesn't exist
    cursor.execute("PRAGMA table_info(live_status)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'ignored_files' not in columns:
        cursor.execute("ALTER TABLE live_status ADD COLUMN ignored_files TEXT")
    
    conn.commit()

    # Migrate existing config if DB is empty
    cursor.execute('SELECT COUNT(*) FROM global_config')
    if cursor.fetchone()[0] == 0:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                try:
                    data = json.load(f)
                    ssh_host = data.get("ssh_host", "")
                    cursor.execute('INSERT INTO global_config (key, value) VALUES (?, ?)', ('ssh_host', ssh_host))
                    for pair in data.get("directories", []):
                        cursor.execute('INSERT INTO directory_pairs (source, destination) VALUES (?, ?)', 
                                     (pair.get("source"), pair.get("destination")))
                    conn.commit()
                except Exception as e:
                    print(f"Failed to migrate json: {e}")

    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
