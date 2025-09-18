import os, sqlite3, shutil, time
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file, flash
)
import openpyxl

# ------------------------------------------------------------------------------
# ENV / Konfiguration
# ------------------------------------------------------------------------------
def _env(key, default=None): return os.getenv(key, default)
def _env_float(key, default):
    try: return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception: return default
def _env_date(key, default_iso): return date.fromisoformat(os.getenv(key, default_iso))

def _parse_pw_map(default_names):
    raw = os.getenv("MITARBEITER_PASSWORDS", "")
    mp = {}
    for chunk in raw.split(","):
        if ":" in chunk:
            n, pw = chunk.split(":", 1)
            n, pw = n.strip(), pw.strip()
            if n and pw: mp[n] = pw
    for n in default_names:
        mp.setdefault(n, f"{n.lower()}123")
    return mp

SECRET_KEY     = _env("SECRET_KEY", "change-me")
ADMIN_PASS     = _env("ADMIN_PASSWORD", "Ramona")
DB_PATH        = _env("DATABASE_PATH", "verkauf.db")

PREIS_BIER     = _env_float("PREIS_BIER", 14.01)
PREIS_ALK      = _env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL    = _env_float("PREIS_HENDL", 22.30)

MITARBEITER = [m.strip() for m in os.getenv(
    "MITARBEITER", "Florian,Jonas,Julia,Regina,Schorsch,Toni"
).split(",") if m.strip()]
MITARBEITER_PASSW = _parse_pw_map(MITARBEITER)

DATA_START  = _env_date("DATA_START", "2025-09-20")
DATA_END    = _env_date("DATA_END",   "2025-10-05")
EDIT_START  = _env_date("EDIT_WINDOW_START", "2025-09-18")
EDIT_END    = _env_date("EDIT_WINDOW_END",   "2025-10-07")
DEMO_MODE   = os.getenv("DEMO_MODE", "0") == "1"

COUNTDOWN_DEADLINE = datetime(2025, 10, 5, 23, 0, 0)

# ------------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ------------------------------------------------------------------------------
# DB
# ------------------------------------------------------------------------------
def ensure_db_dir(path):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        ensure_db_dir(DB_PATH)
        db = g._db = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
    return db

@app.teardown_appcontext
def close_db(_=None):
    db = getattr(g, "_db", None)
    if db: db.close()

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

with app.app_context(): init_db()

# ------------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status": "ok", "demo": DEMO_MODE, "time": datetime.utcnow().isoformat()}

# ------------------------------------------------------------------------------
# Login + Countdown
# ------------------------------------------------------------------------------
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name") or ""
        admin_pw = request.form.get("admin_pw") or ""
        if admin_pw and admin_pw == ADMIN_PASS:
            session.clear(); session["admin"] = True
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear(); session["name"] = name; session["admin"] = False
            return redirect(url_for("eingabe", datum=str(date.today())))
        flash("Bitte Mitarbeiter wählen oder Admin-Passwort eingeben.")
        return redirect(url_for("login"))

    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Willkommen</title>
<style>
:root{--blue:#0a2a66;}
body{background:var(--blue);color:#fff;}
.card-login{background:#fff;color:#111;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25);}
.countdown{font-size:1.5rem;font-weight:700;}
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
<div class="container" style="max-width:980px;">
  <div class="text-center mb-4">
    <h1 class="display-6">Willkommen zur Wiesn-Abrechnung</h1>
    <p class="opacity-75 mb-2">Bearbeitung möglich zwischen 18.09. und 07.10.</p>
    <div id="countdown" class="countdown">–</div>
  </div>
  <div class="card card-login mx-auto mt-4" style="max-width:520px;">
    <div class="card-body p-4">
      <h4 class="mb-3">Login</h4>
      {% with msgs = get_flashed_messages() %}
        {% if msgs %}<div class="alert alert-danger py-2">{{ msgs[0] }}</div>{% endif %}
      {% endwith %}
      <form method="post">
        <div class="mb-3">
          <label class="form-label">Mitarbeiter</label>
          <select name="name" class="form-select">
            <option value="">-- auswählen --</option>
            {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
          </select>
        </div>
        <div class="text-center my-2 text-white-50">oder</div>
        <div class="mb-3">
          <label class="form-label">Admin Passwort</label>
          <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
        </div>
        <button class="btn btn-primary w-100">Einloggen</button>
      </form>
      {% if demo_mode %}
        <div class="alert alert-info small mt-3 mb-0">
          Demo-Modus aktiv: Bearbeitung jederzeit erlaubt (Steuer nur mittwochs sichtbar).
        </div>
      {% endif %}
    </div>
  </div>
</div>
<script>
const deadline=new Date("2025-10-05T23:00:00");
function updateCountdown(){
  const diff=Math.max(0,(deadline-new Date())/1000);
  const d=Math.floor(diff/86400),h=Math.floor((diff%86400)/3600),m=Math.floor((diff%3600)/60);
  document.getElementById('countdown').textContent=`${d} Tage ${h} Std ${m} Min verbleiben`;
}
updateCountdown();setInterval(updateCountdown,60000);
</script></body></html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ------------------------------------------------------------------------------
# Eingabe
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    user = session.get("name", "ADMIN")
    d_obj = date.fromisoformat(datum)
    wtag = d_obj.weekday()
    erster_tag = d_obj == DATA_START

    if DEMO_MODE:
        edit_ok = True
    else:
        edit_ok = (EDIT_START <= date.today() <= EDIT_END) and (DATA_START <= d_obj <= DATA_END)

    db = get_db()
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?", (datum, user)).fetchone()
    action = request.form.get("action")

    # entsperren
    if request.method == "POST" and action == "unlock":
        entered = (request.form.get("edit_pw") or "").strip()
        pw_ok = (entered == ADMIN_PASS) if session.get("admin") else (entered == MITARBEITER_PASSW.get(user))
        if pw_ok and row:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
            flash("Eintrag entsperrt 🔓")
        else:
            flash("Falsches Passwort ❌")
        return redirect(url_for("eingabe", datum=datum))

    # speichern
    if request.method == "POST" and action == "save" and edit_ok and (DEMO_MODE or (not row or row["gespeichert"] == 0)):
        # Summe Start nur am 20.09 oder nach Entsperren änderbar
        if erster_tag or (row and row["gespeichert"] == 0):
            summe_start = float(request.form.get("summe_start") or 0)
        else:
            v = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                           ((d_obj - timedelta(days=1)).isoformat(), user)).fetchone()
            summe_start = float(v["tagessumme"] if v else 0.0)

        bar  = float(request.form.get("bar")  or 0)
        bier = int(request.form.get("bier")   or 0)
        alk  = int(request.form.get("alkoholfrei") or 0)
        hendl= int(request.form.get("hendl") or 0)
        steuer = float(request.form.get("steuer") or 0) if wtag == 2 else 0.0
        gesamt = bar + bier*PREIS_BIER + alk*PREIS_ALK + hendl*PREIS_HENDL
        bar_entn = float(request.form.get("bar_entnommen") or 0)
        tagessumme = gesamt - bar_entn

        if row:
            db.execute("""UPDATE eintraege SET
                summe_start=?,bar=?,bier=?,alkoholfrei=?,hendl=?,steuer=?,
                gesamt=?,bar_entnommen=?,tagessumme=?,gespeichert=1 WHERE id=?""",
                (summe_start,bar,bier,alk,hendl,steuer,gesamt,bar_entn,tagessumme,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
                (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,steuer,
                 gesamt,bar_entnommen,tagessumme,gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                (datum,user,summe_start,bar,bier,alk,hendl,steuer,gesamt,bar_entn,tagessumme))
        db.commit()
        flash("Gespeichert ✅")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige
    if row: vals = dict(row)
    else:
        v = None if erster_tag else db.execute(
            "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
            ((d_obj - timedelta(days=1)).isoformat(), user)).fetchone()
        vals = dict(summe_start=(0.0 if erster_tag else float(v["tagessumme"] if v else 0)),
                    bar=0,bier=0,alkoholfrei=0,hendl=0,steuer=0,
                    gesamt=0,bar_entnommen=0,tagessumme=0,gespeichert=0)

    is_new = row is None
    may_edit_summe = erster_tag or (row and row["gespeichert"] == 0)

    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
<style>
body{background:#f6f7fb;}
.calc-field{background:#e9ecef;}
.editable{background:#fff7d6;}
.readonly{background:#e9ecef;}
.app-card{background:#fff;border:1px solid rgba(13,110,253,.08);box-shadow:0 10px 30px rgba(0,0,0,.05);border-radius:14px;}
</style>
</head>
<body class="container py-4">
<h3 class="mb-3 text-center">Eingabe – {{name}} <small class="text-muted">({{datum}})</small></h3>
{% with msgs = get_flashed_messages() %}
  {% if msgs %}<div class="alert alert-info text-center">{{ msgs[0] }}</div>{% endif %}
{% endwith %}

<!-- Kalender oben zentriert -->
<div class="d-flex justify-content-center mb-3">
  <input type="date" id="datumsauswahl" class="form-control text-center" style="max-width:240px"
         value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
</div>
<!-- Vortag/Folgetag -->
<div class="d-flex justify-content-center gap-2 mb-4">
  <a href="{{ url_for('eingabe', datum=(d_obj - timedelta(days=1)).isoformat()) }}" class="btn btn-outline-primary">← Vortag</a>
  <a href="{{ url_for('eingabe', datum=(d_obj + timedelta(days=1)).isoformat()) }}" class="btn btn-outline-primary">Folgetag →</a>
</div>

<form method="post" oninput="berechne()" class="card app-card p-3 mx-auto" style="max-width:900px;">
  <input type="hidden" name="action" value="save">
  <div class="row g-3 justify-content-center">
    {% set ro = not (im_edit and not vals['gespeichert']) %}
    <div class="col-12 col-md-6">
      <label class="form-label">Summe Start (€)</label>
      <input name="summe_start" type="number" step="0.01"
             value="{{ '' if is_new else vals['summe_start'] }}"
             class="form-control {% if may_edit_summe %}editable{% else %}readonly{% endif %}"
             {% if not may_edit_summe %}readonly{% endif %}>
    </div>
    {% for field,label in [('bar','Bar (€)'),('bier','Bier (Anzahl)'),('alkoholfrei','Alkoholfrei (Anzahl)'),('hendl','Hendl (Anzahl)')] %}
    <div class="col-12 col-md-4">
      <label class="form-label">{{label}}</label>
      <input name="{{field}}" id="{{field}}" type="number"
             value="{{ '' if is_new else vals[field] }}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>
    {% endfor %}
    {% if wtag == 2 %}
    <div class="col-12 col-md-6">
      <label class="form-label">Steuer (€) – nur Mittwoch</label>
      <input name="steuer" id="steuer" type="number" step="0.01"
             value="{{ '' if is_new else vals['steuer'] }}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>
    {% endif %}
    <div class="col-12 col-md-6">
      <label class="form-label">Bar entnommen (€)</label>
      <input name="bar_entnommen" id="bar_entnommen" type="number" step="0.01"
             value="{{ '' if is_new else vals['bar_entnommen'] }}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>
    <div class="col-12 col-md-6">
      <label class="form-label">Gesamt (€)</label>
      <input id="gesamt" class="form-control calc-field" readonly
             value="{{ '' if is_new else ('%.2f' % vals['gesamt']) }}">
    </div>
    <div class="col-12 col-md-6">
      <label class="form-label">Tagessumme (€)</label>
      <input id="tagessumme" class="form-control calc-field" readonly
             value="{{ '' if is_new else ('%.2f' % vals['tagessumme']) }}">
    </div>
    <div class="col-12 text-center">
      {% if im_edit and not vals['gespeichert'] %}
        <button class="btn btn-success mt-2
