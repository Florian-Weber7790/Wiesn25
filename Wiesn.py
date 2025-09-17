import os, sqlite3, shutil, time
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file, flash
)
import openpyxl

# ----------------------------------------------------------------------
# Konfiguration & ENV
# ----------------------------------------------------------------------
def _get_env(key, default=None): return os.getenv(key, default)
def _get_env_float(key, default):
    try: return float(os.getenv(key, str(default)).replace(",", "."))
    except Exception: return default
def _get_env_date(key, default_iso): return date.fromisoformat(os.getenv(key, default_iso))

SECRET_KEY     = _get_env("SECRET_KEY", "change-me")
ADMIN_PASSWORD = _get_env("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH  = _get_env("DATABASE_PATH", "verkauf.db")

PREIS_BIER        = _get_env_float("PREIS_BIER", 14.01)
PREIS_ALKOHOLFREI = _get_env_float("PREIS_ALKOHOLFREI", 6.10)
PREIS_HENDL       = _get_env_float("PREIS_HENDL", 22.30)

# Mitarbeiter-Reihenfolge wie gewünscht
MITARBEITER = ["Florian", "Jonas", "Julia", "Regina", "Schorsch", "Toni"]

# Bearbeitungs-Zeiträume
DATA_START        = _get_env_date("DATA_START", "2025-09-20")
DATA_END          = _get_env_date("DATA_END",   "2025-10-06")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18")
EDIT_WINDOW_END   = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")

# Demo-Modus: 1 = immer bearbeiten/speichern
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# ----------------------------------------------------------------------
# Flask App
# ----------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ----------------------------------------------------------------------
# DB-Helfer
# ----------------------------------------------------------------------
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
def close_connection(exc):
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
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.execute("INSERT OR IGNORE INTO meta (key,value) VALUES ('demo_mode','0')")
    db.commit()

with app.app_context():
    init_db()

# ----------------------------------------------------------------------
# Demo-Cleanup beim Umschalten auf Produktion
# ----------------------------------------------------------------------
def cleanup_if_demo_disabled():
    db = get_db()
    row = db.execute("SELECT value FROM meta WHERE key='demo_mode'").fetchone()
    prev = row["value"] if row else "0"
    curr = "1" if DEMO_MODE else "0"
    if prev == "1" and curr == "0":
        db.execute("DELETE FROM eintraege")
        db.commit()
    db.execute("INSERT INTO meta(key,value) VALUES('demo_mode',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (curr,))
    db.commit()

# ----------------------------------------------------------------------
# Willkommen / Login mit Countdown
# ----------------------------------------------------------------------
@app.route("/", methods=["GET","POST"])
def login():
    cleanup_if_demo_disabled()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        admin_pw = (request.form.get("admin_pw") or "").strip()
        if admin_pw and admin_pw == ADMIN_PASSWORD:
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
  :root{ --blue:#0a2a66; }
  body{ background:var(--blue); color:#fff; }
  .card-login{ background:#fff; color:#111; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.25); }
  .countdown{ font-size:1.5rem; font-weight:700; }
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
<div class="container" style="max-width:960px;">
  <div class="text-center mb-4">
    <h1 class="display-6 fw-bold">Willkommen zur Wiesn-Abrechnung</h1>
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
          <input type="password" name="admin_pw" class="form-control">
        </div>
        <button class="btn btn-primary w-100">Einloggen</button>
      </form>
      {% if demo_mode %}
        <div class="alert alert-info small mt-3 mb-0">
          Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.
        </div>
      {% endif %}
    </div>
  </div>
</div>
<script>
 const deadline = new Date("2025-10-05T23:00:00");
 function updateCountdown(){
   const diff = Math.max(0,(deadline - new Date())/1000);
   const d=Math.floor(diff/86400),h=Math.floor(diff%86400/3600),m=Math.floor(diff%3600/60);
   document.getElementById('countdown').textContent = `${d} Tage ${h} Std ${m} Min verbleiben`;
 }
 updateCountdown(); setInterval(updateCountdown,60000);
</script>
</body></html>
""", mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ----------------------------------------------------------------------
# Eingabe inkl. Entsperren per Passwort
# ----------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))
    aktiver = session.get("name","ADMIN")
    datum_obj = date.fromisoformat(datum)
    db = get_db()
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
                     (datum, aktiver)).fetchone()

    # Bearbeitungsfenster
    if DEMO_MODE:
        edit_ok = True
    else:
        edit_ok = (EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END) \
                  and (DATA_START <= datum_obj <= DATA_END)

    action = request.form.get("action")

    # Entsperren
    if request.method=="POST" and action=="unlock" and row:
        pw = request.form.get("edit_pw","").strip()
        if session.get("admin") and pw==ADMIN_PASSWORD:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit(); flash("Eintrag entsperrt")
        elif not session.get("admin") and pw == aktiver.lower()+"123":
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],))
            db.commit(); flash("Eintrag entsperrt")
        else:
            flash("Falsches Passwort")
        return redirect(url_for("eingabe", datum=datum))

    # Speichern
    if request.method=="POST" and action=="save" and edit_ok and (DEMO_MODE or not row or row["gespeichert"]==0):
        ist_erster = datum_obj == DATA_START
        if ist_erster:
            summe_start = float(request.form.get("summe_start",0) or 0)
        else:
            vortag = datum_obj - timedelta(days=1)
            v = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                           (vortag.isoformat(), aktiver)).fetchone()
            summe_start = float(v["tagessumme"] if v else 0)
        bar  = float(request.form.get("bar",0) or 0)
        bier = int(request.form.get("bier",0) or 0)
        alk  = int(request.form.get("alkoholfrei",0) or 0)
        hndl = int(request.form.get("hendl",0) or 0)
        steuer = float(request.form.get("steuer",0) or 0) if datum_obj.weekday()==2 else 0
        gesamt = bar + bier*PREIS_BIER + alk*PREIS_ALKOHOLFREI + hndl*PREIS_HENDL
        bar_ent = float(request.form.get("bar_entnommen",0) or 0)
        tagessumme = gesamt - bar_ent

        if row:
            db.execute("""UPDATE eintraege SET summe_start=?,bar=?,bier=?,alkoholfrei=?,
                          hendl=?,steuer=?,gesamt=?,bar_entnommen=?,tagessumme=?,gespeichert=1 WHERE id=?""",
                       (summe_start,bar,bier,alk,hndl,steuer,gesamt,bar_ent,tagessumme,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
               (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,steuer,
                gesamt,bar_entnommen,tagessumme,gespeichert)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
               (datum,aktiver,summe_start,bar,bier,alk,hndl,steuer,gesamt,bar_ent,tagessumme))
        db.commit(); flash("Gespeichert")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige
    vals = dict(row) if row else {
        "summe_start":0,"bar":0,"bier":0,"alkoholfrei":0,"hendl":0,"steuer":0,
        "gesamt":0,"bar_entnommen":0,"tagessumme":0,"gespeichert":0
    }
    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
</head><body class="container py-4">
<h3>Eingabe – {{name}} ({{datum}})</h3>
{% with m=get_flashed_messages() %}{% if m %}<div class="alert alert-info">{{m[0]}}</div>{% endif %}{% endwith %}
<form method="post" class="card p-3 mb-3">
<input type="hidden" name="action" value="save">
<div class="mb-2">
<label>Summe Start (€)</label>
<input name="summe_start" type="number" step="0.01" value="{{vals.summe_start}}"
       class="form-control" {% if not edit_ok or (vals.gespeichert and datum!=data_start) %}readonly{% endif %}>
</div>
<div class="mb-2"><label>Bar (€)</label>
<input name="bar" type="number" step="0.01" value="{{vals.bar}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
<div class="mb-2"><label>Bier</label>
<input name="bier" type="number" value="{{vals.bier}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
<div class="mb-2"><label>Alkoholfrei</label>
<input name="alkoholfrei" type="number" value="{{vals.alkoholfrei}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
<div class="mb-2"><label>Hendl</label>
<input name="hendl" type="number" value="{{vals.hendl}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
{% if date.fromisoformat(datum).weekday()==2 %}
<div class="mb-2"><label>Steuer (€)</label>
<input name="steuer" type="number" step="0.01" value="{{vals.steuer}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
{% endif %}
<div class="mb-2"><label>Bar entnommen (€)</label>
<input name="bar_entnommen" type="number" step="0.01" value="{{vals.bar_entnommen}}" class="form-control"
       {% if not edit_ok or vals.gespeichert %}readonly{% endif %}></div>
<div class="mb-2"><label>Gesamt (€)</label>
<input readonly class="form-control" value="{{'%.2f' % vals.gesamt}}"></div>
<div class="mb-2"><label>Tagessumme (€)</label>
<input readonly class="form-control" value="{{'%.2f' % vals.tagessumme}}"></div>
{% if edit_ok and not vals.gespeichert %}
<button class="btn btn-success mt-2">Speichern</button>
{% else %}
<div class="alert alert-secondary mt-2">Bearbeitung gesperrt. Zum Ändern entsperren.</div>
{% endif %}
</form>
<a class="btn btn-outline-secondary" href="{{ url_for('login') }}">Zur Startseite</a>
{% if vals.gespeichert %}
<form method="post" class="card p-3 mt-3">
<input type="hidden" name="action" value="unlock">
<input type="password" name="edit_pw" class="form-control mb-2" placeholder="Passwort" required>
<button class="btn btn-warning">Editieren freischalten</button>
</form>
{% endif %}
</body></html>
""", name=aktiver, datum=datum, vals=vals,
       edit_ok=edit_ok, data_start=DATA_START.isoformat())

# ----------------------------------------------------------------------
# Admin & Export
# ----------------------------------------------------------------------
@app.route("/admin")
def admin_view():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""
        SELECT datum,SUM(gesamt) AS tag_summe,SUM(steuer) AS steuer_summe
        FROM eintraege GROUP BY datum ORDER BY datum
    """).fetchall()
    data=[]
    prev=None
    for r in rows:
        s=float(r["tag_summe"] or 0)
        diff=None if prev is None else s-prev
        data.append({
            "datum":r["datum"],"tag_summe":s,
            "steuer_summe":float(r["steuer_summe"] or 0),
            "diff":diff,
            "pro_person":None if diff is None else diff/6
        })
        prev=s
    gesamt=sum(d["tag_summe"] for d in data)
    gesamt_st=sum(d["steuer_summe"] for d in data)
    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Admin</title></head>
<body class="container py-4">
<h3>Gesamtsummen</h3>
<table class="table table-bordered">
<thead><tr><th>Datum</th><th>Gesamtsumme</th><th>Differenz</th><th>Umsatz/Person</th><th>Steuer</th></tr></thead>
<tbody>
{% for r in rows %}
<tr><td>{{r.datum}}</td><td>{{'%.2f' % r.tag_summe}}</td>
<td>{% if r.diff is not none %}{{'%.2f' % r.diff}}{% endif %}</td>
<td>{% if r.pro_person is not none %}{{'%.2f' % r.pro_person}}{% endif %}</td>
<td>{{'%.2f' % r.steuer_summe}}</td></tr>{% endfor %}
</tbody>
<tfoot>
<tr class="table-secondary"><th>GESAMT BRUTTO</th><th>{{'%.2f' % gesamt}}</th><th></th><th>{{'%.2f' % (gesamt/6)}}</th><th>{{'%.2f' % gesamt_st}}</th></tr>
<tr class="table-dark"><th>GESAMT NACH STEUER</th><th>{{'%.2f' % (gesamt-gesamt_st)}}</th><th></th><th>{{'%.2f' % ((gesamt-gesamt_st)/6)}}</th><th></th></tr>
</tfoot>
</table>
<a href="{{ url_for('export_excel') }}" class="btn btn-primary">Excel Export</a>
</body></html>
""", rows=data, gesamt=gesamt, gesamt_st=gesamt_st)

@app.route("/export_excel")
def export_excel():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("SELECT datum,SUM(gesamt),SUM(steuer) FROM eintraege GROUP BY datum ORDER BY datum").fetchall()
    wb=openpyxl.Workbook(); ws=wb.active
    ws.append(["Datum","Gesamtsumme","Differenz","Umsatz/Person","Steuer"])
    prev=None
    for r in rows:
        s=float(r[1] or 0); st=float(r[2] or 0)
        diff=None if prev is None else s-prev
        pro=None if diff is None else diff/6
        ws.append([r[0],s,"" if diff is None else diff,"" if pro is None else pro,st])
        prev=s
    out=BytesIO(); wb.save(out); out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f"Wiesn25_{date.today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ----------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
