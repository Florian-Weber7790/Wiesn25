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
# Konfiguration (über ENV variabel)
# ------------------------------------------------------------------------------
def _get_env(key, default=None):
    v = os.getenv(key, default)
    return v

def _get_env_float(key, default):
    try:
        return float(os.getenv(key, str(default)).replace(",", "."))
    except ValueError:
        return default

def _get_env_date(key, default_iso):
    raw = os.getenv(key, default_iso)
    return date.fromisoformat(raw)

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

# Zeitraum für Daten, die erfasst werden dürfen
DATA_START = _get_env_date("DATA_START", "2025-09-20")
DATA_END   = _get_env_date("DATA_END", "2025-10-05")

# Zeitraum, in dem das Formular bearbeitet werden darf
EDIT_START = _get_env_date("EDIT_START", "2025-09-18")
EDIT_END   = _get_env_date("EDIT_END", "2025-10-07")

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
        # Bessere Concurrent-Reads/Writes
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

# Beim Import (auch unter Gunicorn) sicherstellen, dass die DB-Struktur existiert
with app.app_context():
    init_db()

# ------------------------------------------------------------------------------
# Health-Check (für Render)
# ------------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}

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
    """, mitarbeiter=MITARBEITER)

# ------------------------------------------------------------------------------
# Eingabeformular
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session:
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)

    # Prüfen, ob Speichern erlaubt ist
    bearbeitung_erlaubt = (
        EDIT_START <= date.today() <= EDIT_END
        and DATA_START <= datum_obj <= DATA_END
    )

    db = get_db()
    row = db.execute(
        "SELECT * FROM eintraege WHERE datum=? AND mitarbeiter=?",
        (datum, session["name"])
    ).fetchone()

    if request.method == "POST" and bearbeitung_erlaubt and (not row or row["gespeichert"] == 0):
        if datum_obj == DATA_START:
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
            """, (datum, session["name"], summe_start, bar, bier, alkoholfrei, hendl, steuer,
                  gesamt, bar_entnommen, tagessumme))
        db.commit()
        return redirect(url_for("eingabe", datum=datum))

    gespeichert = row["gespeichert"] if row else 0
    if row:
        summe_start = row["summe_start"]
        bar = row["bar"]
        bier = row["bier"]
        alkoholfrei = row["alkoholfrei"]
        hendl = row["hendl"]
        steuer = row["steuer"] if row["steuer"] is not None else 0
        bar_entnommen = row["bar_entnommen"]
    else:
        if datum_obj == DATA_START:
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

    wochentag = datum_obj.strftime("%A")

    return render_template_string("""
        <h2>Eingabe für {{datum}} - {{name}}</h2>
        <div>
            <a href="{{ url_for('eingabe', datum=vortag_link) }}">← Vortag</a>
            <a href="{{ url_for('eingabe', datum=folgetag_link) }}">Folgetag →</a>
            <input type="date" id="datumsauswahl" value="{{datum}}" onchange="springeZuDatum()">
        </div>

        <form method="post" oninput="berechne()">
            Summe Start:
            <input type="number" step="0.01" name="summe_start" value="{{summe_start}}"
                   {% if not bearbeitung_erlaubt or datum != data_start or gespeichert %}readonly{% endif %}><br><br>

            Bar (€):
            <input type="number" step="0.01" id="bar" name="bar" value="{{bar}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>

            Bier (Anzahl):
            <input type="number" id="bier" name="bier" value="{{bier}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>

            Alkoholfrei (Anzahl):
            <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{alkoholfrei}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>

            Hendl (Anzahl):
            <input type="number" id="hendl" name="hendl" value="{{hendl}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>

            {% if wochentag == "Wednesday" %}
            Steuer (€):
            <input type="number" step="0.01" name="steuer" value="{{steuer}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>
            {% endif %}

            Gesamt (€):
            <input type="number" step="0.01" id="gesamt" readonly><br><br>

            Bar entnommen (€):
            <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{bar_entnommen}}"
                   {% if not bearbeitung_erlaubt or gespeichert %}readonly{% endif %}><br><br>

            Tagessumme (€):
            <input type="number" step="0.01" id="tagessumme" readonly><br><br>

            {% if bearbeitung_erlaubt and not gespeichert %}
            <button type="submit">Speichern</button>
            {% else %}
            <p><b>Bearbeitung nur vom {{edit_start}} bis {{edit_end}} und für Daten vom {{data_start}} bis {{data_end}} möglich.</b></p>
            {% endif %}
        </form>
    """, datum=datum, name=session["name"], summe_start=summe_start, bar=bar, bier=bier,
       alkoholfrei=alkoholfrei, hendl=hendl, steuer=steuer, bar_entnommen=bar_entnommen,
       gespeichert=gespeichert, vortag_link=vortag_link, folgetag_link=folgetag_link,
       bearbeitung_erlaubt=bearbeitung_erlaubt, data_start=DATA_START.isoformat(),
       data_end=DATA_END.isoformat(), edit_start=EDIT_START.isoformat(), edit_end=EDIT_END.isoformat(),
       wochentag=wochentag)

# ------------------------------------------------------------------------------
# Admin-Ansicht – mit Differenz zum Vortag
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

    # Differenzen berechnen
    rows_with_diff = []
    prev_sum = None
    for r in rows:
        current_sum = r["tag_summe"] or 0
        diff = None
        if prev_sum is not None:
            diff = current_sum - prev_sum
        rows_with_diff.append({
            "datum": r["datum"],
            "tag_summe": current_sum,
            "diff": diff
        })
        prev_sum = current_sum

    gesamt_summe = sum(r["tag_summe"] for r in rows_with_diff if r["tag_summe"] is not None)

    return render_template_string("""
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
                <td>{{ "%.2f"|format(r.tag_summe or 0) }}</td>
                <td>
                    {% if r.diff is not none %}
                        {{ "%.2f"|format(r.diff) }}
                    {% else %}
                        -
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
        <h3>GESAMTSUMME ALLE TAGE: {{"%.2f"|format(gesamt_summe)}}</h3>
        <br>
        <form action="{{ url_for('export_excel') }}" method="get">
            <button type="submit">📥 Export als Excel</button>
        </form>
    """, rows=rows_with_diff, gesamt_summe=gesamt_summe)


# ------------------------------------------------------------------------------
# Excel-Export
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

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gesamtsummen"
    ws.append(["Datum", "Gesamtsumme (€)"])
    for row in rows:
        ws.append([row["datum"], float(row["tag_summe"] or 0)])
    ws.append([])
    ws.append(["GESAMTSUMME ALLE TAGE", sum(float(row["tag_summe"] or 0) for row in rows)])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    # Dateiname mit heutigem Tagesdatum (YYYY-MM-DD)
    today_str = date.today().isoformat()               # z.B. 2025-08-15
    filename = f"Wiesn25_Gesamt_{today_str}.xlsx"

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
    # Lokal mit Flask Dev-Server starten (für Render nutzt du Gunicorn, siehe unten)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)

