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
def _get_env(key, default=None): return os.getenv(key, default)
def _get_env_float(key, default):
    try: return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception: return default
def _get_env_date(key, default_iso): return date.fromisoformat(os.getenv(key, default_iso))
def _parse_password_map(default_names):
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

SECRET_KEY        = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORT    = _get_env("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH     = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER        = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI = _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL       = _get_env_float("PREIS_HENDL", 22.30)

# feste Reihenfolge
MITARBEITER = ["Florian","Jonas","Julia","Regina","Schorsch","Toni"]
MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

DATA_START        = _get_env_date("DATA_START", "2025-09-20")
DATA_END          = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18")
EDIT_WINDOW_END   = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")
DEMO_MODE         = os.getenv("DEMO_MODE", "0") == "1"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ------------------------------------------------------------------------------
# DB
# ------------------------------------------------------------------------------
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
        db.execute("PRAGMA synchronous=NORMAL;")
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
with app.app_context(): init_db()

# ------------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status": "ok", "demo": DEMO_MODE, "time": datetime.utcnow().isoformat()}

# ------------------------------------------------------------------------------
# Login mit Countdown
# ------------------------------------------------------------------------------
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name") or ""
        admin_pw = request.form.get("admin_pw") or ""
        if admin_pw and admin_pw == ADMIN_PASSWORT:
            session.clear(); session["admin"] = True
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear(); session["name"] = name; session["admin"] = False
            return redirect(url_for("eingabe", datum=str(date.today())))
        flash("Bitte Mitarbeiter wählen oder Admin-Passwort eingeben.")
        return redirect(url_for("login"))

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Willkommen</title>
<style>
  :root{ --blue:#0a2a66; }
  body{ background:var(--blue); color:#fff; }
  .card-login{ background:#fff; color:#111; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.25); }
  .countdown{ font-size:1.4rem; font-weight:700; }
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
<div class="container" style="max-width:980px;">
  <div class="text-center mb-4">
    <h1 class="display-6 fw-bold">Willkommen zur Wiesn-Abrechnung</h1>
    <p class="opacity-75">Bearbeitung möglich zwischen 18.09. und 07.10.</p>
    <div id="countdown" class="countdown mb-3">–</div>
  </div>
  <div class="card card-login mx-auto mt-2" style="max-width:520px;">
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
        <div class="text-center my-2"><span class="text-muted">oder</span></div>
        <div class="mb-3">
          <label class="form-label">Admin Passwort</label>
          <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
        </div>
        <button class="btn btn-primary w-100">Einloggen</button>
      </form>
      {% if demo_mode %}
        <div class="alert alert-info small mt-3 mb-0">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.</div>
      {% endif %}
    </div>
  </div>
</div>
<script>
const deadline = new Date("2025-10-05T23:00:00");
function updateCountdown(){
  const now = new Date();
  let diff = (deadline - now)/1000;
  if(diff<0) diff=0;
  const d=Math.floor(diff/86400), h=Math.floor(diff%86400/3600), m=Math.floor(diff%3600/60);
  document.getElementById('countdown').textContent =
      `${d} Tage ${h} Std ${m} Min verbleiben`;
}
updateCountdown(); setInterval(updateCountdown,60000);
</script>
</body></html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ------------------------------------------------------------------------------
# Eingabe mit Zahlenfeldern
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    aktiver_user = session.get("name","ADMIN")
    datum_obj = date.fromisoformat(datum)
    wtag = datum_obj.weekday()
    ist_erster_tag = (datum_obj == DATA_START)

    if DEMO_MODE:
        im_edit_zeitraum = True
    else:
        heute_ok = EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END
        tag_ok   = DATA_START <= datum_obj <= DATA_END
        im_edit_zeitraum = heute_ok and tag_ok

    db = get_db()
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
                     (datum, aktiver_user)).fetchone()
    action = request.form.get("action")

    # --- Entsperren ---
    if request.method=="POST" and action=="unlock":
        if row:
            entered = (request.form.get("edit_pw") or "").strip()
            expected = ADMIN_PASSWORT if session.get("admin") else MITARBEITER_PASSWOERTER.get(aktiver_user)
            if entered == expected:
                db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],)); db.commit()
                flash("Eintrag entsperrt 🔓")
            else:
                flash("Falsches Passwort ❌")
        return redirect(url_for("eingabe", datum=datum))

    # --- Speichern ---
    if request.method=="POST" and action=="save" and im_edit_zeitraum and (DEMO_MODE or (not row or row["gespeichert"]==0)):
        row_exists = row is not None
        unlocked   = (row_exists and row["gespeichert"]==0)
        allow_edit_summe_start = (datum_obj == DATA_START) or unlocked

        if allow_edit_summe_start:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            v = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                ((datum_obj - timedelta(days=1)).isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(v["tagessumme"] if v else 0)

        bar = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
        hendl = int(request.form.get("hendl", 0) or 0)
        steuer = float(request.form.get("steuer", 0) or 0) if wtag==2 else 0
        gesamt = bar + bier*PREIS_BIER + alkoholfrei*PREIS_ALKOHOLFREI + hendl*PREIS_HENDL
        bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)
        tagessumme = gesamt - bar_entnommen

        if row:
            db.execute("""UPDATE eintraege
                          SET summe_start=?,bar=?,bier=?,alkoholfrei=?,hendl=?,steuer=?,
                              gesamt=?,bar_entnommen=?,tagessumme=?,gespeichert=1
                          WHERE id=?""",
                       (summe_start,bar,bier,alkoholfrei,hendl,steuer,
                        gesamt,bar_entnommen,tagessumme,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
                          (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,steuer,
                           gesamt,bar_entnommen,tagessumme,gespeichert)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                       (datum,aktiver_user,summe_start,bar,bier,alkoholfrei,hendl,steuer,
                        gesamt,bar_entnommen,tagessumme))
        db.commit(); flash("Gespeichert ✅")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige
    if row: vals = dict(row)
    else:
        if ist_erster_tag:
            summe_start = 0
        else:
            v = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                ((datum_obj - timedelta(days=1)).isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(v["tagessumme"] if v else 0)
        vals = dict(summe_start=summe_start, bar=0, bier=0,
                    alkoholfrei=0, hendl=0, steuer=0,
                    gesamt=0, bar_entnommen=0, tagessumme=0, gespeichert=0)

    vortag_link   = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
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

<h3 class="mb-3 text-center">Eingabe – {{name}}</h3>

{% with msgs = get_flashed_messages() %}
  {% if msgs %}<div class="alert alert-info text-center">{{ msgs[0] }}</div>{% endif %}
{% endwith %}

<div class="d-flex flex-column align-items-center gap-2 mb-3">
  <input type="date" id="datumsauswahl" class="form-control" style="max-width:220px"
         value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
  <div>
    <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary me-2">← Vortag</a>
    <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary">Folgetag →</a>
  </div>
</div>

<form method="post" oninput="berechne()" class="card app-card">
  <input type="hidden" name="action" value="save">
  <div class="card-body p-4">
    <div class="row g-3">
      <div class="col-12 col-md-6">
        <label class="form-label">Summe Start (€)</label>
        <input name="summe_start" type="number" inputmode="decimal" step="0.01"
               value="{{vals['summe_start'] if vals['summe_start'] else ''}}"
               class="form-control {% if im_edit_zeitraum and (datum==data_start or not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and (datum==data_start or not vals['gespeichert'])) %}readonly{% endif %}>
      </div>
      <div class="col-12 col-md-6">
        <label class="form-label">Bar (€)</label>
        <input name="bar" id="bar" type="number" inputmode="decimal" step="0.01"
               value="{{vals['bar'] if vals['bar'] else ''}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
      <div class="col-12 col-md-4">
        <label class="form-label">Bier (Anzahl)</label>
        <input name="bier" id="bier" type="number" inputmode="numeric" step="1"
               value="{{vals['bier'] if vals['bier'] else ''}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
      <div class="col-12 col-md-4">
        <label class="form-label">Alkoholfrei (Anzahl)</label>
        <input name="alkoholfrei" id="alkoholfrei" type="number" inputmode="numeric" step="1"
               value="{{vals['alkoholfrei'] if vals['alkoholfrei'] else ''}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
      <div class="col-12 col-md-4">
        <label class="form-label">Hendl (Anzahl)</label>
        <input name="hendl" id="hendl" type="number" inputmode="numeric" step="1"
               value="{{vals['hendl'] if vals['hendl'] else ''}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
      {% if wtag == 2 %}
      <div class="col-12 col-md-6">
        <label class="form-label">Steuer (€)</label
