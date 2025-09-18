import os
import sqlite3
import shutil
import time
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file, flash
)
import openpyxl

# =============================================================================
# ENV / Konfiguration
# =============================================================================
def _env(key, default=None): return os.getenv(key, default)

def _env_float(key, default):
    try:
        return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception:
        return default

def _env_date(key, default_iso):
    return date.fromisoformat(os.getenv(key, default_iso))

def _parse_pw_map(default_names):
    """
    ENV: MITARBEITER_PASSWORDS="Florian:pw1,Jonas:pw2"
    Für fehlende Namen -> <name>123 (klein).
    """
    raw = os.getenv("MITARBEITER_PASSWORDS", "")
    mp = {}
    for chunk in raw.split(","):
        if ":" in chunk:
            n, pw = chunk.split(":", 1)
            n, pw = n.strip(), pw.strip()
            if n and pw:
                mp[n] = pw
    for n in default_names:
        mp.setdefault(n, f"{n.lower()}123")
    return mp

SECRET_KEY  = _env("SECRET_KEY", "change-me")
ADMIN_PASS  = _env("ADMIN_PASSWORD", "Ramona")
DB_PATH     = _env("DATABASE_PATH", "verkauf.db")

PREIS_BIER  = _env_float("PREIS_BIER", 14.01)
PREIS_ALK   = _env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL = _env_float("PREIS_HENDL", 22.30)

# feste Mitarbeiter-Reihenfolge
MITARBEITER = [m.strip() for m in os.getenv(
    "MITARBEITER", "Florian,Jonas,Julia,Regina,Schorsch,Toni"
).split(",") if m.strip()]
MITARBEITER_PASSW = _parse_pw_map(MITARBEITER)

# Geschäftslogik-Zeiträume
DATA_START  = _env_date("DATA_START", "2025-09-20")  # erlaubte Tage
DATA_END    = _env_date("DATA_END",   "2025-10-05")
EDIT_START  = _env_date("EDIT_WINDOW_START", "2025-09-18")  # Bearbeitungsfenster
EDIT_END    = _env_date("EDIT_WINDOW_END",   "2025-10-07")

# Demo: immer editierbar
DEMO_MODE   = os.getenv("DEMO_MODE", "0") == "1"

COUNTDOWN_DEADLINE = datetime(2025, 10, 5, 23, 0, 0)

# =============================================================================
# App & DB
# =============================================================================
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB Upload-Limit

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

# =============================================================================
# Health
# =============================================================================
@app.route("/healthz")
def healthz():
    return {"status": "ok", "demo": DEMO_MODE, "time": datetime.utcnow().isoformat()}

# =============================================================================
# Login + Countdown
# =============================================================================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name") or ""
        admin_pw = request.form.get("admin_pw") or ""
        if admin_pw and admin_pw == ADMIN_PASS:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear()
            session["name"] = name
            session["admin"] = False
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
:root{--blue:#0a2a66;}
body{background:var(--blue);color:#fff;}
.card-login{background:#fff;color:#111;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25);}
.countdown{font-size:1.5rem;font-weight:700;}
.note-small{font-size:.9rem;color:#cfd8ff;}
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
<div class="container" style="max-width:980px;">
  <div class="text-center mb-4">
    <h1 class="display-6">Willkommen zur Wiesn-Abrechnung</h1>
    <div id="countdown" class="countdown mt-2">–</div>
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
        <div class="text-center my-2 text-white-50">oder</div>
        <div class="mb-3">
          <label class="form-label">Admin Passwort</label>
          <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
        </div>
        <button class="btn btn-primary w-100">Einloggen</button>
      </form>
      <p class="note-small text-center mt-3 mb-0">Bearbeitung möglich zwischen 18.09. und 07.10.</p>
      {% if demo_mode %}
        <div class="alert alert-info small mt-3 mb-0">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.</div>
      {% endif %}
    </div>
  </div>
</div>
<script>
const deadline = new Date("2025-10-05T23:00:00");
function updateCountdown(){
  const diff = Math.max(0,(deadline - new Date())/1000);
  const d = Math.floor(diff/86400), h = Math.floor((diff%86400)/3600), m = Math.floor((diff%3600)/60);
  document.getElementById('countdown').textContent = `${d} Tage ${h} Std ${m} Min verbleiben`;
}
updateCountdown(); setInterval(updateCountdown, 60000);
</script>
</body>
</html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# =============================================================================
# Eingabe – Zahleneingabe, Passwort-Entsperren, Summe-Start-Logik
# =============================================================================
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    user = session.get("name", "ADMIN")
    d_obj = date.fromisoformat(datum)
    wtag = d_obj.weekday()  # 2 = Mittwoch
    erster_tag = (d_obj == DATA_START)

    # Bearbeitbarkeit
    if DEMO_MODE:
        im_edit = True
    else:
        im_edit = (EDIT_START <= date.today() <= EDIT_END) and (DATA_START <= d_obj <= DATA_END)

    db = get_db()
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?", (datum, user)).fetchone()
    action = request.form.get("action")

    # Entsperren
    if request.method == "POST" and action == "unlock":
        entered = (request.form.get("edit_pw") or "").strip()
        ok = (entered == ADMIN_PASS) if session.get("admin") else (entered == MITARBEITER_PASSW.get(user))
        if ok and row:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
            flash("Eintrag entsperrt 🔓")
        else:
            flash("Falsches Passwort ❌")
        return redirect(url_for("eingabe", datum=datum))

    # Speichern
    if request.method == "POST" and action == "save" and im_edit and (DEMO_MODE or (not row or row["gespeichert"] == 0)):
        # Summe Start: nur am 20.09 oder wenn bestehender Eintrag entsperrt
        if erster_tag or (row and row["gespeichert"] == 0):
            summe_start = float(request.form.get("summe_start") or 0)
        else:
            v = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                           ((d_obj - timedelta(days=1)).isoformat(), user)).fetchone()
            summe_start = float(v["tagessumme"] if v else 0.0)

        bar   = float(request.form.get("bar") or 0)
        bier  = int(request.form.get("bier") or 0)
        alk   = int(request.form.get("alkoholfrei") or 0)
        hendl = int(request.form.get("hendl") or 0)
        steuer = float(request.form.get("steuer") or 0) if wtag == 2 else 0.0

        gesamt = bar + bier*PREIS_BIER + alk*PREIS_ALK + hendl*PREIS_HENDL
        bar_entn = float(request.form.get("bar_entnommen") or 0)
        tagessumme = gesamt - bar_entn  # Steuer NICHT in Tagesansicht abziehen

        if row:
            db.execute("""UPDATE eintraege SET
                summe_start=?, bar=?, bier=?, alkoholfrei=?, hendl=?, steuer=?,
                gesamt=?, bar_entnommen=?, tagessumme=?, gespeichert=1
                WHERE id=?""",
                (summe_start, bar, bier, alk, hendl, steuer,
                 gesamt, bar_entn, tagessumme, row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
                (datum, mitarbeiter, summe_start, bar, bier, alkoholfrei, hendl,
                 steuer, gesamt, bar_entnommen, tagessumme, gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                (datum, user, summe_start, bar, bier, alk, hendl,
                 steuer, gesamt, bar_entn, tagessumme))
        db.commit()
        flash("Gespeichert ✅")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige-Werte
    if row:
        vals = dict(row)
    else:
        # Für neue Einträge: Summe Start vom Vortag (außer erster Tag)
        if erster_tag:
            summe_start = 0.0
        else:
            v = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                ((d_obj - timedelta(days=1)).isoformat(), user)
            ).fetchone()
            summe_start = float(v["tagessumme"] if v else 0.0)
        vals = dict(
            summe_start=summe_start, bar=0, bier=0, alkoholfrei=0, hendl=0, steuer=0.0,
            gesamt=0.0, bar_entnommen=0.0, tagessumme=0.0, gespeichert=0
        )

    is_new = row is None  # für leere Felder
    may_edit_summe = erster_tag or (row and row["gespeichert"] == 0)

    vortag_link = (d_obj - timedelta(days=1)).isoformat()
    folgetag_link = (d_obj + timedelta(days=1)).isoformat()

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

<!-- Kalender oben zentriert -->
<div class="d-flex flex-column align-items-center gap-2 mb-3">
  <input type="date" id="datumsauswahl" class="form-control text-center" style="max-width:220px"
         value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
  <div>
    <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary me-2">← Vortag</a>
    <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary">Folgetag →</a>
  </div>
</div>

<form method="post" oninput="berechne()" class="card app-card p-3 mx-auto" style="max-width:900px;">
  <input type="hidden" name="action" value="save">
  <div class="row g-3">

    <div class="col-12 col-md-6">
      <label class="form-label">Summe Start (€)</label>
      <input name="summe_start" type="number" inputmode="decimal" step="0.01"
             value="{{ '' if is_new else vals['summe_start'] }}"
             class="form-control {% if may_edit_summe %}editable{% else %}readonly{% endif %}"
             {% if not may_edit_summe %}readonly{% endif %}>
    </div>

    <div class="col-12 col-md-6">
      <label class="form-label">Bar (€)</label>
      <input name="bar" id="bar" type="number" inputmode="decimal" step="0.01"
             value="{{ '' if is_new else vals['bar'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <div class="col-12 col-md-4">
      <label class="form-label">Bier (Anzahl)</label>
      <input name="bier" id="bier" type="number" inputmode="numeric" step="1"
             value="{{ '' if is_new else vals['bier'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <div class="col-12 col-md-4">
      <label class="form-label">Alkoholfrei (Anzahl)</label>
      <input name="alkoholfrei" id="alkoholfrei" type="number" inputmode="numeric" step="1"
             value="{{ '' if is_new else vals['alkoholfrei'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <div class="col-12 col-md-4">
      <label class="form-label">Hendl (Anzahl)</label>
      <input name="hendl" id="hendl" type="number" inputmode="numeric" step="1"
             value="{{ '' if is_new else vals['hendl'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    {% if wtag == 2 %}
    <div class="col-12 col-md-6">
      <label class="form-label">Steuer (€) – nur Mittwoch</label>
      <input name="steuer" id="steuer" type="number" inputmode="decimal" step="0.01"
             value="{{ '' if is_new else vals['steuer'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>
    {% endif %}

    <div class="col-12 col-md-6">
      <label class="form-label">Bar entnommen (€)</label>
      <input name="bar_entnommen" id="bar_entnommen" type="number" inputmode="decimal" step="0.01"
             value="{{ '' if is_new else vals['bar_entnommen'] }}"
             class="form-control {% if (im_edit and not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
             {% if not (im_edit and not vals['gespeichert']) %}readonly{% endif %}>
    </div>

    <div class="col-12 col-md-6">
      <label class="form-label">Gesamt (€)</label>
      <input id="gesamt" class="form-control calc-field" type="number" inputmode="decimal" step="0.01" readonly
             value="{{ '' if is_new else ('%.2f' % vals['gesamt']) }}">
    </div>

    <div class="col-12 col-md-6">
      <label class="form-label">Tagessumme (€)</label>
      <input id="tagessumme" class="form-control calc-field" type="number" inputmode="decimal" step="0.01" readonly
             value="{{ '' if is_new else ('%.2f' % vals['tagessumme']) }}">
    </div>

    <div class="col-12 text-center">
      {% if im_edit and not vals['gespeichert'] %}
        <button class="btn btn-success mt-2">Speichern</button>
      {% else %}
        <div class="alert alert-secondary mt-2 mb-0 d-inline-block">
          Bearbeitung gesperrt. Zum Ändern bitte unten entsperren.
        </div>
      {% endif %}
    </div>
  </div>
  <input type="hidden" name="action" value="save">
</form>

<div class="mt-3 text-center">
  <a class="btn btn-primary" href="{{ url_for('login') }}">Zur Startseite</a>
</div>

{% if vals['gespeichert'] %}
<div class="card app-card mt-3 p-3 mx-auto" style="max-width:900px;">
  <h5 class="text-center">Eintrag bearbeiten (entsperren)</h5>
  <form method="post" class="row g-2 justify-content-center">
    <input type="hidden" name="action" value="unlock">
    <div class="col-12 col-md-6">
      <input type="password" name="edit_pw" class="form-control"
             placeholder="Passwort" required autocomplete="current-password">
    </div>
    <div class="col-12 col-md-3">
      <button class="btn btn-warning w-100">Editieren freischalten</button>
    </div>
  </form>
</div>
{% endif %}

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
  const g=document.getElementById("gesamt"), t=document.getElementById("tagessumme");
  if(g) g.value = isFinite(ges) ? ges.toFixed(2) : "";
  if(t) t.value = isFinite(tag) ? tag.toFixed(2) : "";
}
</script>

</body>
</html>
    """,
        datum=datum,
        name=user,
        wtag=wtag,
        vals=vals,
        im_edit=im_edit,
        preis_bier=PREIS_BIER, preis_alk=PREIS_ALK, preis_hendl=PREIS_HENDL,
        is_new=is_new,
        may_edit_summe=may_edit_summe,
        vortag_link=vortag_link,
        folgetag_link=folgetag_link
    )

# =============================================================================
# Admin-Ansicht (Steuer nur in Gesamtsumme abziehen)
# =============================================================================
@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute("""
        SELECT
          datum,
          SUM(gesamt) AS tag_summe,
          SUM(steuer) AS steuer_summe
        FROM eintraege
        WHERE gesamt IS NOT NULL
        GROUP BY datum
        ORDER BY datum
    """).fetchall()

    rows_with = []
    prev = None
    for r in rows:
        brutto = float(r["tag_summe"] or 0.0)
        steuer = float(r["steuer_summe"] or 0.0)
        diff = None if prev is None else (brutto - prev)
        pro_person = None if diff is None else (diff / 6.0)
        rows_with.append({
            "datum": r["datum"],
            "tag_summe": brutto,
            "steuer_summe": steuer,
            "diff": diff,
            "pro_person": pro_person
        })
        prev = brutto

    gesamt_brutto = sum(r["tag_summe"] for r in rows_with)
    gesamt_steuer = sum(r["steuer_summe"] for r in rows_with)
    gesamt_nach_steuer = gesamt_brutto - gesamt_steuer
    gesamt_brutto_pp = gesamt_brutto / 6.0 if rows_with else 0.0
    gesamt_nach_steuer_pp = gesamt_nach_steuer / 6.0 if rows_with else 0.0

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Admin</title>
<style>
body{background:#f6f7fb;}
.app-card{background:#fff;border:1px solid rgba(13,110,253,.08);box-shadow:0 10px 30px rgba(0,0,0,.05);border-radius:14px;}
</style>
</head>
<body class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3 class="mb-0">Gesamtsummen</h3>
    <div class="d-flex gap-2">
      <a href="{{ url_for('export_excel') }}" class="btn btn-primary">📥 Excel Export</a>
      <a href="{{ url_for('backup_db') }}" class="btn btn-secondary">📦 SQL Backup</a>
      <a href="{{ url_for('login') }}" class="btn btn-outline-secondary">Abmelden</a>
    </div>
  </div>

  <div class="card app-card">
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table table-hover mb-0">
          <thead class="table-light">
            <tr>
              <th>Datum</th>
              <th>Brutto (€)</th>
              <th>Differenz Vortag (€)</th>
              <th>Umsatz pro Person (€)</th>
              <th>Steuer je Tag (€)</th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
            <tr>
              <td>{{ r.datum }}</td>
              <td>{{ "%.2f"|format(r.tag_summe) }}</td>
              <td>{% if r.diff is not none %}{{ "%.2f"|format(r.diff) }}{% else %}-{% endif %}</td>
              <td>{% if r.pro_person is not none %}{{ "%.2f"|format(r.pro_person) }}{% else %}-{% endif %}</td>
              <td>{{ "%.2f"|format(r.steuer_summe) }}</td>
            </tr>
            {% endfor %}
          </tbody>
          <tfoot>
            <tr class="table-secondary">
              <th>GESAMT BRUTTO</th>
              <th>{{ "%.2f"|format(gesamt_brutto) }}</th>
              <th></th>
              <th>{{ "%.2f"|format(gesamt_brutto_pp) }}</th>
              <th>{{ "%.2f"|format(gesamt_steuer) }}</th>
            </tr>
            <tr class="table-dark">
              <th>GESAMT NACH STEUER</th>
              <th>{{ "%.2f"|format(gesamt_nach_steuer) }}</th>
              <th></th>
              <th>{{ "%.2f"|format(gesamt_nach_steuer_pp) }}</th>
              <th></th>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
    <div class="card-footer">
      <form action="{{ url_for('restore_db') }}" method="post" enctype="multipart/form-data" class="d-flex flex-wrap gap-2">
        <input type="file" name="file" accept=".sqlite,.db" class="form-control" style="max-width:420px" required>
        <button type="submit" class="btn btn-danger"
                onclick="return confirm('Achtung: Aktuelle Datenbank wird ersetzt. Fortfahren?')">🔁 Restore</button>
      </form>
    </div>
  </div>
</body>
</html>
    """,
        rows=rows_with,
        gesamt_brutto=gesamt_brutto,
        gesamt_steuer=gesamt_steuer,
        gesamt_nach_steuer=gesamt_nach_steuer,
        gesamt_brutto_pp=gesamt_brutto_pp,
        gesamt_nach_steuer_pp=gesamt_nach_steuer_pp
    )

# =============================================================================
# Excel-Export
# =============================================================================
@app.route("/export_excel")
def export_excel():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute("""
        SELECT datum, SUM(gesamt) AS tag_summe, SUM(steuer) AS steuer_summe
        FROM eintraege
        WHERE gesamt IS NOT NULL
        GROUP BY datum
        ORDER BY datum
    """).fetchall()

    data = []
    prev = None
    for r in rows:
        brutto = float(r["tag_summe"] or 0.0)
        steuer = float(r["steuer_summe"] or 0.0)
        diff = None if prev is None else (brutto - prev)
        pro_person = None if diff is None else (diff / 6.0)
        data.append((r["datum"], brutto, diff, pro_person, steuer))
        prev = brutto

    gesamt_brutto = sum(s for _, s, _, _, _ in data)
    gesamt_steuer = sum(st for _, _, _, _, st in data)
    gesamt_nach_steuer = gesamt_brutto - gesamt_steuer
    gesamt_brutto_pp = gesamt_brutto / 6.0 if data else 0.0
    gesamt_nach_steuer_pp = gesamt_nach_steuer / 6.0 if data else 0.0

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamtsummen"
    ws.append(["Datum", "Brutto (€)", "Differenz Vortag (€)", "Umsatz/Person (€)", "Steuer je Tag (€)"])
    for d, s, diff, pp, st in data:
        ws.append([d, s, "" if diff is None else diff, "" if pp is None else pp, st])
    ws.append([])
    ws.append(["GESAMT BRUTTO", gesamt_brutto, "", gesamt_brutto_pp, gesamt_steuer])
    ws.append(["GESAMT NACH STEUER", gesamt_nach_steuer, "", gesamt_nach_steuer_pp, ""])

    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(
        out,
        as_attachment=True,
        download_name=f"Wiesn25_Gesamt_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# =============================================================================
# Backup & Restore
# =============================================================================
@app.route("/backup_db")
def backup_db():
    if not session.get("admin"):
        return redirect(url_for("login"))
    if not os.path.exists(DB_PATH):
        return "Keine Datenbank gefunden.", 404
    return send_file(
        DB_PATH,
        as_attachment=True,
        download_name=f"Wiesn25_Backup_{date.today().isoformat()}.sqlite",
        mimetype="application/x-sqlite3"
    )

@app.route("/restore_db", methods=["POST"])
def restore_db():
    if not session.get("admin"):
        return redirect(url_for("login"))
    f = request.files.get("file")
    if not f or f.filename == "":
        return "Keine Datei.", 400
    tmp = f"/tmp/restore_{int(time.time())}.sqlite"
    f.save(tmp)
    # Basic validity check
    try:
        t = sqlite3.connect(tmp)
        t.execute("PRAGMA schema_version;")
        t.close()
    except Exception:
        try: os.remove(tmp)
        except Exception: pass
        return "Ungültige SQLite-Datei.", 400

    shutil.copy2(tmp, DB_PATH)
    os.remove(tmp)
    with app.app_context():
        init_db()
    return redirect(url_for("admin_view"))

# =============================================================================
# Start
# =============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
