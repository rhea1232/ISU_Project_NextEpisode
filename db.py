import sqlite3
from contextlib import contextmanager
import bcrypt

DB_NAME = "./instance/nextepisode.db"

@contextmanager
def db_session(db_name):
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        print(f"Database error: {e}")
        raise
    finally:
        conn.close()


def init_db():
    with db_session(DB_NAME) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                show_id TEXT NOT NULL,
                title TEXT NOT NULL,
                release_year TEXT,
                total_episodes INTEGER DEFAULT 0,
                watched_episodes INTEGER DEFAULT 0,
                status TEXT DEFAULT 'watchlist',
                rating REAL,
                image_url TEXT,
                date_added TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, show_id)
            );
        """)
    # Migrate existing databases that don't have image_url column yet.
    # Check first so we don't trigger a noisy "duplicate column" error message.
    with sqlite3.connect(DB_NAME) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(watchlist)")]
        if "image_url" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN image_url TEXT")
            conn.commit()


def create_user(username, password):
    """Returns True on success, False if username taken."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    try:
        with db_session(DB_NAME) as conn:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, hashed.decode())
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user(username):
    with db_session(DB_NAME) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def check_password(plain, hashed):
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def add_show(user_id, show_id, title, release_year, total_episodes,
             status="watchlist", image_url=None,
             watched_episodes=0, rating=None):
    """Returns True on success, False if duplicate."""
    valid = {"watchlist", "watching", "completed"}
    if status not in valid:
        status = "watchlist"
    # If added as completed, auto-fill all episodes
    if status == "completed":
        watched_episodes = total_episodes
    try:
        with db_session(DB_NAME) as conn:
            conn.execute(
                """INSERT INTO watchlist
                   (user_id, show_id, title, release_year, total_episodes,
                    watched_episodes, status, image_url, rating)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, str(show_id), title, release_year, total_episodes,
                 watched_episodes, status, image_url, rating)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_watchlist(user_id, sort="date"):
    sort_map = {
        "alpha":  "title ASC",
        "rating": "rating DESC NULLS LAST",
        "date":   "date_added DESC"
    }
    order = sort_map.get(sort, "date_added DESC")
    with db_session(DB_NAME) as conn:
        rows = conn.execute(
            f"SELECT * FROM watchlist WHERE user_id = ? ORDER BY {order}",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_progress(user_id, show_id, watched):
    with db_session(DB_NAME) as conn:
        row = conn.execute(
            "SELECT total_episodes FROM watchlist WHERE user_id=? AND show_id=?",
            (user_id, show_id)
        ).fetchone()
        if not row:
            return False, "Show not found"
        total = row["total_episodes"]
        if watched < 0:
            return False, "Episodes cannot be negative"
        if watched > total:
            return False, f"Show only has {total} episodes"
        new_status = "completed" if watched == total else "watching"
        conn.execute(
            """UPDATE watchlist SET watched_episodes=?, status=?
               WHERE user_id=? AND show_id=?""",
            (watched, new_status, user_id, show_id)
        )
        return True, new_status


def update_status(user_id, show_id, status):
    """When marked completed, auto-fill all episodes. When moved back, don't reset progress."""
    valid = {"watchlist", "watching", "completed"}
    if status not in valid:
        return False
    with db_session(DB_NAME) as conn:
        if status == "completed":
            # Auto-fill watched_episodes = total_episodes
            conn.execute(
                """UPDATE watchlist
                   SET status=?, watched_episodes=total_episodes
                   WHERE user_id=? AND show_id=?""",
                (status, user_id, show_id)
            )
        else:
            conn.execute(
                "UPDATE watchlist SET status=? WHERE user_id=? AND show_id=?",
                (status, user_id, show_id)
            )
    return True


def update_rating(user_id, show_id, rating):
    if rating is not None and (rating < 1 or rating > 5):
        return False, "Rating must be between 1 and 5"
    if rating is not None and (rating * 2) != int(rating * 2):
        return False, "Rating must be in 0.5 increments (e.g. 4.5)"
    with db_session(DB_NAME) as conn:
        conn.execute(
            "UPDATE watchlist SET rating=? WHERE user_id=? AND show_id=?",
            (rating, user_id, show_id)
        )
    return True, "OK"


def remove_show(user_id, show_id):
    with db_session(DB_NAME) as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id=? AND show_id=?",
            (user_id, show_id)
        )


def get_user_stats(user_id):
    """Returns stats for the profile page."""
    with db_session(DB_NAME) as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_id=?", (user_id,)
        ).fetchall()
    shows = [dict(r) for r in rows]

    total        = len(shows)
    watching     = sum(1 for s in shows if s["status"] == "watching")
    completed    = sum(1 for s in shows if s["status"] == "completed")
    watchlist    = sum(1 for s in shows if s["status"] == "watchlist")
    eps_watched  = sum(s["watched_episodes"] for s in shows)
    rated        = [s["rating"] for s in shows if s["rating"] is not None]
    avg_rating   = round(sum(rated) / len(rated), 1) if rated else None
    top_rated    = sorted(
        [s for s in shows if s["rating"] is not None],
        key=lambda x: x["rating"], reverse=True
    )[:3]

    return {
        "total":       total,
        "watching":    watching,
        "completed":   completed,
        "watchlist":   watchlist,
        "eps_watched": eps_watched,
        "avg_rating":  avg_rating,
        "top_rated":   top_rated,
        "shows":       shows,
    }
