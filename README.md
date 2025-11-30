# sub-watcher

A small FastAPI service that monitors configured subreddits and surfaces posts that match your filters. The app runs a background watcher loop and serves a simple HTML dashboard at the root path.

## Setup
1. Create and activate a virtual environment (optional but recommended).
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the sample config and edit it with your values:
   ```bash
   cp config.example.yaml config.yaml
   # then update config.yaml
   ```

## Running the app
Start the combined watcher and dashboard server with Uvicorn:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

The watcher loop starts on application startup, and the dashboard is served at `/` using the Jinja template in `templates/dashboard.html`.
