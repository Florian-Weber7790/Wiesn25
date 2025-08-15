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
DATA_START = _get_env_date("DATA_START", "2025-09-20")        # Tage, die bearbeitet werden dürfen
DATA_END   = _get_env_date("DATA_END",   "2025-10-05")
EDIT_WINDOW_START = _get_env_date("EDIT_WINDOW_START", "2025-09-18")  # Zeitraum, in dem Bearbeitung grundsätzlich erlaubt ist
EDIT_WINDOW_END   = _get_env_date("EDIT_WINDOW_END",   "2025-10-07")

# Demo-Modus: erlaubt alles jederzeit
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
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        select, input { padding: 6px 8px; margin: 6px 0; }
        button { padding: 8px 12px; }
        </style>
        <h2>Login</h2>
        <form method="post">
            <label>Mitarbeiter:</label><br>
            <select name="name">
                <option value="">-- auswählen --</option>
                {% for m in mitarbeiter %}
                <option value="{{m}}">{{m}}</option>
                {% endfor %}
            </select>
            <br><br>
            <label>Oder Admin Passwort:</label><br>
            <input type="password" name="admin_pw" autocomplete="current-password">
            <br><br>
            <button type="submit">Login</button>
        </form>
        {% if demo_mode %}
          <p style="margin-top:1rem;color:#555;">Demo-Modus aktiv: Bearbeitung jederzeit erlaubt.</p>
        {% endif %}
    """, mitarbeiter=MITARBEITER, demo_mode=DEMO_MODE)


# ------------------------------------------------------------------------------
# Eingabe (mit Entsperren per Mitarbeiter-Passwort)
# ------------------------------------------------------------------------------
@app.route("/eingabe/<datum>", methods=["GET", "POST"])
def eingabe(datum):
    if "name" not in session and not session.get("admin"):
        return redirect(url_for("login"))

    datum_obj = date.fromisoformat(datum)
    wochentag = datum_obj.weekday()  # 0=Mo, 2=Mi, 6=So

    # Bearbeitungslogik
    tag_im_erlaubten_bereich = DATA_START <= datum_obj <= DATA_END
    heute_im_bearb_fenster = EDIT_WINDOW_START <= date.today() <= EDIT_WINDOW_END

    if DEMO_MODE:
        im_edit_zeitraum = True
        tag_im_erlaubten_bereich = True
    else:
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
        if not im_edit_zeitraum:
            return "Freischalten derzeit nicht erlaubt.", 403
        if not row:
            return "Kein gespeicherter Eintrag zum Freischalten vorhanden.", 400

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

    # --- Speichern ---
    if request.method == "POST" and action == "save" and im_edit_zeitraum and (not row or row["gespeichert"] == 0):
        # summe_start: am ersten erlaubten Tag manuell, sonst vom Vortag
        if datum_obj == DATA_START:
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
            """, (datum, aktiver_user, summe_start, bar, bier, alkoholfrei, hendl, steuer,
                  gesamt, bar_entnommen, tagessumme))
        db.commit()

        if session.get("admin"):
            return redirect(url_for("admin_view"))
        return redirect(url_for("login"))

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
        if datum_obj == DATA_START:
            summe_start = 0
        else:
            vortag = datum_obj - timedelta(days=1)
            vortag_row = db.execute(
                "SELECT tagessumme FROM eintraege WHERE datum=? AND mitarbeiter=?",
                (vortag.isoformat(), aktiver_user)
            ).fetchone()
            summe_start = float(vortag_row["tagessumme"] if vortag_row else 0)
        bar = bier = alkoholfrei = hendl = steuer = bar_entnommen = 0
        gesamt = bar + (bier * PREIS_BIER) + (alkoholfrei * PREIS_ALKOHOLFREI) + (hendl * PREIS_HENDL) - steuer
        tagessumme = gesamt - bar_entnommen
        gespeichert = 0

    vortag_link = (datum_obj - timedelta(days=1)).isoformat()
    folgetag_link = (datum_obj + timedelta(days=1)).isoformat()
    unlock_error = session.pop("unlock_error", "")

    return render_template_string("""
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        input { padding: 5px; margin: 5px; }
        .editable { background-color: orange; }
        .readonly { background-color: #ddd; }
        .calc-field { background-color: #eee; }
        button { padding: 8px 12px; margin-top: 10px; }
        a.nav-btn { padding: 8px 12px; margin: 5px; background-color: #ccc; text-decoration: none; border-radius: 5px; }
        .error { color: #b00020; margin: 8px 0; }
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
            let steuerEl = document.getElementById("steuer");
            let steuer = steuerEl ? (parseFloat(steuerEl.value) || 0) : 0;
            let barEntnommen = parseFloat(document.getElementById("bar_entnommen").value) || 0;

            let gesamt = bar + (bier * preisBier) + (alkoholfrei * preisAlk) + (hendl * preisHendl) - steuer;
            let tagessumme = gesamt - barEntnommen;

            document.getElementById("gesamt").value = gesamt.toFixed(2);
            document.getElementById("tagessumme").value = tagessumme.toFixed(2);
        }
        window.addEventListener('load', berechne);
        </script>

        <h2>Eingabe für {{datum}} – {{name}}</h2>
        <div>
            <a href="{{ url_for('eingabe', datum=vortag_link) }}" class="nav-btn">← Vortag</a>
            <a href="{{ url_for('eingabe', datum=folgetag_link) }}" class="nav-btn">Folgetag →</a>
            <input type="date" id="datumsauswahl" value="{{datum}}" onchange="window.location.href='/eingabe/' + this.value">
        </div>

        {% if unlock_error %}
          <div class="error">{{ unlock_error }}</div>
        {% endif %}

        <form method="post" oninput="berechne()">
            <input type="hidden" name="action" value="save">

            Summe Start:
            <input type="number" step="0.01" name="summe_start" value="{{summe_start}}"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            Bar (€):
            <input type="number" step="0.01" id="bar" name="bar" value="{{bar}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            Bier (Anzahl):
            <input type="number" id="bier" name="bier" value="{{bier}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            Alkoholfrei (Anzahl):
            <input type="number" id="alkoholfrei" name="alkoholfrei" value="{{alkoholfrei}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            Hendl (Anzahl):
            <input type="number" id="hendl" name="hendl" value="{{hendl}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            {% if wochentag == 2 %}
            Steuer (€):
            <input type="number" step="0.01" id="steuer" name="steuer" value="{{steuer}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>
            {% endif %}

            Gesamt (€):
            <input type="number" step="0.01" id="gesamt" readonly class="calc-field" value="{{ '%.2f' % gesamt }}"><br><br>

            Bar entnommen (€):
            <input type="number" step="0.01" id="bar_entnommen" name="bar_entnommen" value="{{bar_entnommen}}" min="0"
                   class="{% if im_edit_zeitraum and not gespeichert %}editable{% else %}readonly{% endif %}"
                   {% if not im_edit_zeitraum or gespeichert %}readonly{% endif %}><br><br>

            Tagessumme (€):
            <input type="number" step="0.01" id="tagessumme" readonly class="calc-field" value="{{ '%.2f' % tagessumme }}"><br><br>

                        {% if im_edit_zeitraum and not gespeichert %}
              <button type="submit">Speichern</button>
            {% else %}
              <p><b>Bearbeitung gesperrt. Zum Ändern bitte entsperren.</b></p>
            {% endif %}
        </form>

        {% if gespeichert %}
          <hr>
          <h3>Eintrag bearbeiten (entsperren)</h3>
          <form method="post">
            <input type="hidden" name="action" value="unlock">
            <label>Passwort für {{name}}{% if admin %} / Admin{% endif %}:</label>
            <input type="password" name="edit_pw" autocomplete="current-password" required>
            <button type="submit">Editieren freischalten</button>
          </form>
        {% endif %}
    """,
        datum=datum,
        name=aktiver_user,
        admin=session.get("admin", False),
        summe_start=summe_start, bar=bar, bier=bier,
        alkoholfrei=alkoholfrei, hendl=hendl, steuer=steuer,
        bar_entnommen=bar_entnommen, gesamt=gesamt, tagessumme=tagessumme,
        gespeichert=gespeichert,
        preis_bier=PREIS_BIER, preis_alk=PREIS_ALKOHOLFREI, preis_hendl=PREIS_HENDL,
        vortag_link=vortag_link, folgetag_link=folgetag_link,
        im_edit_zeitraum=im_edit_zeitraum,
        wochentag=wochentag
    )


# ------------------------------------------------------------------------------
# Admin-Ansicht (mit Differenz zum Vortag) + Export
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

    # Differenz zum Vortag berechnen
    rows_with_diff = []
    prev = None
    for r in rows:
        s = float(r["tag_summe"] or 0)
        diff = None if prev is None else (s - prev)
        rows_with_diff.append({"datum": r["datum"], "tag_summe": s, "diff": diff})
        prev = s

    gesamt_summe = sum(r["tag_summe"] for r in rows_with_diff)

    return render_template_string("""
        <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        table { border-collapse: collapse; width: 70%; }
        th, td { border: 1px solid #ccc; padding: 6px; text-align: center; }
        th { background-color: #eee; }
        </style>
        <h2>Gesamtsummen pro Tag</h2>
        <table>
          <tr><th>Datum</th><th>Gesamtsumme (€)</th><th>Differenz zum Vortag (€)</th></tr>
          {% for r in rows %}
            <tr>
              <td>{{ r.datum }}</td>
              <td>{{ "%.2f"|format(r.tag_summe) }}</td>
              <td>{% if r.diff is not none %}{{ "%.2f"|format(r.diff) }}{% else %}-{% endif %}</td>
            </tr>
          {% endfor %}
        </table>
        <h3>GESAMTSUMME ALLE TAGE: {{ "%.2f"|format(gesamt_summe) }}</h3>
        <form action="{{ url_for('export_excel') }}" method="get" style="margin-top: 16px;">
          <button type="submit">📥 Export als Excel</button>
        </form>
    """, rows=rows_with_diff, gesamt_summe=gesamt_summe)


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
    prev = None
    for r in rows:
        s = float(r["tag_summe"] or 0)
        diff = None if prev is None else (s - prev)
        data.append((r["datum"], s, diff))
        prev = s

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
# Lokaler Start
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
