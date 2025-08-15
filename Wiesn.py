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

# Produktiv-Zeiträume
DATA_START = _get_env_date("DATA_START", "2025-09-20")               # Tage, die bearbeitet werden dürfen
DATA_END   = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18") # Zeitraum, in dem Bearbeitung grundsätzlich erlaubt ist
EDIT_WINDOW_END   = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")

# Demo-Modus: erlaubt alles jederzeit (Steuerfeld trotzdem nur mittwochs sichtbar)
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
# Eingabe (mit Entsperren per Mitarbeiter-Passwort)
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session:
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()  # 0 = Montag, 2 = Mittwoch usw.

    demo_mode = os.getenv("DEMO_MODE", "0") == "1"

    im_edit_zeitraum = EDIT_BEARBEITBAR_START <= datum_obj <= EDIT_BEARBEITBAR_ENDE
    tag_im_fenster = EDIT_START <= datum_obj <= EDIT_END

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, session["name"])
    ).fetchone()

    # POST-Verarbeitung
    if request.method == "POST":
        if "unlock" in request.form:
            pw = request.form.get("pw", "")
            if pw == MITARBEITER_PASSWOERTER.get(session["name"], ""):
                db.execute(
                    "UPDATE eintraege SET gespeichert=0 WHERE datum=? AND mitarbeiter=?",
                    (datum, session["name"])
                )
                db.commit()
            return redirect(url_for("eingabe", datum=datum))

        if (demo_mode or (im_edit_zeitraum and tag_im_fenster and (not row or row["gespeichert"] == 0))):
            if datum_obj == EDIT_START:
                summe_start = float(request.form.get("summe_start", 0) or 0)
            else:
                vortag = datum_obj - timedelta(days=1)
                vortag_row = db.execute(
                    "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                    (vortag.isoformat(), session["name"])
                ).fetchone()
                summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)

            bar = float(request.form.get("bar", 0) or 0)
            bier = int(request.form.get("bier", 0) or 0)
            alkoholfrei = int(request.form.get("alkoholfrei", 0) or 0)
            hendl = int(request.form.get("hendl", 0) or 0)
            steuer = float(request.form.get("steuer", 0) or 0)
            bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)

            gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL) + steuer
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
                """, (datum, session["name"], summe_start, bar, bier, alkoholfrei, hendl, steuer,
                      gesamt, bar_entnommen, tagessumme))
            db.commit()
        return redirect(url_for("eingabe", datum=datum))

    # Werte für GET
    gespeichert = row["gespeichert"] if row else 0
    if row:
        summe_start = row["summe_start"]
        bar = row["bar"]
        bier = row["bier"]
        alkoholfrei = row["alkoholfrei"]
        hendl = row["hendl"]
        steuer = row["steuer"]
        bar_entnommen = row["bar_entnommen"]
    else:
        if datum_obj == EDIT_START:
            summe_start = 0
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), session["name"])
            ).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)
        bar = bier = alkoholfrei = hendl = steuer = bar_entnommen = 0

    vortag_link = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()

    return render_template_string(
        EINGABE_TEMPLATE,
        datum=datum,
        name=session["name"],
        summe_start=summe_start,
        bar=bar,
        bier=bier,
        alkoholfrei=alkoholfrei,
        hendl=hendl,
        steuer=steuer,
        bar_entnommen=bar_entnommen,
        gespeichert=gespeichert,
        preis_bier=PREIS_BIER,
        preis_alk=PREIS_ALKOHOLFREI,
        preis_hendl=PREIS_HENDL,
        vortag_link=vortag_link,
        folgetag_link=folgetag_link,
        im_edit_zeitraum=im_edit_zeitraum,
        tag_im_fenster=tag_im_fenster,
        edit_start=EDIT_START.isoformat(),
        edit_end=EDIT_END.isoformat(),
        demo_mode=demo_mode,
        wochentag=wochentag
    )


# ------------------------------------------------------------------------------
# Admin-Ansicht (Differenz, Umsatz/Person, Steuer) + Export
# ------------------------------------------------------------------------------
@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    # Tagessumme (gesamt) UND Tagessumme Steuer
    rows = db.execute("""
        SELECT
          datum,
          SUM(gesamt)      AS tag_summe,
          SUM(steuer)      AS steuer_summe
        FROM eintraege
        WHERE gesamt IS NOT NULL
        GROUP BY datum
        HAVING SUM(gesamt) > 0
        ORDER BY datum
    """).fetchall()

    # Mit Differenz und Umsatz/Person anreichern
    rows_with = []
    prev_sum = None
    for r in rows:
        s = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else (s - prev_sum)
        pro_person = None if diff is None else (diff / 6.0)
        rows_with.append({
            "datum": r["datum"],
            "tag_summe": s,
            "diff": diff,
            "pro_person": pro_person,
            "steuer_summe": float(r["steuer_summe"] or 0)
        })
        prev_sum = s

    # Gesamtrechnungen
    gesamt_summe = sum(r["tag_summe"] for r in rows_with)
    gesamt_diff = sum(r["diff"] for r in rows_with if r["diff"] is not None)
    gesamt_pro_person = sum(r["pro_person"] for r in rows_with if r["pro_person"] is not None)
    gesamt_steuer = sum(r["steuer_summe"] for r in rows_with)

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
                      <th>Gesamtsumme (€)</th>
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
                      <th>GESAMT</th>
                      <th>{{ "%.2f"|format(gesamt_summe) }}</th>
                      <th>{{ "%.2f"|format(gesamt_diff) }}</th>
                      <th>{{ "%.2f"|format(gesamt_pro_person) }}</th>
                      <th>{{ "%.2f"|format(gesamt_steuer) }}</th>
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
       gesamt_summe=gesamt_summe,
       gesamt_diff=gesamt_diff,
       gesamt_pro_person=gesamt_pro_person,
       gesamt_steuer=gesamt_steuer
    )


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
        s = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else (s - prev_sum)
        pro_person = None if diff is None else (diff / 6.0)
        steuer_summe = float(r["steuer_summe"] or 0)
        data.append((r["datum"], s, diff, pro_person, steuer_summe))
        prev_sum = s

    gesamt_summe = sum(s for _, s, _, _, _ in data)
    gesamt_diff = sum(d for _, _, d, _, _ in data if d is not None)
    gesamt_pro_person = sum(p for _, _, _, p, _ in data if p is not None)
    gesamt_steuer = sum(st for _, _, _, _, st in data)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamtsummen"
    ws.append(["Datum", "Gesamtsumme (€)", "Differenz Vortag (€)", "Umsatz pro Person (€)", "Steuer je Tag (€)"])
    for d, s, diff, pro_person, steuer_summe in data:
        ws.append([
            d,
            s,
            "" if diff is None else diff,
            "" if pro_person is None else pro_person,
            steuer_summe
        ])
    ws.append([])
    ws.append(["GESAMT", gesamt_summe, gesamt_diff, gesamt_pro_person, gesamt_steuer])

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
