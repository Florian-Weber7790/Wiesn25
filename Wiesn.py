import os, sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file, flash
)
import openpyxl

# ------------------------------------------------------------------------------
# Konfiguration
# ------------------------------------------------------------------------------
def _get_env(key, default=None): return os.getenv(key, default)
def _get_env_float(key, default):
    try: return float(os.getenv(key, str(default)).replace(",", "."))
    except: return default
def _get_env_date(key, default_iso):
    return date.fromisoformat(os.getenv(key, default_iso))

SECRET_KEY      = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORT  = _get_env("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH   = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER       = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI= _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL      = _get_env_float("PREIS_HENDL", 22.30)

MITARBEITER = [m.strip() for m in os.getenv(
    "MITARBEITER", "Julia,Regina,Florian,Schorsch,Toni,Jonas"
).split(",") if m.strip()]

DATA_START        = _get_env_date("DATA_START", "2025-09-20")
DATA_END          = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_START", "2025-09-18")
EDIT_WINDOW_END   = _get_env_date("EDIT_END",   "2025-10-07")
DEMO_MODE         = os.getenv("DEMO_MODE", "0") == "1"

# ------------------------------------------------------------------------------
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
        db = g._database = sqlite3.connect(
            DATABASE_PATH, timeout=30.0, check_same_thread=False
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
    return db

@app.teardown_appcontext
def close_connection(exc):
    db = getattr(g, "_database", None)
    if db:
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
@app.route("/")
def login():
    """Willkommens-/Login-Seite mit Countdown"""
    deadline = datetime(2025, 10, 5, 23, 0)
    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Willkommen</title>
<style>
  body{background:#001f3f;color:#fff;}
  h1,h3{color:#fff;}
  .countdown{font-size:1.5rem;font-weight:600;}
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center vh-100">
  <div class="text-center">
    <h1 class="mb-3">Willkommen zur Wiesn-Abrechnung</h1>
    <h3 class="mb-4">Bearbeitung möglich zwischen 18.09. und 07.10.</h3>
    <div id="countdown" class="countdown mb-5">–</div>
    <form method="post" action="{{ url_for('do_login') }}" class="bg-light p-4 rounded shadow text-dark">
      <h4 class="mb-3">Login</h4>
      <div class="mb-3">
        <label class="form-label">Mitarbeiter</label>
        <select name="name" class="form-select">
          <option value="">-- auswählen --</option>
          {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
        </select>
      </div>
      <div class="text-center my-2">oder</div>
      <div class="mb-3">
        <label class="form-label">Admin Passwort</label>
        <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
      </div>
      <button class="btn btn-primary w-100">Einloggen</button>
    </form>
  </div>
<script>
const deadline = new Date("{{deadline.isoformat()}}Z");
function updateCountdown(){
  const now = new Date();
  let diff = (deadline - now)/1000;
  if(diff<0) diff=0;
  const d=Math.floor(diff/86400);
  const h=Math.floor((diff%86400)/3600);
  const m=Math.floor((diff%3600)/60);
  document.getElementById("countdown").textContent =
    `${d} Tage ${h} Std ${m} Min verbleiben`;
}
setInterval(updateCountdown,60000);
updateCountdown();
</script>
</body></html>
""", mitarbeiter=MITARBEITER, deadline=deadline)

@app.route("/", methods=["POST"])
def do_login():
    name = request.form.get("name")
    pw   = request.form.get("admin_pw")
    if pw and pw == ADMIN_PASSWORT:
        session.clear(); session["admin"] = True
        return redirect(url_for("admin_view"))
    if name in MITARBEITER:
        session.clear(); session["name"] = name; session["admin"] = False
        return redirect(url_for("eingabe", datum=str(date.today())))
    flash("Ungültige Eingabe")
    return redirect(url_for("login"))

# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))
    user = session.get("name", "ADMIN")
    datum_obj = date.fromisoformat(datum)
    wtag = datum_obj.weekday()  # 0=Mo

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, user)
    ).fetchone()

    # Bearbeitungslogik
    if DEMO_MODE:
        im_edit = True
    else:
        im_edit = (EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END) and \
                  (DATA_START <= datum_obj <= DATA_END)

    action = request.form.get("action")

    # --- Entsperren ---
    if request.method == "POST" and action == "unlock":
        pw = request.form.get("edit_pw", "")
        if (session.get("admin") and pw == ADMIN_PASSWORT):
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
        return redirect(url_for("eingabe", datum=datum))

    # --- Speichern ---
    if request.method == "POST" and action == "save" and im_edit and (not row or row["gespeichert"] == 0):
        ist_erster_tag = datum_obj == DATA_START
        summe_start = float(request.form.get("summe_start", 0) or 0) \
            if ist_erster_tag else \
            (db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                        ((datum_obj - timedelta(days=1)).isoformat(), user)).fetchone() or {"tagessumme":0})["tagessumme"]
        bar = float(request.form.get("bar",0) or 0)
        bier = int(request.form.get("bier",0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei",0) or 0)
        hendl = int(request.form.get("hendl",0) or 0)
        steuer = float(request.form.get("steuer",0) or 0) if wtag==2 else 0
        gesamt = bar + bier*PREIS_BIER + alkoholfrei*PREIS_ALKOHOLFREI + hendl*PREIS_HENDL
        bar_ent = float(request.form.get("bar_entnommen",0) or 0)
        tagessumme = gesamt - bar_ent
        if row:
            db.execute("""UPDATE eintraege SET summe_start=?,bar=?,bier=?,alkoholfrei=?,
                          hendl=?,steuer=?,gesamt=?,bar_entnommen=?,tagessumme=?,gespeichert=1
                          WHERE id=?""",
                       (summe_start,bar,bier,alkoholfrei,hendl,steuer,gesamt,bar_ent,tagessumme,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
               (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,steuer,
                gesamt,bar_entnommen,tagessumme,gespeichert)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
               (datum,user,summe_start,bar,bier,alkoholfrei,hendl,steuer,gesamt,bar_ent,tagessumme))
        db.commit()
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige-Werte
    if row: vals = dict(row)
    else:
        v = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                       ((datum_obj - timedelta(days=1)).isoformat(), user)).fetchone()
        vals = dict(summe_start=(0 if datum_obj==DATA_START else float(v["tagessumme"]) if v else 0),
                    bar=0,bier=0,alkoholfrei=0,hendl=0,steuer=0,
                    gesamt=0,bar_entnommen=0,tagessumme=0,gespeichert=0)

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
<style>
  body{background:#f8f9fa;}
  .calc-field{background:#e9ecef;}
  .editable{background:#fff3cd;}
  .readonly{background:#e9ecef;}
  .btn-home{background:#0d6efd;color:#fff;}
</style>
</head>
<body class="container py-4">
<h3 class="mb-3">Eingabe für {{datum}} – {{name}}</h3>
<form method="post" class="card p-3 mb-3" oninput="berechne()">
  <input type="hidden" name="action" value="save">
  <div class="row g-3">
    <div class="col-md-6">
      <label class="form-label">Summe Start</label>
      <input name="summe_start" type="number" step="0.01" value="{{vals['summe_start']}}"
             class="form-control {% if im_edit and (datum==data_start or not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and (datum==data_start or not vals['gespeichert'])) %}readonly{% endif %}>
    </div>
    <div class="col-md-6">
      <label class="form-label">Bar (€)</label>
      <input name="bar" id="bar" type="number" step="0.01" value="{{vals['bar']}}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <!-- Anzahl-Felder als Input-Gruppen -->
    <div class="col-md-4">
      <label class="form-label d-block">Bier</label>
      <div class="input-group">
        <span class="input-group-text">Anzahl</span>
        <input name="bier" id="bier" type="number" value="{{vals['bier']}}"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
    </div>
    <div class="col-md-4">
      <label class="form-label d-block">Alkoholfrei</label>
      <div class="input-group">
        <span class="input-group-text">Anzahl</span>
        <input name="alkoholfrei" id="alkoholfrei" type="number" value="{{vals['alkoholfrei']}}"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
    </div>
    <div class="col-md-4">
      <label class="form-label d-block">Hendl</label>
      <div class="input-group">
        <span class="input-group-text">Anzahl</span>
        <input name="hendl" id="hendl" type="number" value="{{vals['hendl']}}"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
    </div>

    {% if wtag == 2 %}
    <div class="col-md-6">
      <label class="form-label">Steuer (€)</label>
      <input name="steuer" id="steuer" type="number" step="0.01" value="{{vals['steuer']}}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>
    {% endif %}

    <div class="col-md-6">
      <label class="form-label">Bar entnommen (€)</label>
      <input name="bar_entnommen" id="bar_entnommen" type="number" step="0.01" value="{{vals['bar_entnommen']}}"
             class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <div class="col-md-6">
      <label class="form-label">Gesamt (€)</label>
      <input id="gesamt" class="form-control calc-field" readonly value="{{ '%.2f' % vals['gesamt'] }}">
    </div>
    <div class="col-md-6">
      <label class="form-label">Tagessumme (€)</label>
      <input id="tagessumme" class="form-control calc-field" readonly value="{{ '%.2f' % vals['tagessumme'] }}">
    </div>
  </div>

  <div class="mt-3">
    {% if im_edit and not vals['gespeichert'] %}
      <button class="btn btn-success">Speichern</button>
    {% else %}
      <div class="alert alert-secondary mt-2">Bearbeitung gesperrt – zum Ändern unten Passwort eingeben.</div>
    {% endif %}
  </div>
</form>

{% if vals['gespeichert'] %}
<div class="card p-3">
  <form method="post" class="row g-2">
    <input type="hidden" name="action" value="unlock">
    <div class="col-md-6"><input name="edit_pw" type="password" class="form-control" placeholder="Passwort"></div>
    <div class="col-md-6"><button class="btn btn-warning w-100">Editieren freischalten</button></div>
  </form>
</div>
{% endif %}

<a href="{{ url_for('login') }}" class="btn btn-home mt-4">Zur Startseite</a>

<script>
function berechne(){
  let preisB={{preis_bier}}, preisA={{preis_alk}}, preisH={{preis_hendl}};
  let bar=parseFloat(document.getElementById("bar")?.value)||0;
  let bier=parseInt(document.getElementById("bier")?.value)||0;
  let alk=parseInt(document.getElementById("alkoholfrei")?.value)||0;
  let h=parseInt(document.getElementById("hendl")?.value)||0;
  let barEnt=parseFloat(document.getElementById("bar_entnommen")?.value)||0;
  let ges=bar + bier*preisB + alk*preisA + h*preisH;
  let tag=ges - barEnt;
  if(document.getElementById("gesamt"))document.getElementById("gesamt").value=ges.toFixed(2);
  if(document.getElementById("tagessumme"))document.getElementById("tagessumme").value=tag.toFixed(2);
