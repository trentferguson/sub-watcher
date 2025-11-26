# models.py
from pathlib import Path

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Text,
)
from sqlalchemy.orm import sessionmaker, declarative_base

# --- Database Setup ---

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "history.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    echo=False,  # set to True for SQL debug logs
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# --- Models ---

class MatchedPost(Base):
    __tablename__ = "matched_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subreddit = Column(String, nullable=False)
    post_id = Column(String, nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text)
    score = Column(Integer)
    flair = Column(String)
    created_utc = Column(Float)
    matched_at = Column(String, nullable=False)


class SeenPost(Base):
    __tablename__ = "seen_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subreddit = Column(String, nullable=False)
    post_id = Column(String, nullable=False, unique=True)
    first_seen_at = Column(String, nullable=False)


# --- Helper: ensure tables exist ---
def init_db():
    Base.metadata.create_all(bind=engine)
