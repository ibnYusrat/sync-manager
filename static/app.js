let liveStatusInterval;

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    fetchConfig();
    fetchHistory();
    fetchLiveStatus();
    
    initPollInterval();
    
    // Poll history every 10 seconds
    setInterval(fetchHistory, 10000);
});

// Polling Management
function initPollInterval() {
    // Default to 5 seconds if not set
    const savedInterval = localStorage.getItem('pollInterval') || '5000';
    const select = document.getElementById('poll-interval');
    if (select) select.value = savedInterval;
    applyPollInterval(parseInt(savedInterval));
}

function changePollInterval() {
    const select = document.getElementById('poll-interval');
    if (!select) return;
    const interval = parseInt(select.value);
    localStorage.setItem('pollInterval', interval.toString());
    applyPollInterval(interval);
}

function applyPollInterval(intervalMs) {
    if (liveStatusInterval) {
        clearInterval(liveStatusInterval);
    }
    if (intervalMs > 0) {
        liveStatusInterval = setInterval(fetchLiveStatus, intervalMs);
    }
}

// Theme Management
function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    
    if (savedTheme === 'dark' || (!savedTheme && systemPrefersDark)) {
        document.body.classList.add('dark-mode');
        updateThemeIcon(true);
    }
}

function toggleTheme() {
    const isDark = document.body.classList.toggle('dark-mode');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    updateThemeIcon(isDark);
}

function updateThemeIcon(isDark) {
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.textContent = isDark ? '☀️' : '🌙';
    }
    const btn = document.getElementById('theme-toggle');
    if (btn) {
        btn.title = isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode';
    }
}

async function fetchConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        document.getElementById('ssh-host-display').textContent = data.ssh_host || 'Not set';
        
        const tbody = document.getElementById('config-table-body');
        tbody.innerHTML = '';
        if (data.pairs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="text-center">No pairs configured.</td></tr>';
            return;
        }
        
        data.pairs.forEach(pair => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${pair.source}</td>
                <td>${pair.destination}</td>
                <td>
                    <button class="btn-danger btn-small" onclick="deletePair(${pair.id})">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to fetch config", e);
    }
}

async function fetchLiveStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const container = document.getElementById('live-status-container');
        
        if (data.length === 0) {
            container.innerHTML = '<div class="text-center" style="padding: 2rem;">No active sync pairs configured.</div>';
            return;
        }
        
        container.innerHTML = '';
        data.forEach(item => {
            const status = item.status || 'idle';
            const pct = item.global_pct || 0;
            const filePct = item.file_pct || '0%';
            
            const el = document.createElement('div');
            el.className = 'status-item';
            el.innerHTML = `
                <div class="status-header">
                    <div>
                        ${item.source} <span style="color:var(--text-muted)">→</span> ${item.destination}
                        ${item.exclude ? `<br><small style="color:var(--text-muted)">Excluding: ${item.exclude}</small>` : ''}
                    </div>
                    <span class="status-badge ${status}">${status}</span>
                </div>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: ${pct}%"></div>
                </div>
                <div class="status-details">
                    <div class="detail-item">
                        <span class="detail-label">Global Progress</span>
                        <span class="detail-value">${pct.toFixed(1)}%</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Current File</span>
                        <span class="detail-value">${item.current_file || '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">File Progress</span>
                        <span class="detail-value">${filePct}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Speed</span>
                        <span class="detail-value">${item.speed || '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">ETA</span>
                        <span class="detail-value">${item.eta || '-'}</span>
                    </div>
                </div>
                ${item.error_message ? '<div class="error-text">Error: ' + item.error_message + '</div>' : ''}
                ${(function() {
                    if (item.ignored_files) {
                        try {
                            const ignored = JSON.parse(item.ignored_files);
                            if (ignored && ignored.length > 0) {
                                // Escape strings for safety in inline onclick
                                const safeIgnoredJson = JSON.stringify(ignored).replace(/'/g, "&apos;").replace(/"/g, "&quot;");
                                
                                return `
                                <div style="margin-top: 0.5rem; display: flex; align-items: center; font-size: 0.875rem; color: var(--text-muted);">
                                    <div class="custom-tooltip-container">
                                        <span style="display: inline-block; width: 16px; height: 16px; border-radius: 50%; background: #3b82f6; color: white; text-align: center; line-height: 16px; font-size: 12px; margin-right: 6px; cursor: pointer;" onclick="openIgnoredModal('${safeIgnoredJson}')">i</span>
                                        <div class="custom-tooltip">Click for details<br>First ignored: ${ignored[0]}</div>
                                    </div>
                                    <span>${ignored.length} directory/files ignored due to read permissions</span>
                                </div>`;
                            }
                        } catch(e) {}
                    }
                    return '';
                })()}
            `;
            container.appendChild(el);
        });
    } catch (e) {
        console.error("Failed to fetch status", e);
    }
}

async function fetchHistory() {
    try {
        const res = await fetch('/api/history');
        const data = await res.json();
        const tbody = document.getElementById('history-table-body');
        
        tbody.innerHTML = '';
        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center">No history available.</td></tr>';
            return;
        }
        
        data.forEach(item => {
            const date = new Date(item.timestamp).toLocaleString();
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${date}</td>
                <td>${item.source}</td>
                <td>${item.destination}</td>
                <td>${item.duration || '-'}</td>
                <td>${item.total_files || '-'}</td>
                <td>${item.transferred_size || '-'}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to fetch history", e);
    }
}

function showAddPairModal() {
    document.getElementById('new-source').value = '';
    document.getElementById('new-dest').value = '';
    document.getElementById('add-pair-modal').classList.add('active');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

async function addPair() {
    const source = document.getElementById('new-source').value;
    const dest = document.getElementById('new-dest').value;
    if (!source || !dest) return alert("Source and destination are required.");
    
    try {
        const res = await fetch('/api/config/pair', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source, destination: dest})
        });
        if (res.ok) {
            closeModal('add-pair-modal');
            fetchConfig();
            fetchLiveStatus(); // Instantly refresh
        } else {
            const error = await res.json();
            alert("Failed to add pair: " + (error.detail || "Unknown error"));
        }
    } catch (e) {
        alert("Error adding pair");
    }
}

async function deletePair(id) {
    if (!confirm("Are you sure you want to delete this directory pair?")) return;
    try {
        const res = await fetch('/api/config/pair/' + id, {method: 'DELETE'});
        if (res.ok) {
            fetchConfig();
            fetchLiveStatus();
        }
    } catch (e) {
        alert("Error deleting pair");
    }
}

async function editHost() {
    const newHost = prompt("Enter new SSH Host:");
    if (newHost) {
        try {
            const res = await fetch('/api/config/host', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ssh_host: newHost})
            });
            if (res.ok) {
                fetchConfig();
            }
        } catch(e) {
            alert("Error updating host");
        }
    }
}

// Browser State
let browserState = {
    type: 'local', // 'local' or 'remote'
    targetId: '',  // ID of the input to fill
    currentPath: ''
};

function openBrowser(type, targetId) {
    browserState.type = type;
    browserState.targetId = targetId;
    browserState.currentPath = ''; // Backend will decide default (usually / or home)
    
    document.getElementById('browser-title').textContent = `Browse ${type === 'local' ? 'Local' : 'Remote'} Directories`;
    document.getElementById('browser-modal').classList.add('active');
    
    fetchDirectory('');
}

async function fetchDirectory(path) {
    const listContainer = document.getElementById('browser-list');
    listContainer.innerHTML = '<div class="loading" style="padding: 1rem;">Loading...</div>';
    
    try {
        const url = `/api/browse/${browserState.type}?path=${encodeURIComponent(path)}`;
        const res = await fetch(url);
        const data = await res.json();
        
        if (res.ok) {
            browserState.currentPath = data.current_path;
            document.getElementById('current-browser-path').textContent = data.current_path;
            
            listContainer.innerHTML = '';
            data.entries.forEach(entry => {
                const item = document.createElement('div');
                item.className = 'browser-item';
                if (entry.name === '..') item.classList.add('up-dir');
                
                // Add an icon (simple text for now, or could use SVG/FontAwesome if available)
                const icon = entry.name === '..' ? '↑' : '📁';
                
                item.innerHTML = `<span>${icon}</span> <span>${entry.name}</span>`;
                item.onclick = () => fetchDirectory(entry.path);
                listContainer.appendChild(item);
            });
            
            if (data.entries.length === 0) {
                listContainer.innerHTML = '<div style="padding: 1rem; color: var(--text-muted);">No subdirectories found.</div>';
            }
        } else {
            listContainer.innerHTML = `<div class="error-text" style="padding: 1rem;">Error: ${data.detail}</div>`;
        }
    } catch (e) {
        listContainer.innerHTML = `<div class="error-text" style="padding: 1rem;">Failed to fetch directory list.</div>`;
    }
}

function confirmSelection() {
    let selectedPath = browserState.currentPath;
    // Ensure it ends with / for rsync if it doesn't already (and isn't root)
    if (selectedPath !== '/' && !selectedPath.endsWith('/')) {
        selectedPath += '/';
    }
    document.getElementById(browserState.targetId).value = selectedPath;
    closeModal('browser-modal');
}

function openIgnoredModal(ignoredFilesJson) {
    const modal = document.getElementById('ignored-modal');
    const list = document.getElementById('ignored-list');
    list.innerHTML = '';
    
    try {
        const ignored = JSON.parse(ignoredFilesJson);
        ignored.forEach(file => {
            const li = document.createElement('li');
            li.textContent = file;
            li.style.marginBottom = '0.5rem';
            list.appendChild(li);
        });
        modal.style.display = 'flex';
    } catch (e) {
        console.error("Failed to parse ignored files for modal", e);
    }
}

function closeIgnoredModal() {
    document.getElementById('ignored-modal').style.display = 'none';
}

window.onclick = function(event) {
    const browserModal = document.getElementById('browser-modal');
    const ignoredModal = document.getElementById('ignored-modal');
    
    if (event.target == browserModal) {
        closeModal('browser-modal');
    }
    if (event.target == ignoredModal) {
        closeIgnoredModal();
    }
}
