"""Parking server with user accounts. Everything is stored in parking.db next to this file.
Run locally:  pip install flask gunicorn ; python server.py
Production:   gunicorn -w 1 -b 0.0.0.0:5000 server:app   (put HTTPS in front of it)
Users and emails:  sqlite3 parking.db "select username,email from users"
"""
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from flask import Flask, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
HOUR = 3600
TOKEN_DAYS = 30
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parking.db")
lock = threading.Lock()
fails = {}  # login attempts: key -> [count, first_time]

USER_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
MAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@contextmanager
def conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            email TEXT NOT NULL,
            pw_hash TEXT NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS spots(
            spot INTEGER PRIMARY KEY CHECK(spot BETWEEN 1 AND 6),
            user_id INTEGER UNIQUE NOT NULL,
            ends_at REAL NOT NULL,
            entered INTEGER NOT NULL DEFAULT 0);
        """)


init()


def err(reason, code):
    return jsonify(ok=False, reason=reason), code


def body():
    return request.get_json(force=True, silent=True) or {}


def new_token(c, user_id):
    t = secrets.token_hex(32)
    c.execute("INSERT INTO tokens(token,user_id,created) VALUES(?,?,?)", (t, user_id, time.time()))
    return t


def current_user(c):
    h = request.headers.get("Authorization", "")
    t = h[7:] if h.startswith("Bearer ") else ""
    return c.execute(
        "SELECT u.id, u.username FROM tokens t JOIN users u ON u.id=t.user_id "
        "WHERE t.token=? AND t.created>?", (t, time.time() - TOKEN_DAYS * 86400)).fetchone()


def cleanup(c):
    # a paid spot that nobody entered within its hour becomes free again
    c.execute("DELETE FROM spots WHERE entered=0 AND ends_at<?", (time.time(),))


@app.get("/")
def home():
    return "parking server OK"


@app.post("/register")
def register():
    d = body()
    username = str(d.get("username", "")).strip()
    password = str(d.get("password", ""))
    email = str(d.get("email", "")).strip()
    if not USER_RE.match(username):
        return err("bad_username", 400)
    if not 6 <= len(password) <= 100:
        return err("bad_password", 400)
    if not MAIL_RE.match(email) or len(email) > 120:
        return err("bad_email", 400)
    with lock, conn() as c:
        try:
            cur = c.execute("INSERT INTO users(username,email,pw_hash,created) VALUES(?,?,?,?)",
                            (username, email, generate_password_hash(password), time.time()))
        except sqlite3.IntegrityError:
            return err("username_taken", 409)
        token = new_token(c, cur.lastrowid)
    return jsonify(ok=True, token=token, username=username)


@app.post("/login")
def login():
    d = body()
    username = str(d.get("username", "")).strip()
    password = str(d.get("password", ""))
    key = username.lower() + "|" + str(request.remote_addr)
    now = time.time()
    cnt, first = fails.get(key, [0, now])
    if now - first > 300:
        cnt, first = 0, now
    if cnt >= 5:
        return err("too_many", 429)
    with lock, conn() as c:
        u = c.execute("SELECT id, username, pw_hash FROM users WHERE username=?", (username,)).fetchone()
        if not u or not check_password_hash(u["pw_hash"], password):
            fails[key] = [cnt + 1, first]
            return err("bad_credentials", 401)
        fails.pop(key, None)
        token = new_token(c, u["id"])
    return jsonify(ok=True, token=token, username=u["username"])


@app.post("/logout")
def logout():
    h = request.headers.get("Authorization", "")
    t = h[7:] if h.startswith("Bearer ") else ""
    with lock, conn() as c:
        c.execute("DELETE FROM tokens WHERE token=?", (t,))
    return jsonify(ok=True)


@app.get("/spots")
def spots():
    with lock, conn() as c:
        u = current_user(c)
        if not u:
            return err("unauthorized", 401)
        cleanup(c)
        taken = [False] * 6
        mine = None
        for r in c.execute("SELECT spot, user_id, ends_at, entered FROM spots"):
            taken[r["spot"] - 1] = True
            if r["user_id"] == u["id"]:
                mine = {"spot": r["spot"], "ends_at": r["ends_at"], "entered": bool(r["entered"])}
    return jsonify(ok=True, now=time.time(), taken=taken, mine=mine)


def spot_number():
    try:
        n = int(body().get("spot", 0))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 6 else None


@app.post("/reserve")
def reserve():
    n = spot_number()
    with lock, conn() as c:
        u = current_user(c)
        if not u:
            return err("unauthorized", 401)
        if n is None:
            return err("bad_spot", 400)
        cleanup(c)
        if c.execute("SELECT 1 FROM spots WHERE user_id=?", (u["id"],)).fetchone():
            return err("already_has", 409)
        end = time.time() + HOUR
        try:
            c.execute("INSERT INTO spots(spot,user_id,ends_at,entered) VALUES(?,?,?,0)", (n, u["id"], end))
        except sqlite3.IntegrityError:
            return err("taken", 409)
    return jsonify(ok=True, spot=n, ends_at=end)


@app.post("/extend")
def extend():
    with lock, conn() as c:
        u = current_user(c)
        if not u:
            return err("unauthorized", 401)
        r = c.execute("SELECT ends_at FROM spots WHERE user_id=?", (u["id"],)).fetchone()
        if not r:
            return err("no_spot", 404)
        end = max(r["ends_at"], time.time()) + HOUR
        c.execute("UPDATE spots SET ends_at=? WHERE user_id=?", (end, u["id"]))
    return jsonify(ok=True, ends_at=end)


@app.post("/entered")
def entered():
    with lock, conn() as c:
        u = current_user(c)
        if not u:
            return err("unauthorized", 401)
        c.execute("UPDATE spots SET entered=1 WHERE user_id=?", (u["id"],))
    return jsonify(ok=True)


@app.post("/release")
def release():
    with lock, conn() as c:
        u = current_user(c)
        if not u:
            return err("unauthorized", 401)
        c.execute("DELETE FROM spots WHERE user_id=?", (u["id"],))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
