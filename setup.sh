#!/bin/bash

# --- Configuration ---
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="data-sync.service"
PYTHON_BIN=$(which python3)
RSYNC_BIN=$(which rsync)
SSH_BIN=$(which ssh)
CURRENT_USER=$(whoami)

# --- Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Data Sync Manager Setup...${NC}"

# 1. Dependency Checks
echo -e "\n${YELLOW}[1/4] Checking dependencies...${NC}"
if [ -z "$PYTHON_BIN" ]; then echo -e "${RED}Error: python3 not found.${NC}"; exit 1; fi
if [ -z "$RSYNC_BIN" ]; then echo -e "${RED}Error: rsync not found.${NC}"; exit 1; fi
if [ -z "$SSH_BIN" ]; then echo -e "${RED}Error: ssh not found.${NC}"; exit 1; fi
echo -e "Dependencies met: Python3, Rsync, SSH."

# 2. Project Directory Setup
echo -e "\n${YELLOW}[2/4] Setting up project directory...${NC}"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR" || exit
echo -e "Working in $PROJECT_DIR"

# --- INTERACTIVE BROWSER FUNCTIONS ---

browse_remote() {
    # Get the user's home directory to start
    local current_dir=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$SSH_HOST" "pwd")
    if [ -z "$current_dir" ]; then current_dir="/"; fi
    
    while true; do
        echo -e "\n${CYAN}--- Browsing Remote Source ($SSH_HOST) ---${NC}"
        echo -e "Current Path: ${YELLOW}$current_dir${NC}"
        
        # Safely list directories only (ignore files) using ls and grep
        local safe_dir=$(printf "%q" "$current_dir")
        local dirs=$(ssh -o BatchMode=yes "$SSH_HOST" "cd $safe_dir && ls -1pA 2>/dev/null | grep '/$' | sed 's/\/$//'")
        
        local options=("[Select THIS Directory]" "[Go Up (..)]" "[Go to Root (/)]" "[Go to Home (~)]")
        IFS=$'\n' read -rd '' -a dir_array <<<"$dirs"
        for d in "${dir_array[@]}"; do
            if [ -n "$d" ]; then options+=("$d/"); fi
        done
        
        PS3="Choose an option (number): "
        # We temporarily force columns=1 to make the menu an easy-to-read list
        local old_cols=$COLUMNS
        COLUMNS=1
        select opt in "${options[@]}"; do
            COLUMNS=$old_cols
            if [ "$opt" = "[Select THIS Directory]" ]; then
                [[ "${current_dir}" != */ ]] && current_dir="${current_dir}/"
                SELECTED_REMOTE="$current_dir"
                return 0
            elif [ "$opt" = "[Go Up (..)]" ]; then
                current_dir=$(dirname "$current_dir")
                break
            elif [ "$opt" = "[Go to Root (/)]" ]; then
                current_dir="/"
                break
            elif [ "$opt" = "[Go to Home (~)]" ]; then
                current_dir=$(ssh -o BatchMode=yes "$SSH_HOST" "pwd")
                break
            elif [ -n "$opt" ]; then
                local chosen="${opt%/}"
                if [ "$current_dir" = "/" ]; then
                    current_dir="/$chosen"
                else
                    current_dir="$current_dir/$chosen"
                fi
                break
            else
                echo "Invalid option."
            fi
        done
    done
}

browse_local() {
    local current_dir=$(pwd)
    while true; do
        echo -e "\n${CYAN}--- Browsing Local Destination ---${NC}"
        echo -e "Current Path: ${YELLOW}$current_dir${NC}"
        
        # List local directories
        local dirs=$(cd "$current_dir" && ls -1pA 2>/dev/null | grep '/$' | sed 's/\/$//')
        
        local options=("[Select THIS Directory]" "[Go Up (..)]" "[Go to Root (/)]" "[Create New Directory Here]")
        IFS=$'\n' read -rd '' -a dir_array <<<"$dirs"
        for d in "${dir_array[@]}"; do
            if [ -n "$d" ]; then options+=("$d/"); fi
        done
        
        PS3="Choose an option (number): "
        local old_cols=$COLUMNS
        COLUMNS=1
        select opt in "${options[@]}"; do
            COLUMNS=$old_cols
            if [ "$opt" = "[Select THIS Directory]" ]; then
                [[ "${current_dir}" != */ ]] && current_dir="${current_dir}/"
                SELECTED_LOCAL="$current_dir"
                return 0
            elif [ "$opt" = "[Go Up (..)]" ]; then
                current_dir=$(dirname "$current_dir")
                break
            elif [ "$opt" = "[Go to Root (/)]" ]; then
                current_dir="/"
                break
            elif [ "$opt" = "[Create New Directory Here]" ]; then
                read -p "Enter new directory name: " new_dir
                if [ -n "$new_dir" ]; then
                    mkdir -p "$current_dir/$new_dir"
                    if [ "$current_dir" = "/" ]; then
                        current_dir="/$new_dir"
                    else
                        current_dir="$current_dir/$new_dir"
                    fi
                fi
                break
            elif [ -n "$opt" ]; then
                local chosen="${opt%/}"
                if [ "$current_dir" = "/" ]; then
                    current_dir="/$chosen"
                else
                    current_dir="$current_dir/$chosen"
                fi
                break
            else
                echo "Invalid option."
            fi
        done
    done
}


# 3. Virtual Environment & Dependencies
echo -e "\n${YELLOW}[3/5] Setting up virtual environment and web dependencies...${NC}"
pwd
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo -e "Web dependencies installed."

# 4. Database Setup
echo -e "\n${YELLOW}[4/5] Initializing Database...${NC}"
python3 db.py
echo -e "Database initialized."

# 5. Systemd Service Installation
echo -e "\n${YELLOW}[5/5] Installing Systemd services...${NC}"

# Create temporary service files with updated paths and user
TEMP_SYNC_SERVICE=$(mktemp)
TEMP_WEB_SERVICE=$(mktemp)

# Update data-sync.service
sed -e "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" \
    -e "s|ExecStart=.*|ExecStart=$PYTHON_BIN $PROJECT_DIR/sync_manager.py|" \
    -e "s|User=.*|User=$CURRENT_USER|" \
    "$PROJECT_DIR/data-sync.service" > "$TEMP_SYNC_SERVICE"

# Update data-sync-web.service
sed -e "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" \
    -e "s|ExecStart=.*|ExecStart=$PROJECT_DIR/venv/bin/uvicorn web_server:app --host 0.0.0.0 --port 8000|" \
    -e "s|User=.*|User=$CURRENT_USER|" \
    "$PROJECT_DIR/data-sync-web.service" > "$TEMP_WEB_SERVICE"

sudo cp "$TEMP_SYNC_SERVICE" /etc/systemd/system/data-sync.service
sudo cp "$TEMP_WEB_SERVICE" /etc/systemd/system/data-sync-web.service
rm "$TEMP_SYNC_SERVICE" "$TEMP_WEB_SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable data-sync.service
sudo systemctl restart data-sync.service
sudo systemctl enable data-sync-web.service
sudo systemctl restart data-sync-web.service

echo -e "\n${GREEN}Setup Complete!${NC}"
echo -e "The sync service is now running in the background."
echo -e "Web Dashboard available at: ${YELLOW}http://localhost:8000${NC}"
echo -e "Check sync status: ${YELLOW}systemctl status data-sync.service${NC}"
echo -e "Check web status: ${YELLOW}systemctl status data-sync-web.service${NC}"
