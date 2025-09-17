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

# ------------------------------------------------------------------------------
# ENV / Konfiguration
# ------------------------------------------------------------------------------
def _get_env(key, default=None): return os.getenv(key, default)
def _get_env_float(key, default):
    try: return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception: return default
def _get_env_date(key, default_iso): return date.fromisoformat(os.getenv(key, default_iso))

def _parse_password_map(default_names):
    """
    ENV: MITARBEITER_PASSWORDS="Julia:pw1,Regina:pw2,Florian:pw3"
    Für fehlende Namen -> <name>123 (klein).
    """
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

MITARBEITER = [m.strip() for m in os.getenv(
    "MITARBEITER", "Julia,Regina,Florian,Schorsch,Toni,Jonas"
).split(",") if m.strip()]
MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

# Geschäftslogik-Zeiträume
DATA_START         = _get_env_date("DATA_START", "2025-09-20")  # Tage, die erfasst/angezeigt werden
DATA_END           = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START  = _get_env_date("EDIT_WINDOW_START", "2025-09-18")  # Zeitraum, in dem Bearbeitung grundsätzlich erlaubt ist
EDIT_WINDOW_END    = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")
DEMO_MODE          = os.getenv("DEMO_MODE", "0") == "1"         # Demo: immer editierbar

# Countdown-Ziel (Willkommen)
COUNTDOWN_DEADLINE = datetime(2025, 10, 5, 23, 0, 0)

# ------------------------------------------------------------------------------
# App
# ------------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB Upload-Limit (Restore)

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

with app.app_context():
    init_db()

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status": "ok", "demo": DEMO_MODE, "time": datetime.utcnow().isoformat()}

# ------------------------------------------------------------------------------
# Willkommen / Login (dunkelblau + Countdown)
# ------------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def login():
    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Willkommen</title>
<style>
  :root{ --blue:#0a2a66; --lighter:#133a8f; }
  body{ background:var(--blue); color:#fff; }
  .card-login{ background:#ffffff; color:#111; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.25); }
  .countdown{ font-size:1.5rem; font-weight:700; letter-spacing:.3px; }
  .heading{ font-weight:800; letter-spacing:.4px; }
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
  <div class="container" style="max-width:980px;">
    <div class="text-center mb-4">
      <h1 class="heading display-6">Willkommen zur Wiesn-Abrechnung</h1>
      <p class="opacity-75 mb-2">Bearbeitung möglich zwischen 18.09. und 07.10.</p>
      <div id="countdown" class="countdown">–</div>
    </div>

    <div class="card card-login mx-auto mt-4" style="max-width:520px;">
      <div class="card-body p-4">
        <h4 class="mb-3">Login</h4>
        {% with msgs = get_flashed_messages() %}
          {% if msgs %}
            <div class="alert alert-danger py-2">{{ msgs[0] }}</div>
          {% endif %}
        {% endwith %}
        <form method="post" action="{{ url_for('do_login') }}">
          <div class="mb-3">
            <label class="form-label">Mitarbeiter</label>
            <select name="name" class="form-select">
              <option value="">-- auswählen --</option>
              {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
            </select>
          </div>
          <div class="text-center my-2"><span class="text-white-50">oder</span></div>
          <div class="mb-3">
            <label class="form-label">Admin Passwort</label>
            <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
          </div>
          <button class="btn btn-primary w-100">Einloggen</button>
        </form>
        {% if demo_mode %}
          <div class="alert alert-info small mt-3 mb-0">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt (Steuer nur mittwochs sichtbar).</div>
        {% endif %}
      </div>
    </div>
  </div>

<script>
  // Ziel: 05.10.2025 23:00 (lokale Zeit)
  const deadline = new Date("2025-10-05T23:00:00");
  function updateCountdown(){
    const now = new Date();
    let diff = (deadline - now)/1000;
    if(diff < 0) diff = 0;
    const d = Math.floor(diff/86400);
    const h = Math.floor((diff%86400)/3600);
    const m = Math.floor((diff%3600)/60);
    document.getElementById('countdown').textContent =
      `${d} Tage ${h} Std ${m} Min verbleiben`;
  }
  updateCountdown();
  setInterval(updateCountdown, 60000);
</script>
</body>
</html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

@app.route("/", methods=["POST"])
def do_login():
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

# ------------------------------------------------------------------------------
# Eingabe (mit Entsperren, Summe Start Logik)
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    aktiver_user = session.get("name", "ADMIN")
    datum_obj = date.fromisoformat(datum)
    wtag = datum_obj.weekday()  # 2 == Mittwoch
    ist_erster_tag = (datum_obj == DATA_START)

    # Bearbeitung erlaubt?
    if DEMO_MODE:
        im_edit_zeitraum = True
    else:
        heute_ok = EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END
        tag_ok    = DATA_START <= datum_obj <= DATA_END
        im_edit_zeitraum = heute_ok and tag_ok

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, aktiver_user)
    ).fetchone()

    action = request.form.get("action")

    # --- Entsperren ---
    if request.method == "POST" and action == "unlock":
        if not row:
            flash("Kein Eintrag vorhanden.")
            return redirect(url_for("eingabe", datum=datum))

        entered = (request.form.get("edit_pw") or "").strip()
        ok = False
        if session.get("admin"):
            ok = (entered == ADMIN_PASSWORT)
        else:
            expected = MITARBEITER_PASSWOERTER.get(aktiver_user)
            ok = (entered and expected and entered == expected)

        if not ok:
            flash("Falsches Passwort ❌")
        else:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
            flash("Eintrag entsperrt 🔓")

        return redirect(url_for("eingabe", datum=datum))

    # --- Speichern ---
    if request.method == "POST" and action == "save" and im_edit_zeitraum and (DEMO_MODE or (not row or row["gespeichert"] == 0)):
        allow_edit = (request.form.get("allow_edit_summe_start") == "1")

        if ist_erster_tag or allow_edit:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            # vom Vortag übernehmen
            vortag = datum_obj - timedelta(days=1)
            v = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(v["tagessumme"] if v else 0.0)

        bar = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
        hendl = int(request.form.get("hendl", 0) or 0)
        steuer = float(request.form.get("steuer", 0) or 0) if wtag == 2 else 0.0

        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL)
        bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)
        tagessumme = gesamt - bar_entnommen  # Steuer NICHT in Tagesansicht abziehen

        if row:
            db.execute("""
                UPDATE eintraege
                SET summe_start=?, bar=?, bier=?, alkoholfrei=?, hendl=?, steuer=?,
                    gesamt=?, bar_entnommen=?, tagessumme=?, gespeichert=1
                WHERE id=?
            """, (summe_start, bar, bier, alkoholfrei, hendl, steuer,
                  gesamt, bar_entnommen, tagessumme, row["id"]))
        else:
            db.execute("""
                INSERT INTO eintraege
                (datum, mitarbeiter, summe_start, bar, bier, alkoholfrei, hendl, steuer,
                 gesamt, bar_entnommen, tagessumme, gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
            """, (datum, aktiver_user, summe_start, bar, bier, alkoholfrei, hendl, steuer,
                  gesamt, bar_entnommen, tagessumme))
        db.commit()
        flash("Gespeichert ✅")
        return redirect(url_for("eingabe", datum=datum))

    # --- Anzeige-Werte ermitteln ---
    if row:
        vals = dict(row)
    else:
        if ist_erster_tag:
            summe_start = 0.0
        else:
            vortag = datum_obj - timedelta(days=1)
            v = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(v["tagessumme"] if v else 0.0)
        vals = dict(
            summe_start=summe_start, bar=0, bier=0, alkoholfrei=0, hendl=0, steuer=0.0,
            gesamt=0.0, bar_entnommen=0.0, tagessumme=0.0, gespeichert=0
        )

    vortag_link   = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
<style>
  body{background:#f6f7fb;}
  .calc-field{background:#e9ecef;}
  .editable{background:#fff7d6;}
  .readonly{background:#e9ecef;}
  .app-card{ background:#fff; border:1px solid rgba(13,110,253,.08); box-shadow:0 10px 30px rgba(0,0,0,.05); border-radius:14px; }
  .btn-rounded{ border-radius:999px; }
</style>
</head>
<body class="container py-4">

<h3 class="mb-3">Eingabe – {{name}} <small class="text-muted">({{datum}})</small></h3>

{% with msgs = get_flashed_messages() %}
  {% if msgs %}
    <div class="alert alert-info py-2">{{ msgs[0] }}</div>
  {% endif %}
{% endwith %}

<div class="mb-3 d-flex flex-wrap gap-2">
  <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary btn-rounded">← Vortag</a>
  <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary btn-rounded">Folgetag →</a>
  <div class="ms-auto"></div>
  <input type="date" id="datumsauswahl" class="form-control" style="max-width: 240px"
         value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
</div>

<form method="post" oninput="berechne()" class="card app-card">
  <input type="hidden" name="action" value="save">
  <input type="hidden" name="allow_edit_summe_start"
         value="{{ 1 if (im_edit_zeitraum and (datum==data_start or not vals['gespeichert'])) else 0 }}">
  <div class="card-body p-4">
    <div class="row g-3">
      <div class="col-12 col-md-6">
        <label class="form-label">Summe Start (€)</label>
        <input name="summe_start" type="number" step="0.01" value="{{vals['summe_start']}}"
               class="form-control {% if im_edit_zeitraum and (datum==data_start or not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and (datum==data_start or not vals['gespeichert'])) %}readonly{% endif %}>
      </div>

      <div class="col-12 col-md-6">
        <label class="form-label">Bar (€)</label>
        <input name="bar" id="bar" type="number" step="0.01" value="{{vals['bar']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>

      <div class="col-12 col-md-4">
        <label class="form-label">Bier (Anzahl)</label>
        <input name="bier" id="bier" type="number" value="{{vals['bier']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>

      <div class="col-12 col-md-4">
        <label class="form-label">Alkoholfrei (Anzahl)</label>
        <input name="alkoholfrei" id="alkoholfrei" type="number" value="{{vals['alkoholfrei']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>

      <div class="col-12 col-md-4">
        <label class="form-label">Hendl (Anzahl)</label>
        <input name="hendl" id="hendl" type="number" value="{{vals['hendl']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>

      {% if wtag == 2 %}
      <div class="col-12 col-md-6">
        <label class="form-label">Steuer (€) – nur mittwochs</label>
        <input name="steuer" id="steuer" type="number" step="0.01" value="{{vals['steuer']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>
      {% endif %}

      <div class="col-12 col-md-6">
        <label class="form-label">Bar entnommen (€)</label>
        <input name="bar_entnommen" id="bar_entnommen" type="number" step="0.01" value="{{vals['bar_entnommen']}}"
               class="form-control {% if im_edit_zeitraum and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit_zeitraum and not vals['gespeichert']) %}readonly{% endif %}>
      </div>

      <div class="col-12 col-md-6">
        <label class="form-label">Gesamt (€)</label>
        <input id="gesamt" class="form-control calc-field" readonly value="{{ '%.2f' % vals['gesamt'] }}">
      </div>

      <div class="col-12 col-md-6">
        <label class="form-label">Tagessumme (€)</label>
        <input id="tagessumme" class="form-control calc-field" readonly value="{{ '%.2f' % vals['tagessumme'] }}">
      </div>

      <div class="col-12">
        {% if im_edit_zeitraum and not vals['gespeichert'] %}
          <button class="btn btn-success btn-rounded px-4 mt-2">Speichern</button>
        {% else %}
          <div class="alert alert-secondary mt-2 mb-0">Bearbeitung gesperrt. Zum Ändern bitte unten entsperren.</div>
        {% endif %}
      </div>
    </div>
  </div>
</form>

<div class="mt-3">
  <a class="btn btn-outline-secondary btn-rounded" href="{{ url_for('login') }}">Zur Startseite</a>
</div>

{% if vals['gespeichert'] %}
<div class="card app-card mt-3">
  <div class="card-body p-4">
    <div class="card-header px-0 pb-3">Eintrag bearbeiten (entsperren)</div>
    <form method="post" class="row g-2">
      <input type="hidden" name="action" value="unlock">
      <div class="col-12 col-md-6">
        <input type="password" name="edit_pw" class="form-control" placeholder="Passwort" autocomplete="current-password" required>
      </div>
      <div class="col-12 col-md-6">
        <button class="btn btn-warning w-100 btn-rounded">Editieren freischalten</button>
      </div>
    </form>
  </div>
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
  if(g) g.value = ges.toFixed(2);
  if(t) t.value = tag.toFixed(2);
}
window.addEventListener('load', berechne);
</script>
</body>
</html>
    """,
        datum=datum, name=aktiver_user, wtag=wtag,
        vals=vals, im_edit_zeitraum=im_edit_zeitraum,
        data_start=DATA_START.isoformat(),
        preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFREI, preis_hendl=PREIS_HENDL,
        vortag_link=vortag_link, folgetag_link=folgetag_link
    )

# ------------------------------------------------------------------------------
# Admin-Ansicht (Summen + Steuer nur am Ende abziehen)
# ------------------------------------------------------------------------------
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
        HAVING SUM(gesamt) > 0
        ORDER BY datum
    """).fetchall()

    rows_with = []
    prev_sum = None
    for r in rows:
        brutto = float(r["tag_summe"] or 0.0)
        diff = None if prev_sum is None else (brutto - prev_sum)
        pro_person = None if diff is None else (diff / 6.0)
        rows_with.append({
            "datum": r["datum"],
            "tag_summe": brutto,
            "steuer_summe": float(r["steuer_summe"] or 0.0),
            "diff": diff,
            "pro_person": pro_person
        })
        prev_sum = brutto

    gesamt_brutto = sum(r["tag_summe"] for r in rows_with)
    gesamt_steuer = sum(r["steuer_summe"] for r in rows_with)
    gesamt_nach_steuer = gesamt_brutto - gesamt_steuer
    gesamt_brutto_pro_person = gesamt_brutto / 6.0 if rows_with else 0.0
    gesamt_nach_steuer_pro_person = gesamt_nach_steuer / 6.0 if rows_with else 0.0

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Admin</title>
<style>
  body{background:#f6f7fb;}
  .app-card{ background:#fff; border:1px solid rgba(13,110,253,.08); box-shadow:0 10px 30px rgba(0,0,0,.05); border-radius:14px; }
  .btn-rounded{ border-radius:999px; }
</style>
</head>
<body class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3 class="mb-0">Gesamtsummen pro Tag</h3>
    <a href="{{ url_for('login') }}" class="btn btn-outline-secondary btn-rounded">Abmelden</a>
  </div>

  <div class="card app-card">
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table table-hover align-middle mb-0">
          <thead class="table-light">
            <tr>
              <th>Datum</th>
              <th>Gesamtsumme brutto (€)</th>
              <th>Differenz Vortag (€)</th>
              <th>Umsatz pro Person (€)</th>
              <th>Steuer je Tag (€)</th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td class="fw-semibold">{{ r.datum }}</td>
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
              <th>{{ "%.2f"|format(gesamt_brutto_pro_person) }}</th>
              <th>{{ "%.2f"|format(gesamt_steuer) }}</th>
            </tr>
            <tr class="table-dark">
              <th>GESAMT NACH STEUER</th>
              <th>{{ "%.2f"|format(gesamt_nach_steuer) }}</th>
              <th></th>
              <th>{{ "%.2f"|format(gesamt_nach_steuer_pro_person) }}</th>
              <th></th>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
    <div class="card-footer bg-white d-flex flex-wrap gap-2 p-3">
      <form action="{{ url_for('export_excel') }}" method="get" class="d-inline">
        <button type="submit" class="btn btn-primary btn-rounded">📥 Excel Export</button>
      </form>
      <form action="{{ url_for('backup_db') }}" method="get" class="d-inline">
        <button type="submit" class="btn btn-secondary btn-rounded">📦 SQL Backup</button>
      </form>
      <form action="{{ url_for('restore_db') }}" method="post" enctype="multipart/form-data" class="d-inline">
        <div class="input-group" style="max-width:520px;">
          <input type="file" name="file" accept=".sqlite,.db" class="form-control" required>
          <button type="submit" class="btn btn-danger btn-rounded"
                  onclick="return confirm('Achtung: Aktuelle Datenbank wird ersetzt. Fortfahren?')">
            🔁 Restore
          </button>
        </div>
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
        gesamt_brutto_pro_person=gesamt_brutto_pro_person,
        gesamt_nach_steuer_pro_person=gesamt_nach_steuer_pro_person
    )

# ------------------------------------------------------------------------------
# Excel-Export (inkl. Footer & Pro-Person)
# ------------------------------------------------------------------------------
@app.route("/export_excel")
def export_excel():
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
        HAVING SUM(gesamt) > 0
        ORDER BY datum
    """).fetchall()

    data = []
    prev_sum = None
    for r in rows:
        brutto = float(r["tag_summe"] or 0)
        steuer_summe = float(r["steuer_summe"] or 0)
        diff = None if prev_sum is None else (brutto - prev_sum)
        pro_person = None if diff is None else (diff / 6.0)
        data.append((r["datum"], brutto, diff, pro_person, steuer_summe))
        prev_sum = brutto

    gesamt_brutto = sum(s for _, s, _, _, _ in data)
    gesamt_steuer = sum(st for _, _, _, _, st in data)
    gesamt_nach_steuer = gesamt_brutto - gesamt_steuer
    gesamt_brutto_pro_person = gesamt_brutto / 6.0 if data else 0.0
    gesamt_nach_steuer_pro_person = gesamt_nach_steuer / 6.0 if data else 0.0

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamtsummen"
    ws.append(["Datum", "Gesamtsumme brutto (€)", "Differenz Vortag (€)", "Umsatz pro Person (€)", "Steuer je Tag (€)"])
    for d, s, diff, pro_person, steuer_summe in data:
        ws.append([
            d,
            s,
            "" if diff is None else diff,
            "" if pro_person is None else pro_person,
            steuer_summe
        ])
    ws.append([])
    ws.append(["GESAMT BRUTTO", gesamt_brutto, "", gesamt_brutto_pro_person, gesamt_steuer])
    ws.append(["GESAMT NACH STEUER", gesamt_nach_steuer, "", gesamt_nach_steuer_pro_person, ""])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"Wiesn25_Gesamt_{date.today().isoformat()}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ------------------------------------------------------------------------------
# Backup & Restore
# ------------------------------------------------------------------------------
@app.route("/backup_db")
def backup_db():
    if not session.get("admin"):
        return redirect(url_for("login"))
    if not os.path.exists(DATABASE_PATH):
        return "Keine Datenbank gefunden.", 404
    return send_file(
        DATABASE_PATH,
        as_attachment=True,
        download_name=f"Wiesn25_Backup_{date.today().isoformat()}.sqlite",
        mimetype="application/x-sqlite3"
    )

@app.route("/restore_db", methods=["POST"])
def restore_db():
    if not session.get("admin"):
        return redirect(url_for("login"))

    file = request.files.get("file")
    if not file or file.filename == "":
        return "Keine Datei hochgeladen.", 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in {"sqlite", "db"}:
        return "Ungültiges Dateiformat. Erlaubt sind .sqlite oder .db", 400

    tmp_path = f"/tmp/restore_{int(time.time())}.sqlite"
    file.save(tmp_path)

    # Gültigkeit prüfen
    try:
        test = sqlite3.connect(tmp_path)
        test.execute("PRAGMA schema_version;")
        test.close()
    except Exception:
        try: os.remove(tmp_path)
        except Exception: pass
        return "Die hochgeladene Datei ist keine gültige SQLite-Datenbank.", 400

    # aktuelle DB sichern
    if os.path.exists(DATABASE_PATH):
        backup_path = f"{DATABASE_PATH}.bak_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(DATABASE_PATH, backup_path)

    try: close_connection(None)
    except Exception: pass

    shutil.copy2(tmp_path, DATABASE_PATH)
    os.remove(tmp_path)

    with app.app_context():
        init_db()

    return redirect(url_for("admin_view"))

# ------------------------------------------------------------------------------
# Local run
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
