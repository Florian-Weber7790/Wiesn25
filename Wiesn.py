import os
import sqlite3
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
# Konfiguration
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

# Produktiv-Zeiträume (werden im Demo-Mode ignoriert)
DATA_START = _get_env_date("DATA_START", "2025-09-20")               # Tage, die bearbeitet werden dürfen
DATA_END   = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18") # Zeitraum, in dem Bearbeitung grundsätzlich erlaubt ist
EDIT_WINDOW_END   = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")

# Demo-Modus: erlaubt immer Bearbeitung (Steuerfeld bleibt nur mittwochs sichtbar)
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
# Healthcheck (Render)
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
        </head>
        <body class="bg-light">
        <div class="container py-5">
          <div class="row justify-content-center">
            <div class="col-12 col-md-6">
              <div class="card shadow-sm">
                <div class="card-body">
                  <h3 class="mb-3">Login</h3>
                  <form method="post">
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
                    <button type="submit" class="btn btn-primary w-100">Einloggen</button>
                  </form>
                  {% if demo_mode %}
                    <div class="alert alert-info mt-3 mb-0">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt (Steuer nur mittwochs sichtbar).</div>
                  {% endif %}
                </div>
              </div>
            </div>
          </div>
        </div>
        </body>
        </html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)


# ------------------------------------------------------------------------------
# Eingabe (mit Entsperren per Passwort) – bleibt nach Speichern auf Datumsseite
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()  # 0=Mo, 2=Mi
    ist_erster_tag = (datum_obj == DATA_START)

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

        # immer auf derselben Datumsseite bleiben
        return redirect(url_for("eingabe", datum=datum))

    # --- Speichern (bleibt auf /eingabe/<datum>) ---
    if request.method == "POST" and action == "save" and im_edit_zeitraum and (DEMO_MODE or (not row or row["gespeichert"] == 0)):
        # summe_start: am ersten erlaubten Tag manuell, sonst vom Vortag
        if ist_erster_tag:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)

        bar = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
        hendl = int(request.form.get("hendl", 0) or 0)
        # Steuer nur mittwochs eingeben/speichern, aber NICHT in Tagesberechnung abziehen
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

        # Bleibe auf der gleichen Datum-Seite (kein Sprung zur Startseite)
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
        # Neue Seite: Startsumme abhängig vom Vortag, außer am ersten Tag
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
            .calc-field { background: #f1f3f5; }
            .readonly   { background: #e9ecef; }
            .editable   { background: #fff3cd; }
          </style>
        </head>
        <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <h3 class="mb-0">Eingabe für {{datum}} – {{name}}</h3>
            <a href="{{ url_for('login') }}" class="btn btn-outline-secondary btn-sm">Zur Startseite</a>
          </div>

          <div class="d-flex align-items-center gap-2 mb-3">
            <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary">← Vortag</a>
            <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary">Folgetag →</a>
            <input type="date" id="datumsauswahl" class="form-control" style="max-width: 220px"
                   value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
            {% if wochentag == 2 %}
            <span class="badge text-bg-info">Mittwoch (Steuerfeld sichtbar)</span>
            {% endif %}
          </div>

          {% if unlock_error %}
            <div class="alert alert-danger">{{ unlock_error }}</div>
          {% endif %}

          <form method="post" oninput="berechne()" class="card shadow-sm">
            <input type="hidden" name="action" value="save">
            <div class="card-body">
              <div class="row g-3">
                <div class="col-12 col-md-6">
                  <label class="form-label">Summe Start</label>
                  <input type="number" step="0.01" name="summe_start" value="{{summe_start}}"
                         class="form-control {% if im_edit_zeitraum and not gespeichert and ist_erster_tag %}editable{% else %}readonly{% endif %}"
                         {% if not (im_edit_zeitraum and not gespeichert and ist_erster_tag) %}readonly{% endif %}>
                </div>

                <div class="col-12 col-md-6">
                  <label class="form-label">Bar (€)</label>
                  <input type="number" step="0.01" id="bar" name="bar" value="{{bar}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>

                <div class="col-12 col-md-4">
                  <label class="form-label">Bier (Anzahl)</label>
                  <input type="number" id="bier" name="bier" value="{{bier}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>

                <div class="col-12 col-md-4">
                  <label class="form-label">Alkoholfrei (Anzahl)</label>
                  <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{alkoholfrei}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>

                <div class="col-12 col-md-4">
                  <label class="form-label">Hendl (Anzahl)</label>
                  <input type="number" id="hendl" name="hendl" value="{{hendl}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>

                {% if wochentag == 2 %}
                <div class="col-12 col-md-6">
                  <label class="form-label">Steuer (€)</label>
                  <input type="number" step="0.01" id="steuer" name="steuer" value="{{steuer}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>
                {% endif %}

                <div class="col-12 col-md-6">
                  <label class="form-label">Bar entnommen (€)</label>
                  <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{bar_entnommen}}" min="0"
                         class="form-control {% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                         {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}>
                </div>

                <div class="col-12 col-md-6">
                  <label class="form-label">Gesamt (€)</label>
                  <input type="number" step="0.01" id="gesamt" readonly class="form-control calc-field"
                         value="{{ '%.2f' % gesamt }}">
                </div>

                <div class="col-12 col-md-6">
                  <label class="form-label">Tagessumme (€)</label>
                  <input type="number" step="0.01" id="tagessumme" readonly class="form-control calc-field"
                         value="{{ '%.2f' % tagessumme }}">
                </div>
              </div>

              <div class="mt-4 d-flex gap-2">
                {% if im_edit_zeitraum and not gespeichert %}
                  <button type="submit" class="btn btn-success">Speichern</button>
                {% else %}
                  <div class="alert alert-secondary mb-0">Bearbeitung gesperrt. Zum Ändern bitte entsperren.</div>
                {% endif %}
                <a class="btn btn-outline-secondary" href="{{ url_for('login') }}">Zur Startseite</a>
              </div>
            </div>
          </form>

          {% if gespeichert %}
          <div class="card shadow-sm mt-4">
            <div class="card-body">
              <h5 class="card-title">Eintrag bearbeiten (entsperren)</h5>
              <form method="post" class="row g-2">
                <input type="hidden" name="action" value="unlock">
                <div class="col-12 col-md-6">
                  <input type="password" name="edit_pw" class="form-control" placeholder="Passwort" autocomplete="current-password" required>
                </div>
                <div class="col-12 col-md-6">
                  <button type="submit" class="btn btn-warning w-100">Editieren freischalten</button>
                </div>
              </form>
            </div>
          </div>
          {% endif %}
        </div>

        <script>
        function berechne() {
            let preisBier = {{preis_bier}};
            let preisAlk = {{preis_alk}};
            let preisHendl = {{preis_hendl}};

            let bar = parseFloat(document.getElementById("bar")?.value) || 0;
            let bier = parseInt(document.getElementById("bier")?.value) || 0;
            let alkoholfrei = parseInt(document.getElementById("alkoholfrei")?.value) || 0;
            let hendl = parseInt(document.getElementById("hendl")?.value) || 0;
            let barEntnommen = parseFloat(document.getElementById("bar_entnommen")?.value) || 0;

            // Steuer NICHT abziehen – Tagesanzeige bleibt brutto
            let gesamt = bar + (bier * preisBier) + (alkoholfrei * preisAlk) + (hendl * preisHendl);
            let tagessumme = gesamt - barEntnommen;

            if (document.getElementById("gesamt")) document.getElementById("gesamt").value = gesamt.toFixed(2);
            if (document.getElementById("tagessumme")) document.getElementById("tagessumme").value = tagessumme.toFixed(2);
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
# Admin-Ansicht (tägliche Brutto-Summen; Steuer nur am Ende einmal abgezogen)
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

    # Anzeige: tägliche Brutto-Summen (ohne Steuerabzug)
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

    return render_template_string("""
        <!doctype html>
        <html lang="de">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
          <title>Admin</title>
        </head>
        <body class="bg-light">
        <div class="container py-4">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <h3 class="mb-0">Gesamtsummen pro Tag</h3>
            <a href="{{ url_for('login') }}" class="btn btn-outline-secondary btn-sm">Abmelden</a>
          </div>

          <div class="card shadow-sm">
            <div class="card-body">
              <div class="table-responsive">
                <table class="table table-bordered table-striped align-middle">
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
                        <td>{{ r.datum }}</td>
                        <td>{{ "%.2f"|format(r.tag_summe) }}</td>
                        <td>{% if r.diff is not none %}{{ "%.2f"|format(r.diff) }}{% else %}-{% endif %}</td>
                        <td>{% if r.pro_person is not none %}{{ "%.2f"|format(r.pro_person) }}{% else %}-{% endif %}</td>
                        <td>{{ "%.2f"|format(r.steuer_summe) }}</td>
                      </tr>
                    {% endfor %}
                  </tbody>
                  <tfoot class="table-secondary">
                    <tr>
                      <th>GESAMT BRUTTO</th>
                      <th>{{ "%.2f"|format(gesamt_brutto) }}</th>
                      <th colspan="2"></th>
                      <th>{{ "%.2f"|format(gesamt_steuer) }}</th>
                    </tr>
                    <tr class="table-dark">
                      <th>GESAMT NACH STEUER</th>
                      <th>{{ "%.2f"|format(gesamt_nach_steuer) }}</th>
                      <th colspan="3"></th>
                    </tr>
                  </tfoot>
                </table>
              </div>

              <form action="{{ url_for('export_excel') }}" method="get" class="mt-3">
                <button type="submit" class="btn btn-primary">📥 Export als Excel</button>
              </form>
            </div>
          </div>
        </div>
        </body>
        </html>
    """, rows=rows_with,
       gesamt_brutto=gesamt_brutto,
       gesamt_steuer=gesamt_steuer,
       gesamt_nach_steuer=gesamt_nach_steuer
    )


# ------------------------------------------------------------------------------
# Excel-Export (brutto je Tag, Steuer je Tag; am Ende einmal abziehen)
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
    ws.append(["GESAMT BRUTTO", gesamt_brutto, "", "", gesamt_steuer])
    ws.append(["GESAMT NACH STEUER", gesamt_nach_steuer])

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
# Lokaler Start
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
