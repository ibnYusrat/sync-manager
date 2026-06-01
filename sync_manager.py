import subprocess
import os
import sys
import time
import logging
import re
import signal
import threading
from datetime import datetime
from pathlib import Path
import db

# Path Configuration - RELATIVE TO WORKSPACE
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "sync.log"
LOCK_FILE = "/tmp/data-sync-manager.lock"

exit_event = threading.Event()
trigger_event = threading.Event()

def signal_handler(signum, frame):
    if signum == signal.SIGUSR1:
        logging.info("Received SIGUSR1: Triggering immediate sync.")
        trigger_event.set()
    elif signum in (signal.SIGINT, signal.SIGTERM):
        logging.info(f"Received signal {signum}: Shutting down.")
        exit_event.set()
        trigger_event.set() # Wake up to exit

# ... (rest of the functions remain the same)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE)
    ]
)

def get_config_from_db():
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM global_config WHERE key='ssh_host'")
    row = cursor.fetchone()
    ssh_host = row['value'] if row else None
    
    cursor.execute("SELECT id, source, destination, exclude, wipe_approved FROM directory_pairs")
    directories = [{
        'id': r['id'], 
        'source': r['source'], 
        'destination': r['destination'],
        'exclude': r['exclude'],
        'wipe_approved': r['wipe_approved']
    } for r in cursor.fetchall()]
    conn.close()
    
    return {'ssh_host': ssh_host, 'directories': directories}

def reset_wipe_approval(pair_id):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE directory_pairs SET wipe_approved=0 WHERE id=?", (pair_id,))
    conn.commit()
    conn.close()

def update_live_status(pair_id, status_dict):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO live_status (pair_id, status, global_pct, current_file, file_pct, speed, eta, last_updated, error_message, ignored_files)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pair_id) DO UPDATE SET
            status=excluded.status,
            global_pct=excluded.global_pct,
            current_file=excluded.current_file,
            file_pct=excluded.file_pct,
            speed=excluded.speed,
            eta=excluded.eta,
            last_updated=excluded.last_updated,
            error_message=excluded.error_message,
            ignored_files=excluded.ignored_files
    ''', (
        pair_id, 
        status_dict.get('status', 'idle'),
        status_dict.get('global_pct', 0.0),
        status_dict.get('current_file', ''),
        status_dict.get('file_pct', ''),
        status_dict.get('speed', ''),
        status_dict.get('eta', ''),
        time.time(),
        status_dict.get('error_message', ''),
        status_dict.get('ignored_files', '')
    ))
    conn.commit()
    conn.close()
def save_history_db(source, dest, start, end, stats):
    transferred = stats.get('transferred_size', '0')
    # Skip if 0 bytes transferred to reduce noise
    if transferred == '0' or transferred == '0 bytes':
        return

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO sync_sessions (timestamp, source, destination, duration, total_files, total_size, transferred_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        start.isoformat(),
        source,
        dest,
        str(end-start).split('.')[0], # Avoid long microseconds
        stats.get('total_files', '0'),
        stats.get('total_size', '0'),
        stats.get('transferred_size', '0')
    ))
    cursor.execute('''
        DELETE FROM sync_sessions WHERE id NOT IN (
            SELECT id FROM sync_sessions ORDER BY id DESC LIMIT 100
        )
    ''')
    conn.commit()
    conn.close()

def check_connectivity(ssh_host):
    response = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", ssh_host, "echo ok"], 
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return response.returncode == 0

def get_remote_size(ssh_host, remote_path):
    cmd = f"ssh -o ConnectTimeout=5 -o BatchMode=yes {ssh_host} 'du -sb {remote_path}'"
    try:
        return int(subprocess.check_output(cmd, shell=True).decode().split()[0])
    except: return None

def get_local_used_size(local_path):
    if not os.path.exists(local_path): return 0
    try: return int(subprocess.check_output(["du", "-sb", local_path]).decode().split()[0])
    except: return 0

def get_local_free_space(local_path):
    os.makedirs(local_path, exist_ok=True)
    stat = os.statvfs(local_path)
    return stat.f_frsize * stat.f_bavail

def parse_rsync_stats(lines):
    stats = {}
    for line in lines:
        if "Number of files:" in line:
            val = line.split(":", 1)[1].strip()
            stats['total_files'] = val.split("(")[0].strip()
        if "Total file size:" in line:
            stats['total_size'] = line.split(":", 1)[1].strip()
        if "Total transferred file size:" in line:
            stats['transferred_size'] = line.split(":", 1)[1].strip()
    return stats

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

def run_sync():
    config = get_config_from_db()
    ssh_host = config.get('ssh_host')
    directories = config.get('directories', [])
    
    if not ssh_host or not directories: return
    if not check_connectivity(ssh_host): 
        logging.error(f"Connectivity check failed for {ssh_host}")
        return

    for folder_pair in directories:
        pair_id = folder_pair['id']
        source = folder_pair['source']
        dest = folder_pair['destination']
        
        # Safety Check
        if not is_safe_path(dest):
            err_msg = f"Destination {dest} is a protected system directory. Sync aborted."
            logging.error(err_msg)
            update_live_status(pair_id, {'status': 'error', 'error_message': err_msg})
            continue

        # Ensure we create a subdirectory with the source name
        source_name = os.path.basename(source.rstrip('/'))
        if not source_name: # Handle case where source might be just "/"
            source_name = "root_sync"
        
        actual_dest = os.path.join(dest, source_name)
        os.makedirs(actual_dest, exist_ok=True)

        # Only reset status for the current pair when it starts
        update_live_status(pair_id, {'status': 'scanning', 'current_file': 'Calculating sizes...'})
        
        logging.info(f"--- Starting: {source} -> {actual_dest} ---")

        remote_size = get_remote_size(ssh_host, source)
        local_initial_size = get_local_used_size(actual_dest)
        local_free = get_local_free_space(actual_dest)
        
        # Wipe Protection: If remote is empty but local is not, require manual approval
        if remote_size == 0 and local_initial_size > 0:
            if not folder_pair.get('wipe_approved'):
                err_msg = f"Source {source} is empty but destination has data. Wipe requires approval."
                logging.warning(err_msg)
                update_live_status(pair_id, {'status': 'needs_approval', 'error_message': err_msg})
                continue
            else:
                logging.info(f"Wipe approved for {source}. Proceeding to clear destination.")
                reset_wipe_approval(pair_id)

        if remote_size and remote_size > (local_free + local_initial_size):
            err_msg = f"Insufficient space for {source}"
            logging.error(err_msg)
            update_live_status(pair_id, {'status': 'error', 'error_message': err_msg})
            continue

        # Using trailing slash on source and actual_dest to ensure contents sync into the folder
        rsync_cmd = [
            "rsync", "-azP", "--delete", "--stats",
            f"{ssh_host}:{source.rstrip('/')}/", f"{actual_dest}/"
        ]
        
        if folder_pair.get('exclude'):
            rsync_cmd.insert(4, f"--exclude={folder_pair['exclude']}")
        
        start_time = datetime.now()
        last_log_time = 0
        output_lines = []
        current_filename = "Scanning..."
        session_bytes_transferred = 0
        last_file_bytes = 0
        ignored_files_list = []
        import json

        update_live_status(pair_id, {'status': 'running', 'current_file': 'Starting sync...', 'ignored_files': json.dumps([])})

        try:
            process = subprocess.Popen(rsync_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            current_line_bytes = []
            
            for byte in iter(lambda: process.stdout.read(1), b''):
                char = byte.decode('utf-8', errors='replace')
                
                if char == '\r' or char == '\n':
                    line = "".join(current_line_bytes).strip()
                    current_line_bytes = []
                    if not line: continue
                    
                    # Check for permission denied errors to track ignored directories
                    perm_denied_match = re.search(r'rsync: (?:\[sender\] )?opendir "(.*?)" failed: Permission denied \(13\)', line)
                    if perm_denied_match:
                        ignored_path = perm_denied_match.group(1)
                        if ignored_path not in ignored_files_list:
                            ignored_files_list.append(ignored_path)
                            update_live_status(pair_id, {
                                'status': 'running',
                                'current_file': f"Ignoring unreadable: {ignored_path}",
                                'ignored_files': json.dumps(ignored_files_list)
                            })
                        continue

                    if re.match(r'^\s*[\d,]+\s+\d+%', line):
                        parts = line.split()
                        if len(parts) >= 4:
                            current_file_bytes_str = parts[0].replace(',', '')
                            if current_file_bytes_str.isdigit():
                                current_file_bytes = int(current_file_bytes_str)
                                diff = current_file_bytes - last_file_bytes
                                if diff > 0:
                                    session_bytes_transferred += diff
                                last_file_bytes = current_file_bytes
                            
                            file_pct = parts[1]
                            speed = parts[2]
                            eta = parts[3]
                            
                            total_estimate = local_initial_size + session_bytes_transferred
                            global_pct = min((total_estimate / remote_size) * 100, 100.0) if remote_size else 0.0
                            
                            current_time = time.time()
                            if current_time - last_log_time > 1: # Update DB max once per second
                                update_live_status(pair_id, {
                                    'status': 'running',
                                    'global_pct': global_pct,
                                    'current_file': current_filename[:50],
                                    'file_pct': file_pct,
                                    'speed': speed,
                                    'eta': eta
                                })
                                last_log_time = current_time
                    
                    elif any(x in line for x in ["receiving incremental file list", "sent ", "total size is"]):
                        output_lines.append(line)
                        
                    else:
                        if not ("xfr#" in line and "to-chk=" in line):
                            if not line.endswith('/'):
                                current_filename = line.split('/')[-1]
                                last_file_bytes = 0
                        output_lines.append(line)

                else:
                    current_line_bytes.append(char)
            
            process.wait()
            end_time = datetime.now()
            
            # Code 23 means some files could not be transferred, code 24 means partial transfer.
            # If we tracked permission denied files, consider it a successful sync of the readable parts.
            if process.returncode == 0 or (process.returncode in (23, 24) and ignored_files_list):
                stats = parse_rsync_stats(output_lines)
                save_history_db(source, dest, start_time, end_time, stats)
                if process.returncode != 0:
                    logging.info(f"Completed with some unreadable files ignored: {source}")
                else:
                    logging.info(f"Completed: {source}")
                
                update_live_status(pair_id, {
                    'status': 'idle', 
                    'current_file': 'Sync complete', 
                    'global_pct': 100.0,
                    'ignored_files': json.dumps(ignored_files_list)
                })
            else:
                # Capture last few lines of output for better error reporting
                error_context = "\n".join(output_lines[-5:]) if output_lines else "No output from rsync"
                err_msg = f"Rsync failed (code {process.returncode}): {error_context}"
                logging.error(f"Sync failed for {source}: {err_msg}")
                update_live_status(pair_id, {'status': 'error', 'error_message': err_msg})
            
        except Exception as e:
            logging.error(f"Error syncing {source}: {e}")
            update_live_status(pair_id, {'status': 'error', 'error_message': str(e)})

if __name__ == "__main__":
    db.init_db() # Ensure DB is initialized
    
    # Setup signal handlers
    signal.signal(signal.SIGUSR1, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid_str = f.read().strip()
                if pid_str:
                    pid = int(pid_str)
                    os.kill(pid, 0)
                    logging.error(f"Sync manager already running with PID {pid}")
                    sys.exit(1)
        except (ProcessLookupError, ValueError, FileNotFoundError):
            try:
                os.remove(LOCK_FILE)
            except:
                pass

    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))

        logging.info("Sync manager started.")
        
        while not exit_event.is_set():
            run_sync()
            
            if exit_event.is_set():
                break
                
            logging.info("Sync cycle complete. Waiting 10 minutes or until triggered...")
            # Wait for 10 minutes (600 seconds) or until trigger_event is set
            triggered = trigger_event.wait(timeout=600)
            
            if exit_event.is_set():
                break
                
            if triggered:
                logging.info("Triggered by signal. Starting new sync cycle.")
                trigger_event.clear()
            else:
                logging.info("Timeout reached. Starting scheduled sync cycle.")
                
    except Exception as e:
        logging.error(f"Main loop error: {e}")
    finally:
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except:
                pass
        logging.info("Sync manager stopped.")
