import os
import sqlite3
import shutil
import time
from pathlib import Path
from datetime import date, timedelta, datetime
from io import BytesIO

from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file
)
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
    raw = os.getenv(key, default_iso)
    return date.fromisoformat(raw)


def _parse_password_map(default_names):
    """
    Erwartet ENV: MITARBEITER_PASSWORDS="Julia:pw1,Regina:pw2,Florian:pw3"
    Für nicht gesetzte Namen fällt es auf <name>123 zurück (z.B. julia123).
    """
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


# ------------------------------------------------------------------------------
# Konfiguration (per ENV)
# ------------------------------------------------------------------------------
SECRET_KEY = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORT = _get_env("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI = _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL = _get_env_float("PREIS_HENDL", 22.30)

MITARBEITER = [
    m.strip() for m in os.getenv(
        "MITARBEITER", "Julia,Regina,Florian,Schorsch,Toni,Jonas"
    ).split(",") if m.strip()
]
MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

# Eingabe-Zeitraum (Navigation/Erlaubnis)
DATA_START = _get_env_date("DATA_START", "2025-09-20")  # z. B. 20.09.
DATA_END   = _get_env_date("DATA_END",   "2025-10-05")

# Demo-Modus: Bearbeitung immer erlaubt (Steuerfeld trotzdem nur mittwochs sichtbar)
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"


# ------------------------------------------------------------------------------
# App
# ------------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB Upload-Limit (Restore)


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
# Healthcheck
# ------------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "demo": DEMO_MODE}


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
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <title>Login</title>
  <style>
    :root{
      --brand:#0d6efd; --brand-2:#6610f2; --bg:#f8fafc; --card:#fff;
    }
    body{ background:var(--bg); }
    .navbar-blur{ backdrop-filter:saturate(180%) blur(10px); background:rgba(255,255,255,.85)!important; border-bottom:1px solid rgba(0,0,0,.06); }
    .brand-gradient{ background:linear-gradient(135deg,var(--brand),var(--brand-2)); color:#fff; }
    .app-card{ background:var(--card); border:1px solid rgba(13,110,253,.08); box-shadow:0 10px 30px rgba(0,0,0,.05); border-radius:14px; }
    .btn-rounded{ border-radius:999px; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-blur fixed-top">
  <div class="container"><a class="navbar-brand fw-bold" href="#">Wiesn25</a></div>
</nav>
<div style="height:56px"></div>

<header class="brand-gradient py-5">
  <div class="container">
    <h1 class="h2 mb-1">Willkommen</h1>
    <p class="mb-0 opacity-75">Bitte als Mitarbeiter einloggen oder als Admin anmelden.</p>
  </div>
</header>

<main class="container py-5">
  <div class="row justify-content-center">
    <div class="col-12 col-md-7 col-lg-5">
      <div class="card app-card">
        <div class="card-body p-4">
          <div class="card-header px-0 pb-3">Login</div>
          <form method="post" class="mt-2">
            <div class="mb-3">
              <label class="form-label">Mitarbeiter</label>
              <select name="name" class="form-select form-select-lg">
                <option value="">– auswählen –</option>
                {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
              </select>
            </div>
            <div class="text-center my-3"><span class="text-muted">oder</span></div>
            <div class="mb-3">
              <label class="form-label">Admin Passwort</label>
              <input type="password" class="form-control form-control-lg" name="admin_pw" autocomplete="current-password">
            </div>
            <button type="submit" class="btn btn-primary btn-lg w-100 btn-rounded">Einloggen</button>
          </form>
          {% if demo_mode %}
            <div class="alert alert-info mt-3 mb-0 small">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt (Steuer nur mittwochs sichtbar).</div>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
</main>
</body>
</html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)


# ------------------------------------------------------------------------------
# Eingabe (mit Entsperren) – bleibt nach Speichern auf Datumsseite
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()  # 0=Mo ... 2=Mi ... 6=So
    ist_erster_tag = (datum_obj == DATA_START)

    # Bearbeitungslogik
    if DEMO_MODE:
        im_edit_zeitraum = True
    else:
        im_edit_zeitraum = (DATA_START <= datum_obj <= DATA_END)

    db = get_db()
    aktiver_user = session.get("name", "ADMIN")
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, aktiver_user)
    ).fetchone()

    action = request.form.get("action")

    # --- Entsperren (Editieren) ---
    if request.method == "POST" and action == "unlock":
        if not row:
            session["unlock_error"] = "Kein Eintrag zum Freischalten vorhanden."
            return redirect(url_for("eingabe", datum=datum))

        entered = (request.form.get("edit_pw") or "").strip()
        ok = False
        if session.get("admin"):
            ok = (entered == ADMIN_PASSWORT)
        else:
            expected = MITARBEITER_PASSWOERTER.get(aktiver_user)
            ok = (entered and expected and entered == expected)

        if not ok:
            session["unlock_error"] = "Falsches Passwort – Freischalten abgebrochen."
        else:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit()
            session.pop("unlock_error", None)

        return redirect(url_for("eingabe", datum=datum))

    # --- Speichern (Summe Start: nur nach Entsperren manuell – Ausnahme DATA_START) ---
    if request.method == "POST" and action == "save" and im_edit_zeitraum and (DEMO_MODE or (not row or row["gespeichert"] == 0)):
        allow_edit = (request.form.get("allow_edit_summe_start") == "1")

        if ist_erster_tag or allow_edit:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            if datum_obj == DATA_START:
                summe_start = 0.0
            else:
                vortag = datum_obj - timedelta(days=1)
                vortag_row = db.execute(
                    "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                    (vortag.isoformat(), aktiver_user)
                ).fetchone()
                summe_start = float(vortag_row["tagessumme"] if vortag_row else 0.0)

        bar = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
        hendl = int(request.form.get("hendl", 0) or 0)
        # Steuer nur mittwochs, wird NICHT in Tagesansicht abgezogen
        steuer = float(request.form.get("steuer", 0) or 0) if wochentag == 2 else 0.0
        bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)

        # Tagesberechnung OHNE Steuerabzug
        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL)
        tagessumme = gesamt - bar_entnommen

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

        return redirect(url_for("eingabe", datum=datum))

    # --- Anzeige-Daten vorbereiten ---
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
        gespeichert = row["gespeichert"] or 0
    else:
        if ist_erster_tag:
            summe_start = 0
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)
        bar = bier = alkoholfrei = hendl = steuer = bar_entnommen = 0
        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL)
        tagessumme = gesamt - bar_entnommen
        gespeichert = 0

    vortag_link = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()
    unlock_error = session.pop("unlock_error", "")

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <title>Eingabe</title>
  <style>
    :root{ --brand:#0d6efd; --brand-2:#6610f2; --bg:#f8fafc; --card:#fff; }
    body{ background:var(--bg); }
    .navbar-blur{ backdrop-filter:saturate(180%) blur(10px); background:rgba(255,255,255,.85)!important; border-bottom:1px solid rgba(0,0,0,.06); }
    .app-card{ background:var(--card); border:1px solid rgba(13,110,253,.08); box-shadow:0 10px 30px rgba(0,0,0,.05); border-radius:14px; }
    .calc-field { background: #f1f3f5; }
    .readonly   { background: #e9ecef; }
    .editable   { background: #fff7d6; }
    .badge-day{ background:#e7f1ff; color:#0a58ca; border:1px solid #cfe2ff; }
    .sticky-actions{ position:sticky; bottom:0; background:#fff; padding-top:.75rem; margin-top:1rem; border-top:1px solid rgba(0,0,0,.06); }
    .btn-rounded{ border-radius:999px; }
    .form-label{ font-weight:600; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-blur fixed-top">
  <div class="container">
    <a class="navbar-brand fw-bold" href="{{ url_for('login') }}">Wiesn25</a>
    <div class="ms-auto">
      <a href="{{ url_for('login') }}" class="btn btn-sm btn-outline-secondary btn-rounded">Zur Startseite</a>
    </div>
  </div>
</nav>
<div style="height:56px"></div>

<header class="py-4">
  <div class="container d-flex flex-wrap align-items-center gap-3">
    <div>
      <h1 class="h3 mb-1">Eingabe – <span class="text-primary">{{ name }}</span></h1>
      <div class="small text-muted">Datum: <span class="fw-semibold">{{ datum }}</span></div>
    </div>
    {% if wochentag == 2 %}
      <span class="badge badge-day ms-auto">Mittwoch (Steuerfeld sichtbar)</span>
    {% endif %}
  </div>
</header>

<main class="container pb-5">
  {% if unlock_error %}
    <div class="alert alert-danger">{{ unlock_error }}</div>
  {% endif %}

  <div class="mb-3 d-flex flex-wrap gap-2">
    <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary btn-rounded">← Vortag</a>
    <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary btn-rounded">Folgetag →</a>
    <div class="ms-auto"></div>
    <input type="date" id="datumsauswahl" class="form-control" style="max-width: 240px"
           value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
  </div>

  <form method="post" oninput="berechne()" class="card app-card">
    <input type="hidden" name="action" value="save">
    <!-- Summe Start manuell sichern, wenn entsperrt ODER erster Tag -->
    <input type="hidden" name="allow_edit_summe_start"
           value="{{ 1 if (im_edit_zeitraum and (ist_erster_tag or not gespeichert)) else 0 }}">
    <div class="card-body p-4">
      <div class="row g-3">
        <div class="col-12 col-md-6">
          <label class="form-label">Summe Start</label>
          <div class="input-group">
            <span class="input-group-text">€</span>
            <input type="number" step="0.01" name="summe_start" value="{{summe_start}}"
                   class="form-control {% if im_edit_zeitraum and (ist_erster_tag or not gespeichert) %}editable{% else %}readonly{% endif %}"
                   {% if not (im_edit_zeitraum and (ist_erster_tag or not gespeichert)) %}readonly{% endif %}>
          </div>
          {% if ist_erster_tag %}
            <div class="form-text">Am ersten Tag immer frei editierbar.</div>
          {% endif %}
        </div>

        <div class="col-12 col-md-6">
          <label class="form-label">Bar</label>
          <div class="input-group">
            <span class="input-group-text">€</span>
            <input type="number" step="0.01" id="bar" name="bar" value="{{bar}}" min="0"
                   class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          </div>
        </div>

        <div class="col-12 col-md-4">
          <label class="form-label">Bier</label>
          <input type="number" id="bier" name="bier" value="{{bier}}" min="0"
                 class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          <div class="form-text">Preis: {{ '%.2f' % preis_bier }} €</div>
        </div>

        <div class="col-12 col-md-4">
          <label class="form-label">Alkoholfrei</label>
          <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{alkoholfrei}}" min="0"
                 class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          <div class="form-text">Preis: {{ '%.2f' % preis_alk }} €</div>
        </div>

        <div class="col-12 col-md-4">
          <label class="form-label">Hendl</label>
          <input type="number" id="hendl" name="hendl" value="{{hendl}}" min="0"
                 class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          <div class="form-text">Preis: {{ '%.2f' % preis_hendl }} €</div>
        </div>

        {% if wochentag == 2 %}
        <div class="col-12 col-md-6">
          <label class="form-label">Steuer (nur mittwochs)</label>
          <div class="input-group">
            <span class="input-group-text">€</span>
            <input type="number" step="0.01" id="steuer" name="steuer" value="{{steuer}}" min="0"
                   class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          </div>
        </div>
        {% endif %}

        <div class="col-12 col-md-6">
          <label class="form-label">Bar entnommen</label>
          <div class="input-group">
            <span class="input-group-text">€</span>
            <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{bar_entnommen}}" min="0"
                   class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
          </div>
        </div>

        <div class="col-12 col-md-6">
          <label class="form-label">Gesamt</label>
          <input type="number" step="0.01" id="gesamt" readonly class="form-control calc-field"
                 value="{{ '%.2f' % gesamt }}">
        </div>

        <div class="col-12 col-md-6">
          <label class="form-label">Tagessumme</label>
          <input type="number" step="0.01" id="tagessumme" readonly class="form-control calc-field"
                 value="{{ '%.2f' % tagessumme }}">
        </div>
      </div>

      <div class="sticky-actions d-flex gap-2">
        {% if im_edit_zeitraum and not gespeichert %}
          <button type="submit" class="btn btn-success btn-rounded px-4">Speichern</button>
        {% else %}
          <div class="alert alert-secondary mb-0 flex-grow-1">Bearbeitung gesperrt. Zum Ändern bitte entsperren.</div>
        {% endif %}
        <a class="btn btn-outline-secondary btn-rounded" href="{{ url_for('login') }}">Zur Startseite</a>
      </div>
    </div>
  </form>

  {% if gespeichert %}
  <div class="card app-card mt-4">
    <div class="card-body p-4">
      <div class="card-header px-0 pb-3">Eintrag bearbeiten (entsperren)</div>
      <form method="post" class="row g-2">
        <input type="hidden" name="action" value="unlock">
        <div class="col-12 col-md-6">
          <input type="password" name="edit_pw" class="form-control" placeholder="Passwort" autocomplete="current-password" required>
        </div>
        <div class="col-12 col-md-6">
          <button type="submit" class="btn btn-warning w-100 btn-rounded">Editieren freischalten</button>
        </div>
      </form>
    </div>
  </div>
  {% endif %}
</main>

<script>
function berechne() {
  let preisBier = {{preis_bier}};
  let preisAlk  = {{preis_alk}};
  let preisHendl= {{preis_hendl}};

  let bar = parseFloat(document.getElementById("bar")?.value) || 0;
  let bier = parseInt(document.getElementById("bier")?.value) || 0;
  let alkoholfrei = parseInt(document.getElementById("alkoholfrei")?.value) || 0;
  let hendl = parseInt(document.getElementById("hendl")?.value) || 0;
  let barEntnommen = parseFloat(document.getElementById("bar_entnommen")?.value) || 0;

  let gesamt = bar + (bier * preisBier) + (alkoholfrei * preisAlk) + (hendl * preisHendl);
  let tagessumme = gesamt - barEntnommen;

  const g = document.getElementById("gesamt");
  const t = document.getElementById("tagessumme");
  if (g) g.value = gesamt.toFixed(2);
  if (t) t.value = tagessumme.toFixed(2);
}
window.addEventListener('load', berechne);
</script>
</body>
</html>
    """,
        datum=datum,
        name=aktiver_user,
        ist_erster_tag=ist_erster_tag,
        wochentag=wochentag,
        summe_start=summe_start, bar=bar, bier=bier, alkoholfrei=alkoholfrei,
        hendl=hendl, steuer=steuer, bar_entnommen=bar_entnommen,
        gesamt=gesamt, tagessumme=tagessumme,
        gespeichert=gespeichert,
        preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFREI, preis_hendl=PREIS_HENDL,
        vortag_link=vortag_link, folgetag_link=folgetag_link,
        im_edit_zeitraum=im_edit_zeitraum
    )


# ------------------------------------------------------------------------------
# Admin-Ansicht (mit Pro-Person-Gesamten)
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
        brutto = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else (brutto - prev_sum)
        pro_person = None if diff is None else (diff / 6.0)
        rows_with.append({
            "datum": r["datum"],
            "tag_summe": brutto,
            "steuer_summe": float(r["steuer_summe"] or 0),
            "diff": diff,
            "pro_person": pro_person
        })
        prev_sum = brutto

    gesamt_brutto = sum(r["tag_summe"] for r in rows_with)
    gesamt_steuer = sum(r["steuer_summe"] for r in rows_with)
    gesamt_nach_steuer = gesamt_brutto - gesamt_steuer

    # Pro Person (Footer)
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
    :root{ --bg:#f8fafc; --card:#fff; }
    body{ background:var(--bg); }
    .navbar-blur{ backdrop-filter:saturate(180%) blur(10px); background:rgba(255,255,255,.85)!important; border-bottom:1px solid rgba(0,0,0,.06); }
    .app-card{ background:var(--card); border:1px solid rgba(13,110,253,.08); box-shadow:0 10px 30px rgba(0,0,0,.05); border-radius:14px; }
    .btn-rounded{ border-radius:999px; }
    .table thead th{ font-weight:600; }
    .table tfoot th{ font-weight:700; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-blur fixed-top">
  <div class="container">
    <a class="navbar-brand fw-bold" href="{{ url_for('login') }}">Wiesn25</a>
    <div class="ms-auto">
      <a href="{{ url_for('login') }}" class="btn btn-sm btn-outline-secondary btn-rounded">Abmelden</a>
    </div>
  </div>
</nav>
<div style="height:56px"></div>

<header class="py-4">
  <div class="container">
    <h1 class="h3 mb-1">Gesamtsummen pro Tag</h1>
    <p class="text-muted mb-0">Brutto je Tag, Steuer separat; am Ende einmal abgezogen.</p>
  </div>
</header>

<main class="container pb-5">
  <div class="card app-card">
    <div class="card-body p-0">
      <div class="table-responsive">
        <table class="table table-hover align-middle mb-0">
          <thead class="table-light">
            <tr>
              <th style="width:15%">Datum</th>
              <th style="width:20%">Gesamtsumme brutto (€)</th>
              <th style="width:20%">Differenz Vortag (€)</th>
              <th style="width:20%">Umsatz pro Person (€)</th>
              <th style="width:25%">Steuer je Tag (€)</th>
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
</main>
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
# Excel-Export (inkl. Pro-Person-Footer)
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
    # Footer-Zeilen inkl. Pro-Person
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
# Admin Backup Download
# ------------------------------------------------------------------------------
@app.route("/backup_db")
def backup_db():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db_path = DATABASE_PATH
    if not os.path.exists(db_path):
        return "Keine Datenbank gefunden.", 404

    return send_file(
        db_path,
        as_attachment=True,
        download_name=f"Wiesn25_Backup_{date.today().isoformat()}.sqlite",
        mimetype="application/x-sqlite3"
    )


# ------------------------------------------------------------------------------
# Admin Restore Upload (Backup einspielen)
# ------------------------------------------------------------------------------
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

    # Prüfen, ob gültige SQLite-DB
    try:
        test = sqlite3.connect(tmp_path)
        test.execute("PRAGMA schema_version;")
        test.close()
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return "Die hochgeladene Datei ist keine gültige SQLite-Datenbank.", 400

    # Aktuelle DB sichern
    if os.path.exists(DATABASE_PATH):
        backup_path = f"{DATABASE_PATH}.bak_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(DATABASE_PATH, backup_path)

    try:
        close_connection(None)
    except Exception:
        pass

    shutil.copy2(tmp_path, DATABASE_PATH)
    os.remove(tmp_path)

    with app.app_context():
        init_db()

    return redirect(url_for("admin_view"))


# ------------------------------------------------------------------------------
# Lokaler Start
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
