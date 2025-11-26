import requests
import yaml
import time
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, UTC
from pathlib import Path
from models import (
    SessionLocal,
    MatchedPost,
    SeenPost,
    init_db,
)


CONFIG_PATH = Path(__file__).parent / "config.yaml"

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "subreddit-watcher.log"

LAST_CHECKED: dict[str, float] = {}  # r/subreddit -> timestamp of last check

def setup_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("subreddit_watcher")
    
    level = (level or "INFO").upper()
    log_level = getattr(logging, level, logging.INFO)
    logger.setLevel(log_level)

    # Avoid adding multiple handlers if setup_logger is called more than once
    if logger.handlers:
        # If logger is already initialized, update handler levels
        for h in logger.handlers:
            h.setLevel(log_level)
        return logger

    # File handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_000_000,  # ~1 MB
        backupCount=5,  # keep last 5 logs
        encoding="utf-8",
    )
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_level)

    # Console handler (optional, for nice terminal output)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(log_level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

logger = setup_logger()


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
    
    
def log_matched_post_sqlalchemy(
    subreddit: str,
    post_id: str,
    title: str,
    url: str | None,
    score: int | None,
    flair: str | None,
    created_utc: float | None,
):
    matched_at = datetime.now(UTC).isoformat(timespec="seconds")

    session = SessionLocal()
    try:
        entry = MatchedPost(
            subreddit=subreddit,
            post_id=post_id,
            title=title,
            url=url,
            score=score,
            flair=flair,
            created_utc=created_utc,
            matched_at=matched_at,
        )
        session.add(entry)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("Error logging matched post to DB: %s", e)
    finally:
        session.close()


def send_pushover_notification(
    api_token: str,
    user_key: str,
    title: str,
    message: str,
    url: str | None = None,
):
    """Send a push notification via Pushover."""
    if not api_token or not user_key:
        logger.info("Pushover not configured properly; skipping notification.")
        return

    endpoint = "https://api.pushover.net/1/messages.json"
    data = {
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
    }
    if url:
        data["url"] = url
        data["url_title"] = "Open in Reddit"

    try:
        resp = requests.post(endpoint, data=data, timeout=10)
        # Debug lines — handy while wiring things up:
        logger.info("Pushover status: %s", resp.status_code)
        logger.info("Pushover body: %s", resp.text)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Error sending Pushover notification: %s", e)
def fetch_new_posts(subreddit: str, limit: int = 2):
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    params = {"limit": str(limit)}
    headers = {
        # Reddit really wants a descriptive UA; this helps avoid 429s / blocks
        "User-Agent": "subreddit-watcher/0.1 by your_username"
    }

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    children = data.get("data", {}).get("children", [])
    posts = [child["data"] for child in children]
    return posts

# --- Helper: mark existing posts as seen on startup ---

def mark_initial_posts_as_seen(config: dict):
    logger.info("Marking existing posts as seen on startup...")

    db = SessionLocal()
    try:
        subreddits = config.get("subreddits", [])
        if not subreddits:
            logger.warning("No subreddits configured; nothing to mark as seen.")
            return

        for sub in subreddits:
            name = sub["name"]
            logger.info("  Fetching recent posts from r/%s to mark as seen...", name)

            try:
                # You can tune this limit; 50–100 is usually enough
                posts = fetch_new_posts(name, limit=50)
            except Exception as e:
                logger.error("    Error fetching r/%s during initial mark: %s", name, e)
                continue

            for post in posts:
                post_id = post.get("id")
                if not post_id:
                    continue

                # Only insert if not already present
                existing = (
                    db.query(SeenPost).filter(SeenPost.post_id == post_id).first()
                )
                if existing:
                    continue

                db.add(
                    SeenPost(
                        subreddit=name,
                        post_id=post_id,
                        first_seen_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    )
                )

            db.commit()

        logger.info(
            "Initial posts marked as seen; they will not trigger notifications."
        )
    finally:
        db.close()


def is_post_seen(db, post_id: str) -> bool:
    if not post_id:
        return False
    return db.query(SeenPost).filter(SeenPost.post_id == post_id).first() is not None


def mark_post_seen(db, subreddit: str, post_id: str):
    # Insert into seen_posts if not already present
    if not post_id:
        return
    exists = db.query(SeenPost).filter(SeenPost.post_id == post_id).first()
    if exists:
        return
    db.add(
        SeenPost(
            subreddit=subreddit,
            post_id=post_id,
            first_seen_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    )
    db.commit()


def run_once():
    config = load_config()

    pushover_cfg = config.get("pushover", {}) or {}
    pushover_token = (pushover_cfg.get("api_token") or "").strip()
    pushover_user = (pushover_cfg.get("user_key") or "").strip()

    subreddits = config.get("subreddits", [])
    if not subreddits:
        logger.warning("No subreddits configured in config.yaml.")
        return

    now = time.time()

    db = SessionLocal()
    try:
        for sub in subreddits:
            name = sub["name"]
            keywords = sub.get("keywords", [])
            min_score = sub.get("min_score")
            allowed_flairs = sub.get("allowed_flairs", [])
            max_notifications = sub.get("max_notifications_per_run")

            # Per-subreddit interval
            interval = sub.get("interval")
            if isinstance(interval, (int, float)) and interval > 0:
                last = LAST_CHECKED.get(name)
                if last is not None and (now - last) < interval:
                    logger.debug(
                        "Skipping r/%s; last checked %.1fs ago (interval=%ss)",
                        name,
                        now - last,
                        interval,
                    )
                    continue

            # Regex filters
            ignore_title_regex = sub.get("ignore_title_regex") or []
            require_title_regex = sub.get("require_title_regex") or []

            if not isinstance(ignore_title_regex, list):
                ignore_title_regex = [ignore_title_regex]
            if not isinstance(require_title_regex, list):
                require_title_regex = [require_title_regex]

            ignore_patterns = []
            for pattern in ignore_title_regex:
                if isinstance(pattern, str):
                    try:
                        ignore_patterns.append(re.compile(pattern))
                    except re.error as e:
                        logger.warning("Invalid ignore regex %r: %s", pattern, e)

            require_patterns = []
            for pattern in require_title_regex:
                if isinstance(pattern, str):
                    try:
                        require_patterns.append(re.compile(pattern))
                    except re.error as e:
                        logger.warning("Invalid require regex %r: %s", pattern, e)

            notified_count = 0
            logger.info("=== Checking r/%s ===", name)

            try:
                posts = fetch_new_posts(name, limit=10)
                LAST_CHECKED[name] = now
            except Exception as e:
                logger.error("Error fetching r/%s: %s", name, e)
                continue

            for post in posts:
                if (
                    isinstance(max_notifications, int)
                    and notified_count >= max_notifications
                ):
                    logger.info(
                        "Reached max_notifications_per_run (%s) for r/%s.",
                        max_notifications,
                        name,
                    )
                    break

                post_id = post.get("id")
                if not post_id:
                    continue

                # skip if we've already seen this post_id
                if is_post_seen(db, post_id):
                    continue

                title = post.get("title", "")
                permalink = post.get("permalink", "")
                full_url = f"https://reddit.com{permalink}" if permalink else ""
                score = post.get("score", 0)
                flair = (post.get("link_flair_text") or "").strip()
                created_utc = post.get("created_utc")

                # Ignore patterns
                if ignore_patterns and any(p.search(title) for p in ignore_patterns):
                    continue

                # Require patterns
                if require_patterns and not any(
                    p.search(title) for p in require_patterns
                ):
                    continue

                # Keywords
                if keywords:
                    lower = title.lower()
                    if not any(kw.lower() in lower for kw in keywords):
                        continue

                # Min score
                if isinstance(min_score, (int, float)) and score < min_score:
                    continue

                # Flair
                if allowed_flairs and flair not in allowed_flairs:
                    continue

                # --- PASSED ALL FILTERS ---
                logger.info(
                    "Matched in r/%s | title=%r | score=%s | flair=%r | url=%s",
                    name,
                    title,
                    score,
                    flair,
                    full_url,
                )
                print("\n🔔 NEW POST FOUND!")
                print(f"Title: {title}")
                print(f"Score: {score} | Flair: {flair or 'None'}")
                print(full_url)

                # Log match to DB
                log_matched_post_sqlalchemy(
                    subreddit=name,
                    post_id=post_id,
                    title=title,
                    url=full_url or None,
                    score=score,
                    flair=flair or None,
                    created_utc=created_utc,
                )

                # Mark as seen in DB so we don't process again
                mark_post_seen(db, name, post_id)

                # Send push notification
                send_pushover_notification(
                    api_token=pushover_token,
                    user_key=pushover_user,
                    title=f"r/{name}: {title}",
                    message=full_url or "New Reddit post",
                    url=full_url or None,
                )

                notified_count += 1
    finally:
        db.close()


def main():
    # Load config once at startup for logging + startup behavior + loop interval
    config = load_config()

    # Logging level comes only from config.yaml
    log_level = config.get("log_level", "INFO")
    setup_logger(log_level)
    logger.info("Logging initialized at level: %s", log_level)

    # Ensure database tables exist
    init_db()
    logger.info("Database initialized.")    

    # Optional: mark all existing posts as seen so we only get *new* stuff
    if config.get("mark_existing_as_seen_on_startup", False):
        mark_initial_posts_as_seen(config)

    # Determine loop interval (how often run_once() is called)
    # 1) If you want, you can add a global override in config:
    loop_interval = config.get("global_loop_interval_seconds")

    if not isinstance(loop_interval, (int, float)) or loop_interval <= 0:
        # 2) Otherwise, derive it from the smallest subreddit interval
        subreddits = config.get("subreddits", [])
        intervals = [
            sub.get("interval")
            for sub in subreddits
            if isinstance(sub.get("interval"), (int, float)) and sub.get("interval") > 0
        ]
        if intervals:
            loop_interval = min(intervals)
        else:
            loop_interval = 60  # sensible default if nothing specified

    logger.info("Main loop interval set to %s seconds", loop_interval)

    try:
        while True:
            run_once()  # run logic that respects per-subreddit intervals
            time.sleep(loop_interval)
    except KeyboardInterrupt:
        logger.info("Exiting watcher.")

if __name__ == "__main__":
    main()
