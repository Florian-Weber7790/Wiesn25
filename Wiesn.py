import os
import sqlite3
from pathlib import Path
from datetime import date, timedelta, datetime
from io import BytesIO

from flask import Flask, request, redirect, url_for, session, render_template_string, g, send_file
import openpyxl

# ------------------ ENV Helper ------------------
def _get_env(key, default=None):
    return os.getenv(key, default)

def _get_env_float(key, default):
    try:
        return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception:
        return default

def _get_env_date(key, default_iso):
    raw = os.getenv(key, default_iso)
    return date.fromisoformat(raw)

def _parse_password_map(default_names):
    raw = os.getenv("MITARBEITER_PASSWORDS", "")
    mp = {}
    for chunk in raw.split(","):
        if ":" in chunk:
            name, pw = chunk.split(":", 1)
            name, pw = name.strip(), pw.strip()
            if name and pw:
                mp[name] = pw
    for n in default_names:
        mp.setdefault(n, f"{n.lower()}123")
    return mp

# ------------------ Config ------------------
SECRET_KEY = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORT = _get_env("ADMIN_PASSWORD", "admin123")
DATABASE_PATH = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI = _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL = _get_env_float("PREIS_HENDL", 22.30)

MITARBEITER = [m.strip() for m in os.getenv("MITARBEITER", "Julia,Regina,Florian,Schorsch,Toni,Jonas").split(",") if m.strip()]
MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

DATA_START = _get_env_date("DATA_START", "2025-09-20")
DATA_END = _get_env_date("DATA_END", "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18")
EDIT_WINDOW_END = _get_env_date("EDIT_WINDOW_END", "2025-10-07")

DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ------------------ DB ------------------
def ensure_db_dir(path):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        ensure_db_dir(DATABASE_PATH)
        db = g._database = sqlite3.connect(DATABASE_PATH, timeout=30.0, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
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

# ------------------ Routes ------------------
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
            return redirect(url_for("eingabe", datum=str(date.today())))

    return render_template_string("""
    <html><body>
    <h3>Login</h3>
    <form method="post">
        <label>Mitarbeiter:</label>
        <select name="name">
        {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
        </select><br>
        <label>Admin Passwort:</label>
        <input type="password" name="admin_pw"><br>
        <button type="submit">Login</button>
    </form>
    {% if demo_mode %}<p>Demo Modus aktiv</p>{% endif %}
    </body></html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()

    tag_im_erlaubten_bereich = DATA_START <= datum_obj <= DATA_END
    heute_im_bearb_fenster = EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END

    if DEMO_MODE:
        im_edit_zeitraum = True
        tag_im_erlaubten_bereich = True
    else:
        im_edit_zeitraum = heute_im_bearb_fenster and tag_im_erlaubten_bereich

    db = get_db()
    aktiver_user = session.get("name", "ADMIN")
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?", (datum, aktiver_user)).fetchone()
    action = request.form.get("action")

    # Unlock
    if request.method == "POST" and action == "unlock":
        entered = request.form.get("edit_pw")
        if session.get("admin"):
            ok = (entered == ADMIN_PASSWORT)
        else:
            ok = (entered == MITARBEITER_PASSWOERTER.get(aktiver_user))
        if ok and row:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
        return redirect(url_for("eingabe", datum=datum))

    # Save
    if request.method == "POST" and action == "save" and im_edit_zeitraum and (not row or row["gespeichert"] == 0):
        if datum_obj == DATA_START:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?", (vortag.isoformat(), aktiver_user)).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)

        bar = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
        hendl = int(request.form.get("hendl", 0) or 0)
        steuer = float(request.form.get("steuer", 0) or 0) if wochentag == 2 else 0
        bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)

        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL) - steuer
        tagessumme = gesamt - bar_entnommen

        if row:
            db.execute("""UPDATE eintraege
                SET summe_start=?, bar=?, bier=?, alkoholfrei=?, hendl=?, steuer=?, gesamt=?, bar_entnommen=?, tagessumme=?, gespeichert=1
                WHERE id=?""",
                (summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme, row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
                (datum, mitarbeiter, summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme, gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                (datum, aktiver_user, summe_start, bar, bier, alkoholfrei, hendl, steuer, gesamt, bar_entnommen, tagessumme))
        db.commit()
        return redirect(url_for("eingabe", datum=datum))  # <- bleibt auf Seite

    # Anzeige
    if row:
        summe_start, bar, bier, alkoholfrei, hendl, steuer, bar_entnommen, gesamt, tagessumme, gespeichert = \
            row["summe_start"], row["bar"], row["bier"], row["alkoholfrei"], row["hendl"], row["steuer"], row["bar_entnommen"], row["gesamt"], row["tagessumme"], row["gespeichert"]
    else:
        summe_start = 0
        bar = bier = alkoholfrei = hendl = steuer = bar_entnommen = 0
        gesamt = tagessumme = 0
        gespeichert = 0

    return render_template_string("""
    <html><body>
    <h3>Eingabe {{datum}} - {{name}}</h3>
    <form method="post">
        <input type="hidden" name="action" value="save">
        Summe Start: <input name="summe_start" value="{{summe_start}}" {% if gespeichert %}readonly{% endif %}><br>
        Bar: <input name="bar" value="{{bar}}" {% if gespeichert %}readonly{% endif %}><br>
        Bier: <input name="bier" value="{{bier}}" {% if gespeichert %}readonly{% endif %}><br>
        Alkoholfrei: <input name="alkoholfrei" value="{{alkoholfrei}}" {% if gespeichert %}readonly{% endif %}><br>
        Hendl: <input name="hendl" value="{{hendl}}" {% if gespeichert %}readonly{% endif %}><br>
        {% if wochentag == 2 %}Steuer: <input name="steuer" value="{{steuer}}" {% if gespeichert %}readonly{% endif %}><br>{% endif %}
        Bar entnommen: <input name="bar_entnommen" value="{{bar_entnommen}}" {% if gespeichert %}readonly{% endif %}><br>
        Gesamt: {{gesamt}}<br>
        Tagessumme: {{tagessumme}}<br>
        {% if not gespeichert %}<button type="submit">Speichern</button>{% endif %}
    </form>
    {% if gespeichert %}
    <form method="post">
        <input type="hidden" name="action" value="unlock">
        Passwort: <input type="password" name="edit_pw">
        <button type="submit">Freischalten</button>
    </form>
    {% endif %}
    <a href="{{ url_for('login') }}">Zur Startseite</a>
    </body></html>
    """, datum=datum, name=aktiver_user, summe_start=summe_start, bar=bar, bier=bier,
       alkoholfrei=alkoholfrei, hendl=hendl, steuer=steuer, bar_entnommen=bar_entnommen,
       gesamt=gesamt, tagessumme=tagessumme, gespeichert=gespeichert, wochentag=wochentag)

@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""SELECT datum, SUM(gesamt) as tag_summe, SUM(steuer) as steuer_summe
                         FROM eintraege GROUP BY datum ORDER BY datum""").fetchall()
    data = []
    prev_sum = None
    for r in rows:
        s = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else s - prev_sum
        pro_person = None if diff is None else diff / 6.0
        data.append((r["datum"], s, diff, pro_person, float(r["steuer_summe"] or 0)))
        prev_sum = s
    return str(data)

@app.route("/export_excel")
def export_excel():
    if not session.get("admin"):
        return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""SELECT datum, SUM(gesamt) as tag_summe, SUM(steuer) as steuer_summe
                         FROM eintraege GROUP BY datum ORDER BY datum""").fetchall()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Datum", "Gesamt", "Differenz", "Umsatz/Person", "Steuer"])
    prev_sum = None
    for r in rows:
        s = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else s - prev_sum
        pro_person = None if diff is None else diff / 6.0
        ws.append([r["datum"], s, diff or "", pro_person or "", float(r["steuer_summe"] or 0)])
        prev_sum = s
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="export.xlsx")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)

