import os
import sqlite3
from pathlib import Path
from datetime import date, timedelta, datetime
from io import BytesIO
from flask import Flask, request, redirect, url_for, session, render_template_string, g, send_file
import openpyxl

# ------------------------------------------------------------------------------
# ENV-Helper
# ------------------------------------------------------------------------------
def _get_env(key, default=None):
    return os.getenv(key, default)

def _get_env_float(key, default):
    try:
        return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception:
        return default

def _get_env_date(key, default_iso):
    return date.fromisoformat(os.getenv(key, default_iso))

def _parse_password_map(default_names):
    raw = os.getenv("MITARBEITER_PASSWORDS", "")
    mp = {}
    for chunk in raw.split(","):
        if ":" in chunk:
            name, pw = chunk.split(":", 1)
            mp[name.strip()] = pw.strip()
    for n in default_names:
        mp.setdefault(n, f"{n.lower()}123")
    return mp

# ------------------------------------------------------------------------------
# Konfiguration
# ------------------------------------------------------------------------------
SECRET_KEY = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORT = _get_env("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI = _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL = _get_env_float("PREIS_HENDL", 22.30)

MITARBEITER = [m.strip() for m in os.getenv("MITARBEITER", "Julia,Regina,Florian,Schorsch,Toni,Jonas").split(",")]
MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

DATA_START = _get_env_date("DATA_START", "2025-09-20")
DATA_END = _get_env_date("DATA_END", "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18")
EDIT_WINDOW_END = _get_env_date("EDIT_WINDOW_END", "2025-10-07")

DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# ------------------------------------------------------------------------------
# App
# ------------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ------------------------------------------------------------------------------
# DB-Helfer
# ------------------------------------------------------------------------------
def ensure_db_dir(path: str):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        ensure_db_dir(DATABASE_PATH)
        db = g._database = sqlite3.connect(DATABASE_PATH, timeout=30.0, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS eintraege (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum TEXT,
            mitarbeiter TEXT,
            summe_start REAL,
            bar REAL,
            bier INTEGER,
            alkoholfrei INTEGER,
            hendl INTEGER,
            steuer REAL,
            gesamt REAL,
            bar_entnommen REAL,
            tagessumme REAL,
            gespeichert INTEGER,
            UNIQUE(datum, mitarbeiter)
        )
    """)
    db.commit()

with app.app_context():
    init_db()

# ------------------------------------------------------------------------------
# Login
# ------------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name")
        admin_pw = request.form.get("admin_pw")
        if admin_pw and admin_pw == ADMIN_PASSWORT:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear()
            session["name"] = name
            session["admin"] = False
            return redirect(url_for("eingabe", datum=str(date.today())))
    return render_template_string("""
    <!doctype html>
    <html lang="de">
    <head>
      <meta charset="utf-8">
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
      <title>Login</title>
    </head>
    <body class="bg-light">
      <div class="container py-5">
        <div class="card shadow-sm">
          <div class="card-body">
            <h3 class="mb-3">Login</h3>
            <form method="post">
              <div class="mb-3">
                <label class="form-label">Mitarbeiter</label>
                <select name="name" class="form-select">
                  <option value="">-- auswählen --</option>
                  {% for m in mitarbeiter %}<option>{{m}}</option>{% endfor %}
                </select>
              </div>
              <div class="text-center my-2">oder</div>
              <div class="mb-3">
                <label class="form-label">Admin Passwort</label>
                <input type="password" class="form-control" name="admin_pw">
              </div>
              <button class="btn btn-primary w-100">Einloggen</button>
            </form>
            {% if demo_mode %}
              <div class="alert alert-info mt-3">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt (Steuer nur mittwochs sichtbar).</div>
            {% endif %}
          </div>
        </div>
      </div>
    </body>
    </html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ------------------------------------------------------------------------------
# Eingabe
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()

    # Bearbeitungslogik
    if DEMO_MODE:
        im_edit_zeitraum = True
        tag_im_erlaubten_bereich = True
    else:
        tag_im_erlaubten_bereich = DATA_START <= datum_obj <= DATA_END
        heute_im_bearb_fenster = EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END
        im_edit_zeitraum = heute_im_bearb_fenster and tag_im_erlaubten_bereich

    db = get_db()
    aktiver_user = session.get("name", "ADMIN")
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?", (datum, aktiver_user)).fetchone()

    action = request.form.get("action")

    # Speichern
    if request.method == "POST" and action == "save" and im_edit_zeitraum:
        if datum_obj == DATA_START:
            summe_start = float(request.form.get("summe_start", 0))
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?", (vortag.isoformat(), aktiver_user)).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)

        bar = float(request.form.get("bar", 0))
        bier = int(request.form.get("bier", 0))
        alkoholfrei = int(request.form.get("alkoholfrei", 0))
        hendl = int(request.form.get("hendl", 0))
        steuer = float(request.form.get("steuer", 0) if wochentag == 2 else 0)
        bar_entnommen = float(request.form.get("bar_entnommen", 0))

        gesamt = bar + bier * PREIS_BIER + alkoholfrei * PREIS_ALKOHOLFREI + hendl * PREIS_HENDL - steuer
        tagessumme = gesamt - bar_entnommen

        if row:
            db.execute("""UPDATE eintraege SET summe_start=?, bar=?, bier=?, alkoholfrei=?, hendl=?, steuer=?, gesamt=?, bar_entnommen=?, tagessumme=?, gespeichert=1 WHERE id=?""",
                       (summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme, row["id"]))
        else:
            db.execute("""INSERT INTO eintraege (datum, mitarbeiter, summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme, gespeichert)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                       (datum, aktiver_user, summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme))
        db.commit()
        return redirect(url_for("eingabe", datum=datum))  # bleibt auf Datum-Seite

    # Anzeige
    if row:
        summe_start = row["summe_start"] or 0
        bar = row["bar"] or 0
        bier = row["bier"] or 0
        alkoholfrei = row["alkoholfrei"] or 0
        hendl = row["hendl"] or 0
        steuer = row["steuer"] or 0
        bar_entnommen = row["bar_entnommen"] or 0
        gesamt = row["gesamt"] or 0
        tagessumme = row["tagessumme"] or 0
    else:
        summe_start = 0
        bar = bier = alkoholfrei = hendl = steuer = bar_entnommen = 0
        gesamt = 0
        tagessumme = 0

    return render_template_string("""
    <!doctype html>
    <html lang="de">
    <head>
      <meta charset="utf-8">
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
      <title>Eingabe</title>
    </head>
    <body class="bg-light">
      <div class="container py-4">
        <h3>Eingabe für {{datum}} – {{name}}</h3>
        <form method="post" class="card p-3">
          <input type="hidden" name="action" value="save">
          <div class="mb-2"><label>Summe Start</label><input type="number" step="0.01" name="summe_start" value="{{summe_start}}" class="form-control"></div>
          <div class="mb-2"><label>Bar</label><input type="number" step="0.01" name="bar" value="{{bar}}" class="form-control"></div>
          <div class="mb-2"><label>Bier</label><input type="number" name="bier" value="{{bier}}" class="form-control"></div>
          <div class="mb-2"><label>Alkoholfrei</label><input type="number" name="alkoholfrei" value="{{alkoholfrei}}" class="form-control"></div>
          <div class="mb-2"><label>Hendl</label><input type="number" name="hendl" value="{{hendl}}" class="form-control"></div>
          {% if wochentag == 2 %}
          <div class="mb-2"><label>Steuer</label><input type="number" step="0.01" name="steuer" value="{{steuer}}" class="form-control"></div>
          {% endif %}
          <div class="mb-2"><label>Bar entnommen</label><input type="number" step="0.01" name="bar_entnommen" value="{{bar_entnommen}}" class="form-control"></div>
          <button class="btn btn-success mt-2">Speichern</button>
        </form>
      </div>
    </body>
    </html>
    """, datum=datum, name=aktiver_user, summe_start=summe_start, bar=bar, bier=bier,
       alkoholfrei=alkoholfrei, hendl=hendl, steuer=steuer, bar_entnommen=bar_entnommen,
       gesamt=gesamt, tagessumme=tagessumme, wochentag=wochentag)

# ------------------------------------------------------------------------------
# Admin
# ------------------------------------------------------------------------------
@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""SELECT datum, SUM(gesamt) AS tag_summe, SUM(steuer) AS steuer_summe FROM eintraege GROUP BY datum ORDER BY datum""").fetchall()
    return {"rows": [dict(r) for r in rows]}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
