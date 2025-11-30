# app.py

import time
import threading
from typing import Optional, List

from fastapi import FastAPI, Depends, Query, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from fastapi.templating import Jinja2Templates
import yaml

from models import SessionLocal, MatchedPost, init_db
from monitor import run_once, load_config, setup_logger, mark_initial_posts_as_seen, CONFIG_PATH
import logging

app = FastAPI(title="Subreddit Watcher", version="1.0.0")

templates = Jinja2Templates(directory="templates")

# This will be set on startup based on config
LOOP_INTERVAL = 60.0


# --- DB session dependency for FastAPI ---


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Helper: compute loop interval from config ---


def compute_loop_interval(config: dict) -> float:
    loop_interval = config.get("global_loop_interval_seconds")

    if not isinstance(loop_interval, (int, float)) or loop_interval <= 0:
        subreddits = config.get("subreddits", [])
        intervals = [
            sub.get("interval")
            for sub in subreddits
            if isinstance(sub.get("interval"), (int, float)) and sub.get("interval") > 0
        ]
        if intervals:
            loop_interval = min(intervals)
        else:
            loop_interval = 60.0  # default
    return float(loop_interval)


# --- Background watcher loop ---

def watcher_loop():
    logger = logging.getLogger("subreddit_watcher")
    global LOOP_INTERVAL

    while True:
        try:
            run_once()  # this uses your existing logic & config
        except Exception as e:
            logger.error("Error in watcher loop: %s", e)
        time.sleep(LOOP_INTERVAL)


# --- FastAPI startup: init logging, DB, and start watcher thread ---

@app.on_event("startup")
def on_startup():
    global LOOP_INTERVAL

    config = load_config()

    # Setup logger based on config
    log_level = config.get("log_level", "INFO")
    setup_logger(log_level)
    logger = logging.getLogger("subreddit_watcher")
    logger.info("Logging initialized at level: %s", log_level)

    # Init DB
    init_db()
    logger.info("Database initialized.")

    # Determine the watcher loop interval
    LOOP_INTERVAL = compute_loop_interval(config)
    logger.info("Watcher loop interval set to %s seconds", LOOP_INTERVAL)

    # Mark existing posts as seen if enabled in config
    if config.get("mark_existing_as_seen_on_startup", False):
        mark_initial_posts_as_seen(config)

    # Start watcher in a background thread
    t = threading.Thread(target=watcher_loop, daemon=True)
    t.start()
    logger.info("Background watcher thread started.")


# --- Dashboard route  ---

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    subreddit: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(MatchedPost).order_by(MatchedPost.id.desc())
    if subreddit:
        query = query.filter(MatchedPost.subreddit == subreddit)

    posts: List[MatchedPost] = query.limit(limit).all()

    subreddits = (
        db.query(MatchedPost.subreddit)
        .distinct()
        .order_by(MatchedPost.subreddit.asc())
        .all()
    )
    subreddit_list = [row[0] for row in subreddits]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "posts": posts,
            "subreddits": subreddit_list,
            "selected_subreddit": subreddit,
            "limit": limit,
        },
    )


@app.get("/config", response_class=HTMLResponse)
def edit_config_page(request: Request):
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "# config.yaml not found; create it here.\n"

    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "content": text,
            "config_path": str(CONFIG_PATH),
            "error": None,
            "success": None,
        },
    )


@app.post("/config", response_class=HTMLResponse)
def save_config(
    request: Request,
    content: str = Form(...),
):
    # 1) Validate YAML only
    try:
        parsed = yaml.safe_load(content) if content.strip() else {}
    except yaml.YAMLError as e:
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "content": content,
                "config_path": str(CONFIG_PATH),
                "error": f"YAML parse error:\n{e}",
                "success": None,
            },
        )

    # 2) Normalize line endings (optional but nice) and write RAW text
    cleaned = content.replace("\r\n", "\n").rstrip() + "\n"
    CONFIG_PATH.write_text(cleaned, encoding="utf-8")

    # 3) Optionally recompute loop interval from new config
    global LOOP_INTERVAL
    if isinstance(parsed, dict):
        try:
            LOOP_INTERVAL = compute_loop_interval(parsed)
        except Exception:
            pass

    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "content": cleaned,
            "config_path": str(CONFIG_PATH),
            "error": None,
            "success": "Config saved successfully.",
        },
    )
