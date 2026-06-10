from flask import Flask, render_template, request, redirect, url_for, session, flash
import requests
import tomllib
import os
from db import init_db, create_user, get_user, check_password, \
    add_show, get_watchlist, update_progress, update_status, \
    update_rating, remove_show, get_user_stats

# Load secrets file to get the API key and secret key
with open("./secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

# TMDB is the API I used to search for TV shows. 
TMDB_KEY   = secrets["tmdb"]["api_key"]
TMDB_BASE  = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w185"  # used poster images

# Create the Flask app
app = Flask(__name__)
# The secret key is used by Flask to keep login sessions secured
app.secret_key = secrets["app"]["secret_key"]

# helpers:

# If someone tries to visit a page without logging in, it redirects them to login.
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# calculates what percentage of a show has been watched
def progress_percent(watched, total):
    if not total:
        return 0
    return round((watched / total) * 100)


# Authority:

# Home page so redirects to dashboard if logged in, or login page if not
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


# Register page
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        # Validate that fields aren't empty
        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")
        if len(password) < 4:
            flash("Password must be at least 4 characters.", "danger")
            return render_template("register.html")
        # Try to create the user and returns false if username is taken
        if create_user(username, password):
            flash("Account created! Please log in.", "success")
            return redirect(url_for("login"))
        else:
            flash("Username already taken. Try another.", "danger")
    return render_template("register.html")


# Login page which checks username/password and saves user info to the session
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = get_user(username)
        if user and check_password(password, user["password"]):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "danger")
    return render_template("login.html")


# Logout which clears the session so the user is no longer logged in
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# Dashboard:

# Main page showing the user's watchlist.
# Also filtering by status (watching/completed/watchlist) and sorting.
@app.route("/dashboard")
@login_required
def dashboard():
    sort          = request.args.get("sort", "date")
    filter_status = request.args.get("filter", "all")
    all_shows     = get_watchlist(session["user_id"], sort)

    # Calculate percentage progress for each show
    for s in all_shows:
        s["percent"] = progress_percent(s["watched_episodes"], s["total_episodes"])

    # Filter the list if the user selected a specific category
    if filter_status != "all":
        shows = [s for s in all_shows if s["status"] == filter_status]
    else:
        shows = all_shows

    return render_template("dashboard.html",
        shows=shows,
        sort=sort,
        filter_status=filter_status,
        count_watching=len([s for s in all_shows if s["status"] == "watching"]),
        count_completed=len([s for s in all_shows if s["status"] == "completed"]),
        count_watchlist=len([s for s in all_shows if s["status"] == "watchlist"]),
    )


# Profile:

# Profile page showing the user's stats like average rating and total episodes watched
@app.route("/profile")
@login_required
def profile():
    stats = get_user_stats(session["user_id"])
    return render_template("profile.html", stats=stats)


# Search:

# Search page that uses the TMDB API
@app.route("/search")
@login_required
def search():
    query   = request.args.get("q", "").strip()
    results = []
    error   = None

    if query:
        try:
            # Make an API request to TMDB with key and the search query
            resp = requests.get(
                f"{TMDB_BASE}/search/tv",
                params={"api_key": TMDB_KEY, "query": query},
                timeout=5
            )
            data = resp.json().get("results", [])
            # Only take the top 10 results so it doesn't overwhelm the user
            for show in data[:10]:
                year   = (show.get("first_air_date") or "")[:4] or "N/A"
                poster = show.get("poster_path")
                results.append({
                    "id":      show.get("id"),
                    "title":   show.get("name", "Unknown"),
                    "year":    year,
                    "summary": (show.get("overview") or "")[:150] + ("..." if len(show.get("overview") or "") > 150 else ""),
                    "image":   TMDB_IMAGE + poster if poster else "",
                    "status":  show.get("status", ""),
                })
        except Exception:
            error = "Could not reach the TV show database. Please try again."
        if not results and not error:
            error = "No matching TV shows found."

    return render_template("search.html", results=results, query=query, error=error)


# Show detail page 
@app.route("/show/<int:show_id>")
@login_required
def show_detail(show_id):
    try:
        resp = requests.get(
            f"{TMDB_BASE}/tv/{show_id}",
            params={"api_key": TMDB_KEY},
            timeout=5
        )
        data     = resp.json()
        total    = data.get("number_of_episodes", 0)
        year     = (data.get("first_air_date") or "")[:4] or "N/A"
        poster   = data.get("poster_path")
        networks = data.get("networks", [])
        show = {
            "id":      show_id,
            "title":   data.get("name"),
            "year":    year,
            "total":   total,
            "seasons": data.get("number_of_seasons", 0),
            "image":   TMDB_IMAGE + poster if poster else "",
            "summary": data.get("overview", ""),
            "network": networks[0]["name"] if networks else "N/A",
            "status":  data.get("status", ""),
        }
    except Exception:
        flash("Could not load show details.", "danger")
        return redirect(url_for("search"))
    return render_template("show_detail.html", show=show)


# Handles when a user clicks "Add to My List"
@app.route("/add/<int:show_id>", methods=["POST"])
@login_required
def add_show_route(show_id):
    title     = request.form.get("title")
    year      = request.form.get("year")
    total     = int(request.form.get("total", 0))
    status    = request.form.get("status", "watchlist")
    image_url = request.form.get("image_url", "")

    # Get optional episode count and default to 0 if not filled in
    watched_str = request.form.get("watched_episodes", "").strip()
    try:
        watched = int(watched_str) if watched_str else 0
        if watched < 0 or watched > total:
            watched = 0
    except ValueError:
        watched = 0

    # Get optional rating and only save it if it's a valid number in range
    rating_str = request.form.get("rating", "").strip()
    rating = None
    if rating_str:
        try:
            r = float(rating_str)
            if 1 <= r <= 5 and (r * 2) == int(r * 2):
                rating = r
        except ValueError:
            pass

    success = add_show(session["user_id"], show_id, title, year, total,
                       status, image_url, watched, rating)
    if success:
        flash(f'"{title}" added to your {status}!', "success")
    else:
        flash(f'"{title}" is already in your list.', "warning")
    return redirect(url_for("dashboard"))


# Update actions:

# Updates episode progress and auto marks as completed if all episodes watched
@app.route("/update_progress/<show_id>", methods=["POST"])
@login_required
def update_progress_route(show_id):
    watched_str = request.form.get("watched", "")
    try:
        watched = int(watched_str)
    except ValueError:
        flash("Please enter a valid whole number.", "danger")
        return redirect(url_for("dashboard"))
    ok, msg = update_progress(session["user_id"], show_id, watched)
    if ok:
        flash("Progress updated!" + (" Show complete! 🎉" if msg == "completed" else ""), "success")
    else:
        flash(msg, "danger")
    return redirect(url_for("dashboard"))


# Updates the category of a show (watchlist/watching/completed)
# If marked completed, automatically fill in the episode count
@app.route("/update_status/<show_id>", methods=["POST"])
@login_required
def update_status_route(show_id):
    status = request.form.get("status")
    update_status(session["user_id"], show_id, status)
    if status == "completed":
        flash("Show marked as completed! Episodes auto-filled. 🎉", "success")
    else:
        flash("Status updated.", "success")
    return redirect(url_for("dashboard"))


# Saves a rating for a show 
@app.route("/update_rating/<show_id>", methods=["POST"])
@login_required
def update_rating_route(show_id):
    rating_str = request.form.get("rating", "").strip()
    if not rating_str:
        ok, msg = update_rating(session["user_id"], show_id, None)
    else:
        try:
            rating = float(rating_str)
        except ValueError:
            flash("Rating must be a number between 1 and 5.", "danger")
            return redirect(url_for("dashboard"))
        ok, msg = update_rating(session["user_id"], show_id, rating)
    if ok:
        flash("Rating saved.", "success")
    else:
        flash(msg, "danger")
    return redirect(url_for("dashboard"))


# Removes a show from the user's list
@app.route("/remove/<show_id>", methods=["POST"])
@login_required
def remove_show_route(show_id):
    remove_show(session["user_id"], show_id)
    flash("Show removed from your list.", "info")
    return redirect(url_for("dashboard"))


# Run:

if __name__ == "__main__":
    os.makedirs("instance", exist_ok=True)
    init_db()
    app.run(debug=True)
