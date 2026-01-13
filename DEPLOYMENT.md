# Deployment Guide - Split Architecture

## Architecture Overview

```
Windows Laptop (Dev Machine):
├── Frontend (Express)
│   localhost:3000
├── Backend (tunneled from server)
│   localhost:5000 ← SSH tunnel to server:5000
└── Ollama (tunneled from server)
    localhost:11434 ← SSH tunnel to server:11434

Linux Server:
├── Backend (FastAPI)
│   localhost:5000 (runs here, tunneled to laptop)
├── Ollama
│   localhost:11434 (runs here, tunneled to laptop)
└── PST Data
    /data/messages.pst (stored here)
```

---

## Server Setup (One-Time)

### 1. Clone Repository on Server

```bash
ssh user@server
cd /path/to/install/location  # Any location on the server
git clone https://github.com/yourusername/sift.git sift
cd sift/backend
```

**Note**: All paths are relative to the installation directory. PST files should be placed in `./data/` subdirectory.

### 2. Install Python Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Create Data Directory and Copy PST Files

```bash
mkdir -p data/outputs

# Copy your PST files here
cp /path/to/your/emails.pst data/
```

### 4. Run Backend (Keep Running)

```bash
source venv/bin/activate
python main.py
```

Backend will run on `localhost:5000`. All data is stored relative to the installation directory in `./data/`

---

## Local Development (Daily Workflow)

### Terminal 1: SSH Tunnel (Keep Running)

Open **two SSH tunnels** from your Windows laptop:

```bash
# Tunnel 1: Backend (5000:5000)
ssh -L 5000:localhost:5000 user@server

# Keep this running!
```

OR in a separate PowerShell/CMD:

```bash
# Tunnel 2: Ollama (11434:11434) - if you want to test Ollama locally
ssh -L 11434:localhost:11434 user@server
```

After running these, both services are accessible on `localhost`:
- Backend: `http://localhost:5000`
- Ollama: `http://localhost:11434`

### Terminal 2: Start Frontend (Local)

```bash
cd frontend
npm install  # First time only
npm start
```

Frontend runs on `http://localhost:3000`

### Terminal 3: Optional - Monitor Backend Logs

```bash
ssh user@server
tail -f /opt/sift/backend/logs/*.log
```

---

## Frontend Configuration

Frontend automatically uses `localhost:5000` for backend API calls (see `frontend/public/app.js`):

```javascript
const API_URL = "http://localhost:5000";
```

**No changes needed** - the SSH tunnel makes the server feel like `localhost`.

---

## Uploading PST File to Server

### Option 1: SCP (Recommended)

```bash
scp path/to/your_file.pst user@server:/opt/sift/data/
```

### Option 2: Via Upload API

Once backend is running, use the web UI or curl:

```bash
curl -X POST http://localhost:5000/parse \
  -F "file=@your_file.pst" \
  -F "date_start=2025-10-01" \
  -F "date_end=2025-12-31"
```

---

## Development Workflow

### Daily Startup (3 terminals)

**Terminal 1 - SSH Tunnel**:
```bash
ssh -L 5000:localhost:5000 -L 11434:localhost:11434 user@server
# Keep running (just a tunnel, no output)
```

**Terminal 2 - Frontend**:
```bash
cd frontend
npm start
# Opens http://localhost:3000
```

**Terminal 3 - Monitor Logs**:
```bash
ssh user@server
tail -f /opt/sift/backend/logs/*.log
```

### Making Code Changes

**Backend Code**:
1. Edit `backend/app/*.py` on your laptop
2. Push to git: `git add . && git commit -m "..." && git push`
3. On server: `cd /opt/sift && git pull && python main.py` (restart backend)

**Frontend Code**:
1. Edit `frontend/public/*.js` or `public/index.html`
2. Save - hot reload automatic (Express watches files)
3. Refresh browser

**Config/Prompts**:
1. Edit `config.json` or `prompts/*.json`
2. Changes take effect on next API call (no restart needed)

---

## Troubleshooting

### "Connection refused" on localhost:5000

SSH tunnel not established. Check:

```bash
# In a new terminal, verify tunnel is active
netstat -an | grep 5000  # Windows
# or
lsof -i :5000  # macOS/Linux
```

### Backend logs not updating

On server, check if process is running:

```bash
ps aux | grep "python main.py"
```

Restart if needed:

```bash
cd /opt/sift/backend
python main.py  # Must be interactive (no background)
```

### Frontend can't reach backend

Check:
1. SSH tunnel is active (`netstat -an | grep 5000`)
2. Backend is running on server (`ps aux | grep python`)
3. Browser console shows error (F12 → Network tab)

### Ollama not accessible

Ensure second SSH tunnel is active:

```bash
ssh -L 11434:localhost:11434 user@server
```

Or verify on server:

```bash
curl http://localhost:11434/api/tags
```

---

## Restarting Backend

If backend crashes or needs restart:

```bash
# SSH to server
ssh user@server
cd /opt/sift/backend
python main.py  # Restarts backend
```

Frontend will auto-reconnect next time you interact with it.

---

## Deployment Checklist

- [ ] Git initialized and pushed
- [ ] Backend cloned on server
- [ ] Python venv created on server
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Backend running on server (`python main.py`)
- [ ] SSH tunnels established (2 tunnels)
- [ ] Frontend running locally (`npm start`)
- [ ] Test: http://localhost:3000 loads
- [ ] Test: Backend health check: http://localhost:5000/docs
- [ ] PST file uploaded to server

---

## Architecture Benefits

✅ PST parsing works (Linux pypff)
✅ No C++ build tools needed
✅ Frontend stays lightweight on Windows
✅ Easy to deploy updates (git pull)
✅ Logs centralized on server
✅ Single source of truth (git repo)

---

## Next Steps

1. Deploy backend to server (this guide)
2. Test Phase 1: PST parsing with sample file
3. Continue Phase 2: Ollama integration (backend only)
4. Frontend stays on Windows, unaffected by backend changes
