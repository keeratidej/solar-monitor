# ================================================================
#  app.py — Flask Backend  |  Solar / EV Monitor
#  ESP32 → POST /api/data  (voltage, current, power, pv, inv, flux)
# ================================================================
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import sqlite3, os, csv, io
from datetime import datetime

app = Flask(__name__)
CORS(app)
DB = os.path.join(os.path.dirname(__file__), "solar.db")

def init_db():
    with sqlite3.connect(DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS readings (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       DATETIME DEFAULT (datetime('now','localtime')),
            voltage  REAL, current REAL, power REAL,
            vpv1 REAL, cpv1 REAL, vpv2 REAL, cpv2 REAL,
            vinv REAL, cinv REAL, flux REAL
        )""")
        c.commit()
init_db()

@app.route("/")
def index():
    return send_file("dashboard.html")

KEYS = ["id","ts","voltage","current","power",
        "vpv1","cpv1","vpv2","cpv2","vinv","cinv","flux"]
def row2dict(r): return dict(zip(KEYS, r))

# ── POST /api/data ─────────────────────────────────────────────
@app.route("/api/data", methods=["POST"])
def recv_data():
    d = request.get_json(force=True, silent=True)
    if not d: return jsonify({"error":"bad json"}), 400
    fields = ["voltage","current","power","vpv1","cpv1",
              "vpv2","cpv2","vinv","cinv","flux"]
    vals = [float(d.get(f, 0)) for f in fields]
    with sqlite3.connect(DB) as c:
        c.execute("""INSERT INTO readings
            (voltage,current,power,vpv1,cpv1,vpv2,cpv2,vinv,cinv,flux)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", vals)
        c.commit()
    print(f"[{datetime.now():%H:%M:%S}] saved: V={vals[0]:.1f} P={vals[2]:.0f}W flux={vals[9]:.0f}")
    return jsonify({"ok": True})

# ── POST /api/flux ─────────────────────────────────────────────
@app.route("/api/flux", methods=["POST"])
def recv_flux():
    d = request.get_json(force=True, silent=True)
    if not d: return jsonify({"error":"bad json"}), 400
    flux_val = float(d.get("flux", 0))
    with sqlite3.connect(DB) as c:
        row = c.execute("""
            SELECT id FROM readings
            WHERE ts >= datetime('now','localtime','-90 seconds')
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if row:
            c.execute("UPDATE readings SET flux=? WHERE id=?", (flux_val, row[0]))
        else:
            c.execute("INSERT INTO readings (flux) VALUES (?)", (flux_val,))
        c.commit()
    print(f"[{datetime.now():%H:%M:%S}] flux: {flux_val:.0f} W/m²")
    return jsonify({"ok": True})

# ── GET /api/latest ────────────────────────────────────────────
@app.route("/api/latest")
def latest():
    with sqlite3.connect(DB) as c:
        r = c.execute("SELECT * FROM readings ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify(row2dict(r)) if r else ("", 204)

# ── GET /api/realtime ──────────────────────────────────────────
@app.route("/api/realtime")
def realtime():
    with sqlite3.connect(DB) as c:
        rows = c.execute("""
            SELECT * FROM (SELECT * FROM readings ORDER BY id DESC LIMIT 60)
            ORDER BY id
        """).fetchall()
    return jsonify([row2dict(r) for r in rows])

# ── GET /api/history?date=YYYY-MM-DD ──────────────────────────
@app.route("/api/history")
def history():
    date = request.args.get("date","")
    if not date: return jsonify({"error":"date required"}), 400
    with sqlite3.connect(DB) as c:
        rows = c.execute(
            "SELECT * FROM readings WHERE date(ts)=? ORDER BY ts", (date,)
        ).fetchall()
    return jsonify([row2dict(r) for r in rows])

# ── GET /api/dates ─────────────────────────────────────────────
@app.route("/api/dates")
def dates():
    with sqlite3.connect(DB) as c:
        rows = c.execute(
            "SELECT DISTINCT date(ts) FROM readings ORDER BY 1 DESC"
        ).fetchall()
    return jsonify([r[0] for r in rows])

# ── GET /api/export?date=YYYY-MM-DD ───────────────────────────
@app.route("/api/export")
def export():
    date = request.args.get("date","")
    if not date: return jsonify({"error":"date required"}), 400
    with sqlite3.connect(DB) as c:
        rows = c.execute(
            "SELECT * FROM readings WHERE date(ts)=? ORDER BY ts", (date,)
        ).fetchall()
    buf = io.StringIO()
    csv.writer(buf).writerow(KEYS)
    csv.writer(buf).writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=solar_{date}.csv"})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print("="*50)
    print("  Solar Monitor Backend")
    print(f"  http://0.0.0.0:{port}")
    print("="*50)
    app.run(host="0.0.0.0", port=port, debug=False)
