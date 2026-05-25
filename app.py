# ================================================================
#  app.py — Flask Backend  |  Solar / EV Monitor
#  Database: PostgreSQL (Railway) / fallback SQLite (local)
# ================================================================
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import os, csv, io
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
CORS(app)

# ── Timezone +7 (Asia/Bangkok) ─────────────────────────────────
TZ_OFFSET = timedelta(hours=7)

def to_local(ts_str):
    """แปลง UTC timestamp string → UTC+7 string"""
    if not ts_str:
        return ts_str
    try:
        # รองรับทั้ง "2024-01-01 05:00:42" และ "2024-01-01T05:00:42.123456"
        s = str(ts_str).replace("T", " ").split(".")[0]  # ตัด microseconds
        dt_utc = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_local = dt_utc + TZ_OFFSET
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts_str)

# ── เลือก database อัตโนมัติ ──────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    USE_PG = True
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    import sqlite3
    USE_PG = False
    DB = os.path.join(os.path.dirname(__file__), "solar.db")

def get_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect(DB)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id       SERIAL PRIMARY KEY,
                ts       TIMESTAMP DEFAULT NOW(),
                voltage  REAL, current REAL, power REAL,
                vpv1 REAL, cpv1 REAL, vpv2 REAL, cpv2 REAL,
                vinv REAL, cinv REAL, flux REAL
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       DATETIME DEFAULT (datetime('now','localtime')),
                voltage  REAL, current REAL, power REAL,
                vpv1 REAL, cpv1 REAL, vpv2 REAL, cpv2 REAL,
                vinv REAL, cinv REAL, flux REAL
            )
        """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

KEYS = ["id","ts","voltage","current","power",
        "vpv1","cpv1","vpv2","cpv2","vinv","cinv","flux"]

def row2dict(row):
    d = dict(zip(KEYS, row))
    if d.get("ts"):
        if USE_PG:
            # PostgreSQL เก็บ UTC → แปลงเป็น UTC+7 ก่อนส่งออก
            d["ts"] = to_local(str(d["ts"]))
        else:
            # SQLite ใช้ localtime อยู่แล้ว ไม่ต้องแปลง
            d["ts"] = str(d["ts"])
    return d

def fetchall(cur):
    return [row2dict(r) for r in cur.fetchall()]

# ── POST /api/data ─────────────────────────────────────────────
@app.route("/api/data", methods=["POST"])
def recv_data():
    d = request.get_json(force=True, silent=True)
    if not d: return jsonify({"error":"bad json"}), 400
    fields = ["voltage","current","power","vpv1","cpv1",
              "vpv2","cpv2","vinv","cinv","flux"]
    vals = [float(d.get(f, 0)) for f in fields]
    conn = get_conn()
    cur  = conn.cursor()
    if USE_PG:
        cur.execute("""INSERT INTO readings
            (voltage,current,power,vpv1,cpv1,vpv2,cpv2,vinv,cinv,flux)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", vals)
    else:
        cur.execute("""INSERT INTO readings
            (voltage,current,power,vpv1,cpv1,vpv2,cpv2,vinv,cinv,flux)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", vals)
    conn.commit(); cur.close(); conn.close()
    print(f"[{datetime.now():%H:%M:%S}] saved V={vals[0]:.1f} P={vals[2]:.0f}W")
    return jsonify({"ok": True})

# ── POST /api/flux ─────────────────────────────────────────────
@app.route("/api/flux", methods=["POST"])
def recv_flux():
    d = request.get_json(force=True, silent=True)
    if not d: return jsonify({"error":"bad json"}), 400
    flux_val = float(d.get("flux", 0))
    conn = get_conn(); cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            SELECT id FROM readings
            WHERE ts >= NOW() - INTERVAL '90 seconds'
            ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE readings SET flux=%s WHERE id=%s", (flux_val, row[0]))
        else:
            cur.execute("INSERT INTO readings (flux) VALUES (%s)", (flux_val,))
    else:
        cur.execute("""
            SELECT id FROM readings
            WHERE ts >= datetime('now','localtime','-90 seconds')
            ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE readings SET flux=? WHERE id=?", (flux_val, row[0]))
        else:
            cur.execute("INSERT INTO readings (flux) VALUES (?)", (flux_val,))
    conn.commit(); cur.close(); conn.close()
    print(f"[{datetime.now():%H:%M:%S}] flux: {flux_val:.0f} W/m²")
    return jsonify({"ok": True})

# ── GET /api/latest ────────────────────────────────────────────
@app.route("/api/latest")
def latest():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM readings ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    return jsonify(row2dict(row)) if row else ("", 204)

# ── GET /api/realtime ──────────────────────────────────────────
@app.route("/api/realtime")
def realtime():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT * FROM (SELECT * FROM readings ORDER BY id DESC LIMIT 60) t
        ORDER BY id ASC""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([row2dict(r) for r in rows])

# ── GET /api/history?date=YYYY-MM-DD ──────────────────────────
@app.route("/api/history")
def history():
    date = request.args.get("date","")
    if not date: return jsonify({"error":"date required"}), 400
    conn = get_conn(); cur = conn.cursor()
    if USE_PG:
        # ต้องแปลงเป็น UTC+7 ก่อน filter วันที่ (UTC 17:00 = ไทย 00:00 วันถัดไป)
        cur.execute("""
            SELECT * FROM readings
            WHERE (ts + INTERVAL '7 hours')::date = %s
            ORDER BY ts
        """, (date,))
    else:
        cur.execute("SELECT * FROM readings WHERE date(ts)=? ORDER BY ts", (date,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([row2dict(r) for r in rows])

# ── GET /api/dates ─────────────────────────────────────────────
@app.route("/api/dates")
def dates():
    conn = get_conn(); cur = conn.cursor()
    if USE_PG:
        # แสดงวันที่ตาม UTC+7
        cur.execute("""
            SELECT DISTINCT (ts + INTERVAL '7 hours')::date
            FROM readings ORDER BY 1 DESC
        """)
    else:
        cur.execute("SELECT DISTINCT date(ts) FROM readings ORDER BY 1 DESC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([str(r[0]) for r in rows])

# ── GET /api/export?date=YYYY-MM-DD ───────────────────────────
@app.route("/api/export")
def export():
    date = request.args.get("date","")
    if not date: return jsonify({"error":"date required"}), 400
    conn = get_conn(); cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            SELECT * FROM readings
            WHERE (ts + INTERVAL '7 hours')::date = %s
            ORDER BY ts
        """, (date,))
    else:
        cur.execute("SELECT * FROM readings WHERE date(ts)=? ORDER BY ts", (date,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    buf = io.StringIO()
    csv.writer(buf).writerow([
        "วันที่","เวลา",
        "แรงดัน (V)","กระแส (A)","กำลังไฟฟ้า (W)",
        "V INV (V)","I INV (A)","P INV (W)",
        "แรงดัน PV1 (V)","กระแส PV1 (A)",
        "แรงดัน PV2 (V)","กระแส PV2 (A)",
        "Solar Irradiance (W/m^2)"
    ])
    def fmt(v, dp=3):
        try: return round(float(v), dp) if v is not None else 0
        except: return 0

    for r in rows:
        d = row2dict(r)  # ts ถูกแปลงเป็น UTC+7 แล้วใน row2dict
        ts  = str(d.get("ts",""))
        date_part = ts[:10] if len(ts) >= 10 else ""
        time_part = ts[11:19] if len(ts) >= 19 else ""
        vinv = fmt(d.get("vinv",0),1) / 10
        cinv = fmt(d.get("cinv",0),4) / 1000
        pinv = round(vinv * cinv, 2)
        vpv1 = fmt(d.get("vpv1",0),1) / 10
        vpv2 = fmt(d.get("vpv2",0),1) / 10
        csv.writer(buf).writerow([
            date_part, time_part,
            fmt(d.get("voltage",0),2),
            fmt(d.get("current",0),3),
            fmt(d.get("power",0),1),
            round(vinv,1), round(cinv,3), pinv,
            round(vpv1,1), round(fmt(d.get("cpv1",0),3)/100, 3),
            round(vpv2,1), round(fmt(d.get("cpv2",0),3)/100, 3),
            fmt(d.get("flux",0),2),
        ])
    return Response(buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=solar_{date}.csv"})

# ── GET / ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file("dashboard.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Solar Monitor — {'PostgreSQL' if USE_PG else 'SQLite'} — port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
