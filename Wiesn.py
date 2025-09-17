import os, sqlite3
from pathlib import Path
from datetime import date, timedelta, datetime
from io import BytesIO
from flask import (
    Flask, request, redirect, url_for,
    render_template_string, session, g,
    send_file, flash, get_flashed_messages
)
import openpyxl

# ---------------------------------------------------------------------
# Konfiguration & Helfer
# ---------------------------------------------------------------------
SECRET_KEY      = os.getenv("SECRET_KEY", "change-me")
ADMIN_PASSWORT  = os.getenv("ADMIN_PASSWORD", "Ramona")
DATABASE_PATH   = os.getenv("DATABASE_PATH", "verkauf.db")
PREIS_BIER      = float(os.getenv("PREIS_BIER", "14.01").replace(",", "."))
PREIS_ALKOHOLFR = float(os.getenv("PREIS_ALKOHOLFREI", "6.10").replace(",", "."))
PREIS_HENDL     = float(os.getenv("PREIS_HENDL", "22.30").replace(",", "."))

# Bearbeitbare Tage und Zeitfenster
DATA_START   = date(2025, 9, 20)
DATA_END     = date(2025, 10, 5)
EDIT_START   = date(2025, 9, 18)
EDIT_END     = date(2025, 10, 7)

# Demo: Bearbeitung immer möglich
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

# Gewünschte Reihenfolge der Mitarbeiter
MITARBEITER = ["Florian", "Jonas", "Julia", "Regina", "Schorsch", "Toni"]

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------
def ensure_db_dir(path):
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)

def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        ensure_db_dir(DATABASE_PATH)
        db = g._db = sqlite3.connect(DATABASE_PATH, timeout=30.0, check_same_thread=False)
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
        steuer REAL DEFAULT 0,
        gespeichert INTEGER,
        UNIQUE(datum, mitarbeiter)
      )
    """)
    db.commit()

with app.app_context(): init_db()

# ---------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status":"ok","time":datetime.utcnow().isoformat()}

# ---------------------------------------------------------------------
# Login mit Countdown
# ---------------------------------------------------------------------
@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        name = request.form.get("name")
        admin_pw = request.form.get("admin_pw")
        if admin_pw and admin_pw == ADMIN_PASSWORT:
            session.clear(); session["admin"]=True
            return redirect(url_for("admin_view"))
        if name in MITARBEITER:
            session.clear(); session["name"]=name
            return redirect(url_for("eingabe", datum=str(date.today())))
        flash("Bitte Mitarbeiter wählen oder Admin-Passwort eingeben.")
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
  .countdown{ font-size:1.4rem; font-weight:700; }
</style>
</head>
<body class="d-flex flex-column justify-content-center align-items-center min-vh-100 p-3">
<div class="container" style="max-width:980px;">
  <div class="text-center mb-4">
    <h1 class="display-6 fw-bold">Willkommen zur Wiesn-Abrechnung</h1>
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
        <div class="text-center my-2"><span class="text-muted">oder</span></div>
        <div class="mb-3">
          <label class="form-label">Admin Passwort</label>
          <input type="password" class="form-control" name="admin_pw" autocomplete="current-password">
        </div>
        <button class="btn btn-primary w-100">Einloggen</button>
      </form>
      <p class="text-center text-light mt-3 small">Bearbeitung möglich zwischen 18.09. und 07.10.</p>
      {% if demo_mode %}
        <div class="alert alert-info small mt-3 mb-0">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.</div>
      {% endif %}
    </div>
  </div>
</div>
<script>
  const deadline=new Date("2025-10-05T23:00:00");
  function tick(){
    const diff=Math.max(0,(deadline-new Date())/1000);
    const d=Math.floor(diff/86400);
    const h=Math.floor(diff%86400/3600);
    const m=Math.floor(diff%3600/60);
    document.getElementById('countdown').textContent=`Noch ${d} Tage ${h} Std ${m} Min`;
  }
  tick(); setInterval(tick,60000);
</script>
</body></html>
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ---------------------------------------------------------------------
# Eingabe
# ---------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET","POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"): return redirect(url_for("login"))
    user = session.get("name","ADMIN")
    datum_obj = date.fromisoformat(datum)
    wtag = datum_obj.weekday()
    im_edit = DEMO_MODE or (EDIT_START<=date.today()<=EDIT_END and DATA_START<=datum_obj<=DATA_END)

    db = get_db()
    row = db.execute("SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",(datum,user)).fetchone()
    action = request.form.get("action")

    # speichern
    if request.method=="POST" and action=="save" and im_edit and (not row or row["gespeichert"]==0):
        if datum_obj == DATA_START:
            summe_start = float(request.form.get("summe_start") or 0)
        else:
            if request.form.get("allow_edit_summe_start")=="1":
                summe_start = float(request.form.get("summe_start") or 0)
            else:
                vortag = datum_obj - timedelta(days=1)
                r = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                               (vortag.isoformat(),user)).fetchone()
                summe_start = float(r["tagessumme"] if r else 0)
        bar = float(request.form.get("bar") or 0)
        bier = int(request.form.get("bier") or 0)
        alkoholfrei = int(request.form.get("alkoholfrei") or 0)
        hendl = int(request.form.get("hendl") or 0)
        steuer = float(request.form.get("steuer") or 0) if wtag==2 else 0
        gesamt = bar + bier*PREIS_BIER + alkoholfrei*PREIS_ALKOHOLFR + hendl*PREIS_HENDL
        tagessumme = gesamt - float(request.form.get("bar_entnommen") or 0)
        if row:
            db.execute("""UPDATE eintraege SET summe_start=?,bar=?,bier=?,alkoholfrei=?,
                hendl=?,gesamt=?,bar_entnommen=?,tagessumme=?,steuer=?,gespeichert=1 WHERE id=?""",
                (summe_start,bar,bier,alkoholfrei,hendl,gesamt,
                 float(request.form.get("bar_entnommen") or 0),
                 tagessumme,steuer,row["id"]))
        else:
            db.execute("""INSERT INTO eintraege
                (datum,mitarbeiter,summe_start,bar,bier,alkoholfrei,hendl,gesamt,bar_entnommen,tagessumme,steuer,gespeichert)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                (datum,user,summe_start,bar,bier,alkoholfrei,hendl,
                 gesamt,float(request.form.get("bar_entnommen") or 0),tagessumme,steuer))
        db.commit()
        flash("Gespeichert ✔︎")
        return redirect(url_for("eingabe", datum=datum))

    if request.method=="POST" and action=="unlock" and row:
        entered=request.form.get("edit_pw","")
        if session.get("admin") and entered==ADMIN_PASSWORT:
            db.execute("UPDATE eintraege SET gespeichert=0 WHERE id=?", (row["id"],)); db.commit()
            flash("Eintrag entsperrt 🔓")
        return redirect(url_for("eingabe", datum=datum))

    # Anzeige
    if row:
        vals=dict(row)
    else:
        vortag = datum_obj - timedelta(days=1)
        r = db.execute("SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                       (vortag.isoformat(),user)).fetchone()
        vals={"summe_start": (0 if datum_obj==DATA_START else float(r["tagessumme"] if r else 0)),
              "bar":0,"bier":0,"alkoholfrei":0,"hendl":0,"gesamt":0,
              "bar_entnommen":0,"tagessumme":0,"steuer":0,"gespeichert":0}
    return render_template_string("""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Eingabe</title>
<style>
  .calc-field{background:#f1f3f5;}
  .readonly{background:#e9ecef;}
  .editable{background:#fff3cd;}
</style>
</head>
<body class="bg-light">
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3>Eingabe für {{datum}} – {{user}}</h3>
    <a href="{{ url_for('login') }}" class="btn btn-primary text-white">Zur Startseite</a>
  </div>
  <div class="d-flex gap-2 mb-3">
    <a href="{{ url_for('eingabe', datum=(dobj - timedelta(days=1)).isoformat()) }}" class="btn btn-outline-primary">← Vortag</a>
    <a href="{{ url_for('eingabe', datum=(dobj + timedelta(days=1)).isoformat()) }}" class="btn btn-outline-primary">Folgetag →</a>
    <input type="date" class="form-control" style="max-width:220px"
           value="{{datum}}" onchange="window.location='/eingabe/'+this.value">
    {% if wtag==2 %}<span class="badge bg-info">Mittwoch (Steuer sichtbar)</span>{% endif %}
  </div>
  {% for m in get_flashed_messages() %}
    <div class="alert alert-success">{{ m }}</div>
  {% endfor %}
  <form method="post" oninput="berechne()" class="card p-4 shadow-sm">
    <input type="hidden" name="action" value="save">
    <input type="hidden" name="allow_edit_summe_start"
           value="{{ 1 if (im_edit and (dobj==data_start or not vals['gespeichert'])) else 0 }}">
    <div class="row g-3">
      <div class="col-md-6">
        <label class="form-label">Summe Start (€)</label>
        <input type="number" step="0.01" name="summe_start" value="{{vals['summe_start']}}"
               class="form-control {% if im_edit and (dobj==data_start or not vals['gespeichert']) %}editable{% else %}readonly{% endif %}"
               {% if not (im_edit and (dobj==data_start or not vals['gespeichert'])) %}readonly{% endif %}>
      </div>
      <div class="col-md-6">
        <label class="form-label">Bar (€)</label>
        <input type="number" step="0.01" id="bar" name="bar" value="{{vals['bar']}}" min="0"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
      </div>
      <div class="col-md-4">
        <label class="form-label d-block">Bier</label>
        <div class="input-group">
          <span class="input-group-text">Anzahl</span>
          <input type="number" id="bier" name="bier" value="{{vals['bier']}}" min="0"
                 class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
        </div>
      </div>
      <div class="col-md-4">
        <label class="form-label d-block">Alkoholfrei</label>
        <div class="input-group">
          <span class="input-group-text">Anzahl</span>
          <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{vals['alkoholfrei']}}" min="0"
                 class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
        </div>
      </div>
      <div class="col-md-4">
        <label class="form-label d-block">Hendl</label>
        <div class="input-group">
          <span class="input-group-text">Anzahl</span>
          <input type="number" id="hendl" name="hendl" value="{{vals['hendl']}}" min="0"
                 class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
                 {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
        </div>
      </div>
      {% if wtag==2 %}
      <div class="col-md-6">
        <label class="form-label">Steuer (€)</label>
        <input type="number" step="0.01" id="steuer" name="steuer" value="{{vals['steuer']}}" min="0"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
      </div>
      {% endif %}
      <div class="col-md-6">
        <label class="form-label">Bar entnommen (€)</label>
        <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{vals['bar_entnommen']}}" min="0"
               class="form-control {% if im_edit and not vals['gespeichert'] %}editable{% else %}readonly{% endif %}"
               {% if not im_edit or vals['gespeichert'] %}readonly{% endif %}>
      </div>
      <div class="col-md-6">
        <label class="form-label">Gesamt (€)</label>
        <input type="number" step="0.01" id="gesamt" readonly class="form-control calc-field"
               value="{{'%.2f' % vals['gesamt']}}">
      </div>
      <div class="col-md-6">
        <label class="form-label">Tagessumme (€)</label>
        <input type="number" step="0.01" id="tagessumme" readonly class="form-control calc-field"
               value="{{'%.2f' % vals['tagessumme']}}">
      </div>
    </div>
    <div class="mt-3">
      {% if im_edit and not vals['gespeichert'] %}
        <button class="btn btn-success">Speichern</button>
      {% else %}
        <div class="alert alert-secondary mt-2 mb-0">Bearbeitung gesperrt. Zum Ändern bitte entsperren.</div>
      {% endif %}
    </div>
  </form>
  {% if vals['gespeichert'] %}
  <div class="card mt-4 p-3">
    <h5>Eintrag bearbeiten (entsperren)</h5>
    <form method="post" class="d-flex gap-2">
      <input type="hidden" name="action" value="unlock">
      <input type="password" name="edit_pw" class="form-control" placeholder="Passwort" required>
      <button class="btn btn-warning">Editieren freischalten</button>
    </form>
  </div>
  {% endif %}
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
  document.getElementById("gesamt").value = gesamt.toFixed(2);
  document.getElementById("tagessumme").value = tag.toFixed(2);
}
window.addEventListener('load', berechne);
</script>
</body></html>
    """, datum=datum, user=user, vals=vals,
         im_edit=im_edit, dobj=datum_obj,
         wtag=wtag, data_start=DATA_START,
         preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFR, preis_hendl=PREIS_HENDL)

# ---------------------------------------------------------------------
# Admin Übersicht + Excel
# ---------------------------------------------------------------------
@app.route("/admin")
def admin_view():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""
      SELECT datum, SUM(gesamt) AS tag_summe, SUM(steuer) AS steuer_summe
      FROM eintraege GROUP BY datum ORDER BY datum
    """).fetchall()
    result=[]; prev=None
    for r in rows:
        s=float(r["tag_summe"] or 0)
        diff=None if prev is None else s - prev
        pro_person=None if diff is None else diff/6
        result.append({"datum":r["datum"],"summe":s,"diff":diff,
                       "pro_person":pro_person,"steuer":float(r["steuer_summe"] or 0)})
        prev=s
    gesamt=sum(r["summe"] for r in result)
    gesamt_diff=sum(r["diff"] for r in result if r["diff"] is not None)
    gesamt_pro=sum(r["pro_person"] for r in result if r["pro_person"] is not None)
    gesamt_steuer=sum(r["steuer"] for r in result)
    netto=gesamt-gesamt_steuer
    return render_template_string("""
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<title>Admin</title></head>
<body class="bg-light">
<div class="container py-4">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h3>Gesamtsummen pro Tag</h3>
    <a href="{{ url_for('login') }}" class="btn btn-secondary">Abmelden</a>
  </div>
  <table class="table table-bordered table-striped">
    <thead class="table-light">
      <tr><th>Datum</th><th>Gesamtsumme (€)</th><th>Differenz</th><th>Umsatz pro Person</th><th>Steuer</th></tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{r.datum}}</td>
        <td>{{"%.2f"|format(r.summe)}}</td>
        <td>{% if r.diff is not none %}{{"%.2f"|format(r.diff)}}{% else %}-{% endif %}</td>
        <td>{% if r.pro_person is not none %}{{"%.2f"|format(r.pro_person)}}{% else %}-{% endif %}</td>
        <td>{{"%.2f"|format(r.steuer)}}</td>
      </tr>
      {% endfor %}
    </tbody>
    <tfoot class="table-secondary">
      <tr>
        <th>GESAMT</th>
        <th>{{"%.2f"|format(gesamt)}}</th>
        <th>{{"%.2f"|format(gesamt_diff)}}</th>
        <th>{{"%.2f"|format(gesamt_pro)}}</th>
        <th>{{"%.2f"|format(gesamt_steuer)}}</th>
      </tr>
      <tr>
        <th colspan="5">Netto (nach Steuer): {{ "%.2f"|format(netto) }}</th>
      </tr>
    </tfoot>
  </table>
  <form action="{{ url_for('export_excel') }}" method="get">
    <button class="btn btn-primary">📥 Excel Export</button>
  </form>
</div></body></html>
    """, rows=result, gesamt=gesamt, gesamt_diff=gesamt_diff,
         gesamt_pro=gesamt_pro, gesamt_steuer=gesamt_steuer, netto=netto)

@app.route("/export_excel")
def export_excel():
    if not session.get("admin"): return redirect(url_for("login"))
    db = get_db()
    rows = db.execute("""
      SELECT datum, SUM(gesamt) AS summe, SUM(steuer) AS steuer
      FROM eintraege GROUP BY datum ORDER BY datum
    """).fetchall()
    wb = openpyxl.Workbook(); ws=wb.active; ws.title="Gesamtsummen"
    ws.append(["Datum","Gesamt","Differenz","Umsatz/Person","Steuer"])
    prev=None
    for r in rows:
        s=float(r["summe"] or 0)
        diff=None if prev is None else s-prev
        per=None if diff is None else diff/6
        ws.append([r["datum"],s,"" if diff is None else diff,"" if per is None else per,float(r["steuer"] or 0)])
        prev=s
    ws.append([])
    ws.append(["GESAMT", sum(float(r["summe"] or 0) for r in rows)])
    out=BytesIO(); wb.save(out); out.seek(0)
    fname=f"Wiesn25_Gesamt_{date.today().isoformat()}.xlsx"
    return send_file(out, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
