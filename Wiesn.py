import os
import sqlite3
from datetime import date, timedelta, datetime
from io import BytesIO
from pathlib import Path

from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file, flash
)
import openpyxl

# ===================== Konfiguration / ENV =====================
SECRET_KEY     = os.getenv("SECRET_KEY", "change-me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_PATH  = os.getenv("DATABASE_PATH", "verkauf.db")

# Demo-Modus: 1 = Demo, 0 = Produktion
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# Erlaubter Speicherzeitraum im Produktivmodus
DATA_START = date(2025, 9, 20)
DATA_END   = date(2025, 10, 6)

# Preise (nur intern zur Berechnung; nicht anzeigen)
PREIS_BIER       = 14.01
PREIS_ALKOHOLFREI= 6.10
PREIS_HENDL      = 22.30

# Mitarbeiter in gewünschter Reihenfolge
MITARBEITER = ["Florian", "Jonas", "Julia", "Regina", "Schorsch", "Toni"]

# ===================== Flask Setup =====================
app = Flask(__name__)
app.secret_key = SECRET_KEY


# ===================== DB-Helfer =====================
def ensure_db_dir(path: str):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        ensure_db_dir(DATABASE_PATH)
        db = g._db = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db: db.close()

def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS eintraege(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          datum TEXT,
          mitarbeiter TEXT,
          summe_start REAL,
          bar REAL,
          bier INTEGER,
          alkoholfrei INTEGER,
          hendl INTEGER,
          gesamt REAL,
          bar_entnommen REAL,
          tagessumme REAL,
          gespeichert INTEGER,
          UNIQUE(datum, mitarbeiter)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta(
          key TEXT PRIMARY KEY,
          value TEXT
        )
    """)
    # Default für demo_mode in meta
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('demo_mode','0')")
    db.commit()

with app.app_context():
    init_db()


# ===================== Demo-Cleanup bei Moduswechsel =====================
def demo_cleanup_if_needed():
    """
    Wenn der zuletzt gespeicherte Demo-Status '1' war und die App jetzt mit DEMO_MODE==0 läuft,
    lösche alle Einträge in 'eintraege'. Aktualisiere danach den Status in 'meta'.
    """
    db = get_db()
    row = db.execute("SELECT value FROM meta WHERE key='demo_mode'").fetchone()
    prev = (row["value"] if row else "0")
    current = "1" if DEMO_MODE else "0"

    if prev == "1" and current == "0":
        db.execute("DELETE FROM eintraege")
        db.commit()

    db.execute("""
        INSERT INTO meta(key,value) VALUES('demo_mode', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (current,))
    db.commit()


# ===================== Healthcheck (optional) =====================
@app.route("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "demo": DEMO_MODE}


# ===================== Login / Startseite =====================
@app.route("/", methods=["GET", "POST"])
def login():
    # Beim Aufruf der Startseite Moduswechsel prüfen und ggf. Demo-Daten löschen
    demo_cleanup_if_needed()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        admin_pw = (request.form.get("admin_pw") or "").strip()

        # Admin-Login
        if admin_pw:
            if admin_pw == ADMIN_PASSWORD:
                session.clear()
                session["admin"] = True
                return redirect(url_for("admin_view"))
            else:
                flash("Falsches Admin-Passwort.")

        # Mitarbeiter-Login
        if name in MITARBEITER:
            session.clear()
            session["name"] = name
            return redirect(url_for("eingabe", datum=str(date.today())))

        # Meldungen
        if not admin_pw and not name:
            flash("Bitte Mitarbeiter auswählen oder Admin-Passwort eingeben.")
        elif name and name not in MITARBEITER:
            flash("Unbekannter Mitarbeiter.")

    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Willkommen</title>
<style>
  :root{ --blue:#0a2a66; }
  body{ background:var(--blue); color:#fff; }
  .card-login{ background:#ffffff; color:#111; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.25); }
  .countdown{ font-size:1.5rem; font-weight:700; letter-spacing:.3px; }
  .heading{ font-weight:800; letter-spacing:.4px; }
  .note-small{ font-size:.9rem; color:#cfd8ff; }
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
  <div class="container" style="max-width:980px;">
    <div class="text-center mb-4">
      <h1 class="heading display-6">Willkommen zur Wiesn-Abrechnung</h1>
      <div id="countdown" class="countdown mt-2">–</div>
    </div>

    <div class="card card-login mx-auto mt-2" style="max-width:520px;">
      <div class="card-body p-4">
        <h4 class="mb-3">Login</h4>

        {% with msgs = get_flashed_messages() %}
          {% if msgs %}
            <div class="alert alert-danger py-2">{{ msgs[0] }}</div>
          {% endif %}
        {% endwith %}

        <form method="post" action="{{ url_for('login') }}">
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

        <p class="note-small text-center mt-3 mb-0">
          Speichern im Produktivmodus (DEMO_MODE=0) ist nur vom <b>20.09.</b> bis <b>06.10.</b> möglich.
        </p>

        {% if demo_mode %}
          <div class="alert alert-info small mt-3 mb-0">
            Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.<br>
            Beim Umschalten auf Produktion (DEMO_MODE=0) werden alle Demo-Daten automatisch gelöscht.
          </div>
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
      `Noch ${d} Tage ${h} Std ${m} Min`;
  }
  updateCountdown();
  setInterval(updateCountdown, 60000);
</script>
</body>
</html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)


# ===================== Eingabe =====================
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session:
        return redirect(url_for("login"))

    user = session["name"]
    d_obj = date.fromisoformat(datum)

    # Nur im DEMO_MODE immer editierbar; sonst nur im erlaubten Datenbereich
    im_edit = DEMO_MODE or (DATA_START <= d_obj <= DATA_END)

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, user)
    ).fetchone()

    if request.method == "POST" and im_edit:
        # Im Produktivmodus nur speichern, wenn Datum im erlaubten Intervall liegt
        if not DEMO_MODE and not (DATA_START <= d_obj <= DATA_END):
            flash("Speichern im Produktivmodus nur 20.09.–06.10. erlaubt.")
            return redirect(url_for("eingabe", datum=datum))

        # Werte einlesen
        bar  = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alk  = int(request.form.get("alkoholfrei", 0) or 0)
        hendl= int(request.form.get("hendl", 0) or 0)

        # summe_start: frei in Demo oder am ersten Tag; sonst bestehend lassen (oder 0 bei neuem Datensatz)
        if DEMO_MODE or d_obj == DATA_START:
            summe_start = float(request.form.get("summe_start", 0) or 0)
        else:
            summe_start = float(row["summe_start"]) if row else 0.0

        gesamt = bar + bier*PREIS_BIER + alk*PREIS_ALKOHOLFREI + hendl*PREIS_HENDL
        bar_ent = float(request.form.get("bar_entnommen", 0) or 0)
        tagessumme = gesamt - bar_ent

        if row:
            db.execute("""
                UPDATE eintraege
                SET summe_start=?, bar=?, bier=?, alkoholfrei=?, hendl=?,
                    gesamt=?, bar_entnommen=?, tagessumme=?, gespeichert=1
                WHERE id=?
            """, (summe_start, bar, bier, alk, hendl, gesamt, bar_ent, tagessumme, row["id"]))
        else:
            db.execute("""
                INSERT INTO eintraege
                (datum, mitarbeiter, summe_start, bar, bier, alkoholfrei, hendl,
                 gesamt, bar_entnommen, tagessumme, gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,1)
            """, (datum, user, summe_start, bar, bier, alk, hendl, gesamt, bar_ent, tagessumme))
        db.commit()
        flash("Gespeichert ✔︎")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige-Werte vorbereiten
    if row:
        vals = dict(row)
    else:
        vals = dict(summe_start=0, bar=0, bier=0, alkoholfrei=0, hendl=0,
                    gesamt=0, bar_entnommen=0, tagessumme=0, gespeichert=0)

    vortag_link = (d_obj - timedelta(days=1)).isoformat()
    folgetag_link = (d_obj + timedelta(days=1)).isoformat()

    return render_template_string("""
<!doctype html><html lang="de">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
<style>
  .calc-field{background:#f1f3f5}
  .readonly{background:#e9ecef}
  .editable{background:#fff3cd}
</style>
</head>
<body class="bg-light">
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3>Eingabe {{datum}} – {{user}}</h3>
    <a href="{{ url_for('login') }}" class="btn btn-primary text-white">Zur Startseite</a>
  </div>

  <div class="d-flex gap-2 mb-3">
    <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="btn btn-outline-primary">← Vortag</a>
    <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="btn btn-outline-primary">Folgetag →</a>
    <input type="date" class="form-control" style="max-width:220px"
           value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
  </div>

  {% for m in get_flashed_messages() %}
    <div class="alert alert-success">{{ m }}</div>
  {% endfor %}

  <form method="post" oninput="berechne()" class="card p-3 shadow-sm">
    <div class="row g-3">
      <div class="col-md-4">
        <label class="form-label">Summe Start</label>
        <input type="number" step="0.01" name="summe_start" value="{{ vals['summe_start'] }}"
          class="form-control {% if (demo or datum==data_start_str) %}editable{% else %}readonly{% endif %}"
          {% if not (demo or datum==data_start_str) %}readonly{% endif %}>
      </div>

      <div class="col-md-4">
        <label class="form-label">Bar (€)</label>
        <input type="number" step="0.01" id="bar" name="bar" value="{{ vals['bar'] }}"
          class="form-control {% if im_edit %}editable{% else %}readonly{% endif %}"
          {% if not im_edit %}readonly{% endif %}>
      </div>

      <div class="col-md-4">
        <label class="form-label">Bar entnommen (€)</label>
        <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{ vals['bar_entnommen'] }}"
          class="form-control {% if im_edit %}editable{% else %}readonly{% endif %}"
          {% if not im_edit %}readonly{% endif %}>
      </div>

      <div class="col-md-4">
        <label class="form-label">Bier (Anzahl)</label>
        <input type="number" id="bier" name="bier" value="{{ vals['bier'] }}"
          class="form-control {% if im_edit %}editable{% else %}readonly{% endif %}"
          {% if not im_edit %}readonly{% endif %}>
      </div>

      <div class="col-md-4">
        <label class="form-label">Alkoholfrei (Anzahl)</label>
        <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{ vals['alkoholfrei'] }}"
          class="form-control {% if im_edit %}editable{% else %}readonly{% endif %}"
          {% if not im_edit %}readonly{% endif %}>
      </div>

      <div class="col-md-4">
        <label class="form-label">Hendl (Anzahl)</label>
        <input type="number" id="hendl" name="hendl" value="{{ vals['hendl'] }}"
          class="form-control {% if im_edit %}editable{% else %}readonly{% endif %}"
          {% if not im_edit %}readonly{% endif %}>
      </div>

      <div class="col-md-6">
        <label class="form-label">Gesamt (€)</label>
        <input type="number" step="0.01" id="gesamt" readonly class="form-control calc-field"
          value="{{ '%.2f' % (vals['gesamt'] or 0) }}">
      </div>

      <div class="col-md-6">
        <label class="form-label">Tagessumme (€)</label>
        <input type="number" step="0.01" id="tagessumme" readonly class="form-control calc-field"
          value="{{ '%.2f' % (vals['tagessumme'] or 0) }}">
      </div>
    </div>

    <div class="mt-3">
      {% if im_edit %}
        <button class="btn btn-success">Speichern</button>
      {% else %}
        <div class="alert alert-secondary mt-2 mb-0">Bearbeitung nur 20.09.–06.10. möglich (Produktivmodus).</div>
      {% endif %}
    </div>
  </form>
</div>

<script>
function berechne(){
  const preisB={{preis_bier}}, preisA={{preis_alk}}, preisH={{preis_hendl}};
  let bar=parseFloat(document.getElementById("bar")?.value)||0;
  let bier=parseInt(document.getElementById("bier")?.value)||0;
  let alkoholfrei=parseInt(document.getElementById("alkoholfrei")?.value)||0;
  let hendl=parseInt(document.getElementById("hendl")?.value)||0;
  let gesamt=bar + bier*preisB + alkoholfrei*preisA + hendl*preisH;
  let entn=parseFloat(document.getElementById("bar_entnommen")?.value)||0;
  let tag=gesamt - entn;
  const g=document.getElementById("gesamt");
  const t=document.getElementById("tagessumme");
  if(g) g.value=gesamt.toFixed(2);
  if(t) t.value=tag.toFixed(2);
}
window.addEventListener('load', berechne);
</script>
</body></html>
    """,
        datum=datum,
        user=user,
        vals=vals,
        demo=DEMO_MODE,
        im_edit=im_edit,
        vortag_link=vortag_link,
        folgetag_link=folgetag_link,
        data_start_str=DATA_START.isoformat(),
        preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFREI, preis_hendl=PREIS_HENDL
    )


# ===================== Admin-Ansicht =====================
@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))

    # Auch hier vorsichtshalber Demo-Wechsel prüfen/löschen
    demo_cleanup_if_needed()

    db = get_db()
    rows = db.execute("""
        SELECT datum, SUM(gesamt) AS summe
        FROM eintraege
        GROUP BY datum
        ORDER BY datum
    """).fetchall()

    total = sum(float(r["summe"] or 0) for r in rows)

    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Admin</title></head>
<body class="bg-light">
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3>Admin-Übersicht</h3>
    <a href="{{ url_for('login') }}" class="btn btn-outline-secondary">Zur Startseite</a>
  </div>

  <div class="card shadow-sm">
    <div class="card-body">
      <div class="table-responsive">
        <table class="table table-bordered table-striped align-middle">
          <thead class="table-light">
            <tr>
              <th>Datum</th>
              <th>Gesamt (€)</th>
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td>{{ r["datum"] }}</td>
                <td>{{ "%.2f"|format(r["summe"] or 0) }}</td>
              </tr>
            {% endfor %}
          </tbody>
          <tfoot class="table-secondary">
            <tr>
              <th>GESAMT</th>
              <th>{{ "%.2f"|format(total) }}</th>
            </tr>
          </tfoot>
        </table>
      </div>

      <form action="{{ url_for('export_excel') }}" method="get" class="mt-2">
        <button class="btn btn-primary">📥 Excel Export</button>
      </form>
    </div>
  </div>
</div>
</body></html>
    """, rows=rows, total=total)


# ===================== Excel-Export =====================
@app.route("/export_excel")
def export_excel():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute("""
        SELECT datum, SUM(gesamt) AS summe
        FROM eintraege
        GROUP BY datum
        ORDER BY datum
    """).fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamt"
    ws.append(["Datum", "Gesamt (€)"])
    for r in rows:
        ws.append([r["datum"], float(r["summe"] or 0)])
    ws.append([])
    ws.append(["GESAMT", sum(float(r["summe"] or 0) for r in rows)])

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"Wiesn25_Gesamt_{date.today().isoformat()}.xlsx"
    return send_file(
        out,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ===================== Start (lokal) =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
