# dashboard.py

from fastapi import FastAPI, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional, List

from models import SessionLocal, MatchedPost, init_db

app = FastAPI(title="Subreddit Watcher Dashboard")


# Dependency to get a DB session per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    # Ensure tables exist
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(
    limit: int = Query(50, ge=1, le=500),
    subreddit: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Simple HTML dashboard showing recent matched posts.
    Optional ?subreddit=name filter.
    """

    query = db.query(MatchedPost).order_by(MatchedPost.id.desc())
    if subreddit:
        query = query.filter(MatchedPost.subreddit == subreddit)

    posts: List[MatchedPost] = query.limit(limit).all()

    # Get list of distinct subreddits for a filter dropdown
    subreddits = (
        db.query(MatchedPost.subreddit)
        .distinct()
        .order_by(MatchedPost.subreddit.asc())
        .all()
    )
    subreddit_list = [row[0] for row in subreddits]

    html_rows = []
    for p in posts:
        title = (p.title or "").replace("<", "&lt;").replace(">", "&gt;")
        url = p.url or ""
        flair = p.flair or ""
        score = p.score if p.score is not None else ""
        matched_at = p.matched_at or ""
        created_utc = p.created_utc if p.created_utc is not None else ""

        html_rows.append(
            f"""
            <tr>
                <td>{p.id}</td>
                <td>{p.subreddit}</td>
                <td>{flair}</td>
                <td>{score}</td>
                <td>{created_utc}</td>
                <td><a href="{url}" target="_blank">{title}</a></td>
                <td>{matched_at}</td>
            </tr>
            """
        )

    # Simple subreddit filter dropdown
    options_html = '<option value="">All</option>'
    for s in subreddit_list:
        selected = " selected" if subreddit == s else ""
        options_html += f'<option value="{s}"{selected}>{s}</option>'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Subreddit Watcher Dashboard</title>
        <style>
            body {{
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                margin: 20px;
                background: #0f172a;
                color: #e5e7eb;
            }}
            h1 {{
                margin-bottom: 0.2rem;
            }}
            .subtitle {{
                margin-top: 0;
                margin-bottom: 1rem;
                color: #9ca3af;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 1rem;
                font-size: 0.9rem;
            }}
            th, td {{
                padding: 8px 10px;
                border-bottom: 1px solid #1f2937;
                vertical-align: top;
            }}
            th {{
                background: #111827;
                position: sticky;
                top: 0;
                z-index: 1;
            }}
            tr:nth-child(even) {{
                background: #020617;
            }}
            tr:nth-child(odd) {{
                background: #020617;
            }}
            a {{
                color: #93c5fd;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .controls {{
                display: flex;
                gap: 1rem;
                align-items: center;
                margin-top: 0.5rem;
            }}
            label {{
                font-size: 0.85rem;
                color: #9ca3af;
            }}
            select, input[type="number"] {{
                background: #020617;
                color: #e5e7eb;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 4px 6px;
            }}
            button {{
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 10px;
                cursor: pointer;
                font-size: 0.85rem;
            }}
            button:hover {{
                background: #1d4ed8;
            }}
            .top-bar {{
                display: flex;
                justify-content: space-between;
                align-items: baseline;
                gap: 1rem;
            }}
        </style>
    </head>
    <body>
        <div class="top-bar">
            <div>
                <h1>Subreddit Watcher Dashboard</h1>
                <p class="subtitle">
                    Showing the {len(posts)} most recent matched posts
                    {f"for r/{subreddit}" if subreddit else ""}.
                </p>
            </div>
        </div>

        <form method="get" class="controls">
            <div>
                <label for="subreddit">Subreddit:</label>
                <select name="subreddit" id="subreddit">
                    {options_html}
                </select>
            </div>
            <div>
                <label for="limit">Limit:</label>
                <input type="number" id="limit" name="limit" min="1" max="500" value="{limit}">
            </div>
            <div>
                <button type="submit">Apply</button>
            </div>
        </form>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Subreddit</th>
                    <th>Flair</th>
                    <th>Score</th>
                    <th>Created (UTC)</th>
                    <th>Title</th>
                    <th>Matched At</th>
                </tr>
            </thead>
            <tbody>
                {"".join(html_rows) if html_rows else '<tr><td colspan="7">No posts found.</td></tr>'}
            </tbody>
        </table>
    </body>
    </html>
    """

    return HTMLResponse(content=html)
