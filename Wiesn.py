import os, sqlite3
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file
)
import openpyxl

# -------------------- Konfiguration --------------------
SECRET_KEY     = os.getenv("SECRET_KEY", "change-me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_PATH  = os.getenv("DATABASE_PATH", "verkauf.db")

# Demo-Modus (1 = Demo, 0 = Produktion)
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# Erlaubter Speicherzeitraum im Produktivmodus
DATA_START = date(2025, 9, 20)
DATA_END   = date(2025, 10, 6)

MITARBEITER = ["Florian", "Jonas", "Julia", "Regina", "Schorsch", "Toni"]

# -------------------- Flask App ------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

# -------------------- DB Hilfsfunktionen ----------------
def ensure_db_dir(path):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        ensure_db_dir(DATABASE_PATH)
        db = g._db = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()

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
    db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('demo_mode','0')")
    db.commit()

def get_meta(key):
    r = get_db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None

def set_meta(key, val):
    get_db().execute("""
        INSERT INTO meta(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, val))
    get_db().commit()

with app.app_context():
    init_db()

# -------------------- Demo-Cleanup ---------------------
def demo_cleanup_if_needed():
    """
    Löscht alle Einträge wenn vorher DEMO_MODE=1 war und jetzt 0 ist.
    """
    prev = get_meta("demo_mode")
    current = "1" if DEMO_MODE else "0"
    if prev == "1" and current == "0":
        db = get_db()
        db.execute("DELETE FROM eintraege")
        db.commit()
    set_meta("demo_mode", current)

# -------------------- Routen ---------------------------
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name")
        admin_pw = request.form.get("admin_pw")
        if admin_pw and admin_pw == ADMIN_PASSWORD:
            session.clear(); session["admin"] = True
            demo_cleanup_if_needed()
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear(); session["name"] = name
            return redirect(url_for("eingabe", datum=str(date.today())))
    return render_template_string("""
    <!doctype html><html lang="de"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>Login</title></head>
    <body class="bg-primary text-white">
      <div class="container py-5 text-center">
        <h1 class="mb-4">Willkommen</h1>
        <form method="post" class="bg-light text-dark p-4 rounded shadow-sm mx-auto" style="max-width:400px">
          <div class="mb-3">
            <label class="form-label">Mitarbeiter</label>
            <select name="name" class="form-select">
              <option value="">-- auswählen --</option>
              {% for m in mitarbeiter %}<option value="{{m}}">{{m}}</option>{% endfor %}
            </select>
          </div>
          <div class="mb-3">oder Admin</div>
          <div class="mb-3">
            <input type="password" name="admin_pw" class="form-control" placeholder="Admin Passwort">
          </div>
          <button class="btn btn-primary w-100">Login</button>
        </form>
        {% if demo %}
          <div class="alert alert-info mt-3">Demo-Modus aktiv: Bearbeitung jederzeit möglich.</div>
        {% endif %}
      </div></body></html>
    """, mitarbeiter=MITARBEITER, demo=DEMO_MODE)

@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session: return redirect(url_for("login"))
    db = get_db()
    user = session["name"]
    d = date.fromisoformat(datum)
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
                     (datum, user)).fetchone()

    im_edit = DEMO_MODE or (DATA_START <= d <= DATA_END)

    if request.method == "POST" and im_edit:
        bar  = float(request.form.get("bar", 0) or 0)
        bier = int(request.form.get("bier", 0) or 0)
        alk  = int(request.form.get("alkoholfrei", 0) or 0)
        hendl= int(request.form.get("hendl", 0) or 0)
        summe_start = float(request.form.get("summe_start", 0) or 0) \
            if (DEMO_MODE or d == DATA_START) else (row["summe_start"] if row else 0)
        gesamt = bar + bier*14.01 + alk*6.10 + hendl*22.30
        bar_ent = float(request.form.get("bar_entnommen", 0) or 0)
        tagessumme = gesamt - bar_ent
        if row:
            db.execute("""UPDATE eintraege
                          SET summe_start=?,bar=?,bier=?,alkoholfrei=?,hendl=?,
                              gesamt=?,bar_entnommen=?,tagessumme=?,gespeichert=1
                          WHERE id=?""",
                       (summe_start,bar,bier,alk,hendl,gesamt,bar_ent,tagessumme,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
              (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,
               gesamt,bar_entnommen,tagessumme,gespeichert)
              VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
              (datum,user,summe_start,bar,bier,alk,hendl,gesamt,bar_ent,tagessumme))
        db.commit()
        return redirect(url_for("eingabe", datum=datum))

    vals = dict(summe_start=0,bar=0,bier=0,alkoholfrei=0,hendl=0,
                gesamt=0,bar_entnommen=0,tagessumme=0)
    if row: vals.update(row)

    return render_template_string("""
    <!doctype html><html lang="de"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>Eingabe</title></head>
    <body class="bg-light">
      <div class="container py-4">
        <div class="d-flex justify-content-between mb-3">
          <h3>Eingabe {{datum}} – {{user}}</h3>
          <a href="{{ url_for('login') }}" class="btn btn-primary text-white">Zur Startseite</a>
        </div>
        <div class="mb-3 d-flex gap-2">
          <a class="btn btn-outline-secondary" href="{{ url_for('eingabe', datum=(d - delta).isoformat()) }}">← Vortag</a>
          <a class="btn btn-outline-secondary" href="{{ url_for('eingabe', datum=(d + delta).isoformat()) }}">Folgetag →</a>
          <input type="date" class="form-control" value="{{datum}}" onchange="window.location='/eingabe/'+this.value">
        </div>
        <form method="post" class="card p-3 shadow-sm">
          <div class="row g-3">
            <div class="col-md-4">
              <label class="form-label">Summe Start</label>
              <input type="number" step="0.01" name="summe_start" value="{{vals.summe_start}}" class="form-control" {% if not demo and d!=data_start %}readonly{% endif %}>
            </div>
            <div class="col-md-4">
              <label class="form-label">Bar (€)</label>
              <input type="number" step="0.01" name="bar" value="{{vals.bar}}" class="form-control" {% if not im_edit %}readonly{% endif %}>
            </div>
            <div class="col-md-4">
              <label class="form-label">Bier</label>
              <input type="number" name="bier" value="{{vals.bier}}" class="form-control" {% if not im_edit %}readonly{% endif %}>
            </div>
            <div class="col-md-4">
              <label class="form-label">Alkoholfrei</label>
              <input type="number" name="alkoholfrei" value="{{vals.alkoholfrei}}" class="form-control" {% if not im_edit %}readonly{% endif %}>
            </div>
            <div class="col-md-4">
              <label class="form-label">Hendl</label>
              <input type="number" name="hendl" value="{{vals.hendl}}" class="form-control" {% if not im_edit %}readonly{% endif %}>
            </div>
            <div class="col-md-4">
              <label class="form-label">Bar entnommen (€)</label>
              <input type="number" step="0.01" name="bar_entnommen" value="{{vals.bar_entnommen}}" class="form-control" {% if not im_edit %}readonly{% endif %}>
            </div>
            <div class="col-md-6">
              <label class="form-label">Gesamt (€)</label>
              <input type="number" readonly value="{{vals.gesamt}}" class="form-control">
            </div>
            <div class="col-md-6">
              <label class="form-label">Tagessumme (€)</label>
              <input type="number" readonly value="{{vals.tagessumme}}" class="form-control">
            </div>
          </div>
          {% if im_edit %}
            <button class="btn btn-success mt-3">Speichern</button>
          {% else %}
            <div class="alert alert-secondary mt-3">Bearbeitung nur 20.09.–06.10. möglich.</div>
          {% endif %}
        </form>
      </div>
    </body></html>
    """, datum=datum, user=user, vals=vals, im_edit=im_edit,
         demo=DEMO_MODE, d=d, delta=timedelta(days=1), data_start=DATA_START)

@app.route("/admin")
def admin_view():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""
      SELECT datum,SUM(gesamt) AS summe
      FROM eintraege GROUP BY datum ORDER BY datum
    """).fetchall()
    total = sum(float(r["summe"] or 0) for r in rows)
    return render_template_string("""
    <!doctype html><html lang="de"><head>
    <meta charset="utf-8">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>Admin</title></head><body class="bg-light">
    <div class="container py-4">
      <h3>Admin-Übersicht</h3>
      <table class="table table-bordered">
        <thead><tr><th>Datum</th><th>Gesamt (€)</th></tr></thead>
        <tbody>
          {% for r in rows %}
          <tr><td>{{r["datum"]}}</td><td>{{"%.2f"|format(r["summe"])}}</td></tr>
          {% endfor %}
        </tbody>
        <tfoot class="table-secondary">
          <tr><th>GESAMT</th><th>{{"%.2f"|format(total)}}</th></tr>
        </tfoot>
      </table>
      <form action="{{ url_for('export_excel') }}" method="get">
        <button class="btn btn-primary">Excel Export</button>
      </form>
    </div></body></html>
    """, rows=rows, total=total)

@app.route("/export_excel")
def export_excel():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("SELECT datum,SUM(gesamt) as summe FROM eintraege GROUP BY datum ORDER BY datum").fetchall()
    wb=openpyxl.Workbook();ws=wb.active;ws.title="Gesamt"
    ws.append(["Datum","Gesamt (€)"])
    for r in rows: ws.append([r["datum"], float(r["summe"] or 0)])
    ws.append([]); ws.append(["GESAMT", sum(float(r["summe"] or 0) for r in rows)])
    out=BytesIO(); wb.save(out); out.seek(0)
    return send_file(out, as_attachment=True,
        download_name=f"Wiesn25_Gesamt_{date.today()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# -------------------- Start -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
