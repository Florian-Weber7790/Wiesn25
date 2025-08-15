import os
import sqlite3
from pathlib import Path
from datetime import date, timedelta, datetime
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, g, send_file
)
import openpyxl
from io import BytesIO

# ------------------------------------------------------------------------------
# Hilfsfunktionen für ENV
# ------------------------------------------------------------------------------
def _get_env(key, default=None):
    return os.getenv(key, default)

def _get_env_float(key, default):
    try:
        return float(os.getenv(key, str(default)).replace(",", "."))
    except ValueError:
        return default

def _get_env_date(key, default_iso):
    raw = os.getenv(key, default_iso)
    return date.fromisoformat(raw)

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
        "MITARBEITER",
        "Julia,Regina,Florian,Schorsch,Toni,Jonas"
    ).split(",") if m.strip()
]

# Bearbeitbare Tage
EDIT_START = _get_env_date("EDIT_START", "2025-09-20")
EDIT_END   = _get_env_date("EDIT_END", "2025-10-05")

# Zeitraum, in dem Bearbeitung erlaubt ist
EDIT_BEARBEITBAR_START = _get_env_date("EDIT_BEARBEITBAR_START", "2025-09-18")
EDIT_BEARBEITBAR_ENDE  = _get_env_date("EDIT_BEARBEITBAR_ENDE", "2025-10-07")


# Passwörter aus ENV einlesen
def _parse_password_map(default_names):
    # Erwartet ENV: MITARBEITER_PASSWORDS="Julia:pw1,Regina:pw2,Florian:pw3"
    raw = os.getenv("MITARBEITER_PASSWORDS", "")
    mp = {}
    for chunk in raw.split(","):
        if ":" in chunk:
            name, pw = chunk.split(":", 1)
            name, pw = name.strip(), pw.strip()
            if name and pw:
                mp[name] = pw
    # Falls für manche Namen nichts in ENV steht, lege Dummy-PWs
    for n in default_names:
        mp.setdefault(n, f"{n.lower()}123")
    return mp

MITARBEITER_PASSWOERTER = _parse_password_map(MITARBEITER)

# Demo-Modus: auf Render zum Testen DEMO_MODE=1 setzen
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"
if DEMO_MODE:
    today = date.today()
    DATA_START = today - timedelta(days=5)   # gültige Daten (Anzeige & Speichern)
    DATA_END   = today + timedelta(days=5)
    EDIT_START = today - timedelta(days=2)   # Bearbeitungsfenster (heutiges Datum)
    EDIT_END   = today + timedelta(days=2)
else:
    # Produktivregeln
    DATA_START = _get_env_date("DATA_START", "2025-09-20")  # nur diese Tage dürfen gespeichert werden
    DATA_END   = _get_env_date("DATA_END",   "2025-10-05")
    EDIT_START = _get_env_date("EDIT_START", "2025-09-18")  # nur in diesem Fenster darf generell bearbeitet werden
    EDIT_END   = _get_env_date("EDIT_END",   "2025-10-07")

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
        db = g._database = sqlite3.connect(
            DATABASE_PATH, timeout=30.0, check_same_thread=False
        )
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
# Healthcheck (für Render)
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

        if admin_pw == ADMIN_PASSWORT:
            session["admin"] = True
            return redirect(url_for("admin_view"))

        if name in MITARBEITER:
            session["name"] = name
            session["admin"] = False
            return redirect(url_for("eingabe", datum=str(date.today())))

    return render_template_string("""
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        select, input { padding: 5px; margin: 5px; }
        button { padding: 8px 12px; }
        </style>
        <h2>Login</h2>
        <form method="post">
            <label>Mitarbeiter:</label>
            <select name="name">
                <option value="">-- auswählen --</option>
                {% for m in mitarbeiter %}
                <option value="{{m}}">{{m}}</option>
                {% endfor %}
            </select>
            <br><br>
            <label>Oder Admin Passwort:</label>
            <input type="password" name="admin_pw" autocomplete="current-password">
            <br><br>
            <button type="submit">Login</button>
        </form>
        {% if demo_mode %}
          <p style="margin-top:1rem;color:#555;">Demo-Modus aktiv: Datenbereich = heute±5 Tage, Bearbeitung = heute±2 Tage.</p>
        {% endif %}
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)

# ------------------------------------------------------------------------------
# Eingabe
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session:
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()  # 0=Montag, 2=Mittwoch etc.
    im_edit_zeitraum = EDIT_BEARBEITBAR_START <= datum_obj <= EDIT_BEARBEITBAR_ENDE
    im_erlaubten_tag = EDIT_START <= datum_obj <= EDIT_END

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, session["name"])
    ).fetchone()

    # Speichern nur wenn erlaubt
    if request.method == "POST" and im_edit_zeitraum and im_erlaubten_tag and (not row or row["gespeichert"] == 0):
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
        steuer = float(request.form.get("steuer", 0) or 0) if wochentag == 2 else 0
        bar_entnommen = float(request.form.get("bar_entnommen", 0) or 0)

        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL) - steuer
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
            """, (datum, session["name"], summe_start, bar, bier, alkoholfrei,
                  hendl, steuer, gesamt, bar_entnommen, tagessumme))
        db.commit()
        return redirect(url_for("login"))  # nach Speichern zurück Startseite

    gespeichert = row["gespeichert"] if row else 0
    if row:
        summe_start = row["summe_start"]
        bar = row["bar"]
        bier = row["bier"]
        alkoholfrei = row["alkoholfrei"]
        hendl = row["hendl"]
        steuer = row["steuer"] if "steuer" in row.keys() else 0
        bar_entnommen = row["bar_entnommen"]
        gesamt = row["gesamt"]
        tagessumme = row["tagessumme"]
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
        gesamt = tagessumme = 0

    vortag_link = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()

    return render_template_string("""
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        input { padding: 5px; margin: 5px; }
        .editable { background-color: orange; }
        .readonly { background-color: #ddd; }
        .calc-field { background-color: #eee; }
        button { padding: 8px 12px; margin-top: 10px; }
        a.nav-btn { padding: 8px 12px; margin: 5px; background-color: #ccc; text-decoration: none; border-radius: 5px; }
        </style>

        <script>
        function berechne() {
            let preisBier = {{preis_bier}};
            let preisAlk = {{preis_alk}};
            let preisHendl = {{preis_hendl}};

            let bar = parseFloat(document.getElementById("bar").value) || 0;
            let bier = parseInt(document.getElementById("bier").value) || 0;
            let alkoholfrei = parseInt(document.getElementById("alkoholfrei").value) || 0;
            let hendl = parseInt(document.getElementById("hendl").value) || 0;
            let steuer = parseFloat(document.getElementById("steuer")?.value) || 0;
            let barEntnommen = parseFloat(document.getElementById("bar_entnommen").value) || 0;

            let gesamt = bar + (bier * preisBier) + (alkoholfrei * preisAlk) + (hendl * preisHendl) - steuer;
            let tagessumme = gesamt - barEntnommen;

            document.getElementById("gesamt").value = gesamt.toFixed(2);
            document.getElementById("tagessumme").value = tagessumme.toFixed(2);
        }
        window.addEventListener('load', berechne);
        </script>

        <h2>Eingabe für {{datum}} - {{name}}</h2>
        <div>
            <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="nav-btn">← Vortag</a>
            <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="nav-btn">Folgetag →</a>
            <input type="date" id="datumsauswahl" value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
        </div>

        <form method="post" oninput="berechne()">
            Summe Start:
            <input type="number" step="0.01" name="summe_start" value="{{summe_start}}" 
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>

            Bar (€):
            <input type="number" step="0.01" id="bar" name="bar" value="{{bar}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>

            Bier (Anzahl):
            <input type="number" id="bier" name="bier" value="{{bier}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>

            Alkoholfrei (Anzahl):
            <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{alkoholfrei}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>

            Hendl (Anzahl):
            <input type="number" id="hendl" name="hendl" value="{{hendl}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>

            {% if wochentag == 2 %}
            Steuer (€):
            <input type="number" step="0.01" id="steuer" name="steuer" value="{{steuer}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or not im_erlaubten_tag or gespeichert %}readonly{% endif %}><br><br>
            {% endif %}

            Gesamt (€):
            <input type="number" step="0.01" id="gesamt" value="{{gesamt}}" readonly class="calc-field"><br><br>

            Bar entnommen (€):
            <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{bar_entnommen}}" min="0"
                   class="{% if im_edit_zeitraum and im_erlaubten_tag %}editable{% else %}readonly{% endif %}"><br><br>

            Tagessumme (€):
            <input type="number" step="0.01" id="tagessumme" value="{{tagessumme}}" readonly class="calc-field"><br><br>

            {% if im_edit_zeitraum and im_erlaubten_tag and not gespeichert %}
            <button type="submit">Speichern</button>
            {% endif %}
        </form>
    """, datum=datum, name=session["name"], summe_start=summe_start, bar=bar, bier=bier,
       alkoholfrei=alkoholfrei, hendl=hendl, steuer=steuer, bar_entnommen=bar_entnommen,
       gesamt=gesamt, tagessumme=tagessumme,
       gespeichert=gespeichert,
       preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFREI, preis_hendl=PREIS_HENDL,
       vortag_link=vortag_link, folgetag_link=folgetag_link,
       im_edit_zeitraum=im_edit_zeitraum, im_erlaubten_tag=im_erlaubten_tag,
       edit_start=EDIT_START.isoformat(), edit_end=EDIT_END.isoformat(),
       wochentag=wochentag)

# ------------------------------------------------------------------------------
# Admin-Ansicht mit Differenz
# ------------------------------------------------------------------------------
@app.route("/admin")
def admin_view():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute("""
        SELECT datum, SUM(gesamt) AS tag_summe
        FROM eintraege
        WHERE gesamt IS NOT NULL
        GROUP BY datum
        HAVING SUM(gesamt) > 0
        ORDER BY datum
    """).fetchall()

    rows_with_diff = []
    prev_sum = None
    for r in rows:
        current = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else current - prev_sum
        rows_with_diff.append({
            "datum": r["datum"],
            "tag_summe": current,
            "diff": diff
        })
        prev_sum = current

    gesamt_summe = sum(r["tag_summe"] for r in rows_with_diff)

    return render_template_string("""
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        table { border-collapse: collapse; width: 65%; }
        th, td { border: 1px solid #ccc; padding: 5px; text-align: center; }
        th { background-color: #eee; }
        </style>
        <h2>Gesamtsummen pro Tag</h2>
        <table>
            <tr>
                <th>Datum</th>
                <th>Gesamtsumme (€)</th>
                <th>Differenz zum Vortag (€)</th>
            </tr>
            {% for r in rows %}
            <tr>
                <td>{{ r.datum }}</td>
                <td>{{ "%.2f"|format(r.tag_summe) }}</td>
                <td>{% if r.diff is not none %}{{ "%.2f"|format(r.diff) }}{% else %}-{% endif %}</td>
            </tr>
            {% endfor %}
        </table>
        <h3>GESAMTSUMME ALLE TAGE: {{ "%.2f"|format(gesamt_summe) }}</h3>
        <br>
        <form action="{{ url_for('export_excel') }}" method="get">
            <button type="submit">📥 Export als Excel</button>
        </form>
    """, rows=rows_with_diff, gesamt_summe=gesamt_summe)

# ------------------------------------------------------------------------------
# Excel-Export mit Differenz + Dateiname
# ------------------------------------------------------------------------------
@app.route("/export_excel")
def export_excel():
    if not session.get("admin"):
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute("""
        SELECT datum, SUM(gesamt) AS tag_summe
        FROM eintraege
        WHERE gesamt IS NOT NULL
        GROUP BY datum
        HAVING SUM(gesamt) > 0
        ORDER BY datum
    """).fetchall()

    data = []
    prev_sum = None
    for r in rows:
        current = float(r["tag_summe"] or 0)
        diff = None if prev_sum is None else current - prev_sum
        data.append((r["datum"], current, diff))
        prev_sum = current

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamtsummen"
    ws.append(["Datum", "Gesamtsumme (€)", "Differenz zum Vortag (€)"])
    for d, s, diff in data:
        ws.append([d, s, "" if diff is None else diff])
    ws.append([])
    ws.append(["GESAMTSUMME ALLE TAGE", sum(s for _, s, _ in data)])

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
# Start (lokal)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
