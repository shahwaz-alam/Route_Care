"""
db.py — RouteCare SQLite database layer
All persistent state lives here so ambulance & hospital data stays synced.
"""
import sqlite3, json, os, hashlib, secrets

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'routecare.db')

# ─────────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ─────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL UNIQUE,
    role        TEXT    NOT NULL CHECK(role IN ('ambulance','hospital')),
    name        TEXT    NOT NULL,
    password_hash TEXT  NOT NULL,
    extra_json  TEXT    DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hospitals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id     TEXT    NOT NULL UNIQUE,   -- e.g. H001, H003, or user-created
    user_id         TEXT    NOT NULL UNIQUE,   -- FK → users.user_id
    name            TEXT    NOT NULL,
    address         TEXT    NOT NULL DEFAULT '',
    contact         TEXT    NOT NULL DEFAULT '',
    distance_km     REAL    NOT NULL DEFAULT 5.0,
    eta_minutes     INTEGER NOT NULL DEFAULT 12,
    icu_beds        INTEGER NOT NULL DEFAULT 5,
    ventilators     INTEGER NOT NULL DEFAULT 3,
    trauma_unit     INTEGER NOT NULL DEFAULT 0,
    cardiac_unit    INTEGER NOT NULL DEFAULT 0,
    lat             REAL    NOT NULL DEFAULT 13.0500,
    lng             REAL    NOT NULL DEFAULT 80.2500,
    rating          REAL    NOT NULL DEFAULT 4.0,
    specialties_json TEXT   DEFAULT '["General"]',
    equipment_json  TEXT    DEFAULT '{}',
    facilities_json TEXT    DEFAULT '{}',
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS doctors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_uid TEXT   NOT NULL,   -- FK → users.user_id for hospital
    name        TEXT    NOT NULL,
    specialty   TEXT    NOT NULL DEFAULT 'General',
    on_duty     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (hospital_uid) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS live_emergency (
    id          INTEGER PRIMARY KEY CHECK(id=1),
    active      INTEGER NOT NULL DEFAULT 0,
    ambulance_uid TEXT,
    accepted_by TEXT,
    patient_json  TEXT DEFAULT '{}',
    vitals_json   TEXT DEFAULT '{}',
    ai_json       TEXT DEFAULT '{}',
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Ensure exactly one row in live_emergency
INSERT OR IGNORE INTO live_emergency (id) VALUES (1);
"""

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    # Also create hospital_requests table added in v2
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hospital_requests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ambulance_uid   TEXT NOT NULL,
        hospital_id     TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        requested_at    REAL NOT NULL,
        resolved_at     REAL
    );
    """)
    conn.commit()
    _seed(conn)
    conn.close()

# ─────────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────────
def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

# ─────────────────────────────────────────────────────────────
# Seed initial data
# ─────────────────────────────────────────────────────────────
def _seed(conn):
    # Seed users
    seed_users = [
        ('AMB001','ambulance','Unit Alpha-1','pass123','{"vehicle":"KA-01-1234"}'),
        ('AMB002','ambulance','Unit Bravo-2','pass456','{"vehicle":"KA-02-5678"}'),
        ('HSP001','hospital','Apollo Trauma Center','hosp123','{}'),
        ('HSP002','hospital','Fortis Malar Hospital','hosp456','{}'),
    ]
    for uid, role, name, pw, extra in seed_users:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id,role,name,password_hash,extra_json) VALUES (?,?,?,?,?)",
            (uid, role, name, _hash(pw), extra)
        )

    # Seed hospitals
    seed_hospitals = [
        {
            'hospital_id':'H001','user_id':'HSP001',
            'name':'Apollo Trauma Center',
            'address':'21 Greams Road, Chennai',
            'contact':'+91-44-2829-0200',
            'distance_km':2.4,'eta_minutes':6,
            'icu_beds':8,'ventilators':5,'trauma_unit':1,'cardiac_unit':1,
            'lat':13.0607,'lng':80.2496,'rating':4.8,
            'specialties_json':json.dumps(['Trauma','Cardiac','Neuro','Burns']),
            'equipment_json':json.dumps({'ICU Beds':8,'Ventilators':5,'Defibrillators':4,
                'CT Scanner':2,'MRI Machine':1,'Blood Bank Units':50,
                'Oxygen Cylinders':30,'Cardiac Monitors':10}),
            'facilities_json':json.dumps({'Cardiac ICU':True,'Trauma Bay':True,'Burns Unit':True,
                'NICU':True,'Dialysis':True,'Cath Lab':True}),
        },
        {
            'hospital_id':'H002','user_id':'HSP002',
            'name':'Fortis Malar Hospital',
            'address':'52 1st Main Rd, Gandhi Nagar, Chennai',
            'contact':'+91-44-4289-2288',
            'distance_km':3.8,'eta_minutes':9,
            'icu_beds':5,'ventilators':3,'trauma_unit':0,'cardiac_unit':1,
            'lat':13.0201,'lng':80.2568,'rating':4.6,
            'specialties_json':json.dumps(['Cardiac','Neuro','Oncology']),
            'equipment_json':json.dumps({'ICU Beds':5,'Ventilators':3,'Defibrillators':2,
                'CT Scanner':1,'MRI Machine':1,'Blood Bank Units':30,
                'Oxygen Cylinders':20,'Cardiac Monitors':6}),
            'facilities_json':json.dumps({'Cardiac ICU':True,'Trauma Bay':False,'Burns Unit':False,
                'NICU':True,'Dialysis':True,'Cath Lab':False}),
        },
        {
            'hospital_id':'H003','user_id':'HSP001',   # shared admin for demo
            'name':'MIOT International',
            'address':'4/112, Mount Poonamallee Rd, Chennai',
            'contact':'+91-44-4200-2288',
            'distance_km':5.1,'eta_minutes':12,
            'icu_beds':3,'ventilators':2,'trauma_unit':1,'cardiac_unit':0,
            'lat':13.0418,'lng':80.1765,'rating':4.5,
            'specialties_json':json.dumps(['Ortho','Trauma','General Surgery']),
            'equipment_json':json.dumps({'ICU Beds':3,'Ventilators':2,'Defibrillators':2,
                'CT Scanner':1,'MRI Machine':0,'Blood Bank Units':20,
                'Oxygen Cylinders':15,'Cardiac Monitors':4}),
            'facilities_json':json.dumps({'Cardiac ICU':False,'Trauma Bay':True,'Burns Unit':False,
                'NICU':False,'Dialysis':False,'Cath Lab':False}),
        },
    ]
    for h in seed_hospitals:
        conn.execute("""
            INSERT OR IGNORE INTO hospitals
            (hospital_id,user_id,name,address,contact,distance_km,eta_minutes,
             icu_beds,ventilators,trauma_unit,cardiac_unit,lat,lng,rating,
             specialties_json,equipment_json,facilities_json)
            VALUES (:hospital_id,:user_id,:name,:address,:contact,:distance_km,:eta_minutes,
                    :icu_beds,:ventilators,:trauma_unit,:cardiac_unit,:lat,:lng,:rating,
                    :specialties_json,:equipment_json,:facilities_json)
        """, h)

    # Seed doctors
    seed_doctors = [
        ('HSP001','Dr. Priya Menon','Cardiologist',1),
        ('HSP001','Dr. Arjun Nair','Trauma Surgeon',1),
        ('HSP001','Dr. Sneha Rajan','Neurologist',0),
        ('HSP001','Dr. Karthik Suresh','Pulmonologist',1),
        ('HSP001','Dr. Meera Pillai','Anesthesiologist',0),
        ('HSP001','Dr. Rahul Verma','ER Physician',1),
        ('HSP002','Dr. Anil Kumar','Cardiologist',1),
        ('HSP002','Dr. Divya Sharma','ER Physician',1),
        ('HSP002','Dr. Suresh Babu','Orthopedic',0),
        ('HSP002','Dr. Lakshmi Patel','Neurologist',1),
    ]
    for huid, name, spec, duty in seed_doctors:
        existing = conn.execute(
            "SELECT id FROM doctors WHERE hospital_uid=? AND name=?", (huid, name)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO doctors (hospital_uid,name,specialty,on_duty) VALUES (?,?,?,?)",
                (huid, name, spec, duty)
            )

    conn.commit()

# ─────────────────────────────────────────────────────────────
# USER CRUD
# ─────────────────────────────────────────────────────────────
def user_exists(user_id):
    with get_conn() as c:
        return c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None

def get_user(user_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d['extra'] = json.loads(d.get('extra_json') or '{}')
        return d

def verify_user(user_id, password, role):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE user_id=? AND role=? AND password_hash=?",
            (user_id, role, _hash(password))
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d['extra'] = json.loads(d.get('extra_json') or '{}')
        return d

def create_user(user_id, role, name, password, extra=None):
    with get_conn() as c:
        c.execute(
            "INSERT INTO users (user_id,role,name,password_hash,extra_json) VALUES (?,?,?,?,?)",
            (user_id, role, name, _hash(password), json.dumps(extra or {}))
        )
        c.commit()

# ─────────────────────────────────────────────────────────────
# HOSPITAL CRUD
# ─────────────────────────────────────────────────────────────
def get_all_hospitals():
    """Return all hospitals as list of dicts — used by ambulance AI routing."""
    with get_conn() as c:
        rows = c.execute("SELECT * FROM hospitals ORDER BY distance_km").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['specialties']  = json.loads(d.pop('specialties_json', '[]'))
        d['equipment']    = json.loads(d.pop('equipment_json', '{}'))
        d['facilities']   = json.loads(d.pop('facilities_json', '{}'))
        d['trauma_unit']  = bool(d['trauma_unit'])
        d['cardiac_unit'] = bool(d['cardiac_unit'])
        result.append(d)
    return result

def get_hospital_by_id(hospital_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM hospitals WHERE hospital_id=?", (hospital_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['specialties']  = json.loads(d.pop('specialties_json', '[]'))
    d['equipment']    = json.loads(d.pop('equipment_json', '{}'))
    d['facilities']   = json.loads(d.pop('facilities_json', '{}'))
    d['trauma_unit']  = bool(d['trauma_unit'])
    d['cardiac_unit'] = bool(d['cardiac_unit'])
    return d

def get_hospital_by_user(user_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM hospitals WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['specialties']  = json.loads(d.pop('specialties_json', '[]'))
    d['equipment']    = json.loads(d.pop('equipment_json', '{}'))
    d['facilities']   = json.loads(d.pop('facilities_json', '{}'))
    d['trauma_unit']  = bool(d['trauma_unit'])
    d['cardiac_unit'] = bool(d['cardiac_unit'])
    return d

def create_hospital(user_id, name, address, contact, distance_km, eta_minutes,
                    icu_beds, ventilators, trauma_unit, cardiac_unit,
                    lat, lng, specialties, equipment, facilities):
    """Create a new hospital record linked to a hospital user account."""
    # Generate unique hospital_id
    with get_conn() as c:
        count = c.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
        hospital_id = f"H{count+1:03d}"
        # Ensure uniqueness
        while c.execute("SELECT 1 FROM hospitals WHERE hospital_id=?", (hospital_id,)).fetchone():
            count += 1
            hospital_id = f"H{count+1:03d}"
        c.execute("""
            INSERT INTO hospitals
            (hospital_id,user_id,name,address,contact,distance_km,eta_minutes,
             icu_beds,ventilators,trauma_unit,cardiac_unit,lat,lng,rating,
             specialties_json,equipment_json,facilities_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            hospital_id, user_id, name, address, contact,
            distance_km, eta_minutes, icu_beds, ventilators,
            int(trauma_unit), int(cardiac_unit), lat, lng, 4.0,
            json.dumps(specialties), json.dumps(equipment), json.dumps(facilities)
        ))
        c.commit()
    return hospital_id

def update_hospital_equipment(user_id, equipment, facilities):
    with get_conn() as c:
        # Also update icu_beds from equipment dict if present
        icu = equipment.get('ICU Beds')
        vents = equipment.get('Ventilators')
        extra_sets = []
        params = []
        if icu is not None:
            extra_sets.append('icu_beds=?')
            params.append(icu)
        if vents is not None:
            extra_sets.append('ventilators=?')
            params.append(vents)
        extra_clause = (', ' + ', '.join(extra_sets)) if extra_sets else ''
        params += [json.dumps(equipment), json.dumps(facilities), user_id]
        c.execute(
            f"UPDATE hospitals SET equipment_json=?, facilities_json=?{extra_clause.replace(', ','').replace('icu_beds=?','').replace('ventilators=?','').replace(',','')} WHERE user_id=?",
            params
        )
        # Simpler approach:
        c.execute(
            "UPDATE hospitals SET equipment_json=?, facilities_json=? WHERE user_id=?",
            (json.dumps(equipment), json.dumps(facilities), user_id)
        )
        if icu is not None:
            c.execute("UPDATE hospitals SET icu_beds=? WHERE user_id=?", (icu, user_id))
        if vents is not None:
            c.execute("UPDATE hospitals SET ventilators=? WHERE user_id=?", (vents, user_id))
        c.commit()

# ─────────────────────────────────────────────────────────────
# DOCTOR CRUD
# ─────────────────────────────────────────────────────────────
def get_doctors(hospital_uid):
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM doctors WHERE hospital_uid=? ORDER BY id", (hospital_uid,)
        ).fetchall()
    return [dict(r) for r in rows]

def toggle_doctor(doctor_id):
    with get_conn() as c:
        c.execute("UPDATE doctors SET on_duty = 1-on_duty WHERE id=?", (doctor_id,))
        c.commit()
        row = c.execute("SELECT * FROM doctors WHERE id=?", (doctor_id,)).fetchone()
    return dict(row) if row else None

def add_doctor(hospital_uid, name, specialty, on_duty=True):
    with get_conn() as c:
        c.execute(
            "INSERT INTO doctors (hospital_uid,name,specialty,on_duty) VALUES (?,?,?,?)",
            (hospital_uid, name, specialty, int(on_duty))
        )
        c.commit()
    return get_doctors(hospital_uid)

def delete_doctor(doctor_id, hospital_uid):
    """Remove a doctor from the roster."""
    with get_conn() as c:
        c.execute("DELETE FROM doctors WHERE id=? AND hospital_uid=?", (doctor_id, hospital_uid))
        c.commit()
    return get_doctors(hospital_uid)

# ─────────────────────────────────────────────────────────────
# LIVE EMERGENCY
# ─────────────────────────────────────────────────────────────
def set_live_emergency(ambulance_uid, patient, vitals, ai):
    import datetime as dt
    with get_conn() as c:
        c.execute("""
            UPDATE live_emergency SET
              active=1, ambulance_uid=?, accepted_by=NULL,
              patient_json=?, vitals_json=?, ai_json=?,
              updated_at=datetime('now')
            WHERE id=1
        """, (ambulance_uid, json.dumps(patient), json.dumps(vitals), json.dumps(ai)))
        c.commit()

def update_live_vitals(vitals, ai):
    with get_conn() as c:
        c.execute("""
            UPDATE live_emergency SET vitals_json=?, ai_json=?, updated_at=datetime('now')
            WHERE id=1 AND active=1
        """, (json.dumps(vitals), json.dumps(ai)))
        c.commit()

def accept_live_emergency(hospital_uid):
    with get_conn() as c:
        c.execute("UPDATE live_emergency SET accepted_by=? WHERE id=1", (hospital_uid,))
        c.commit()

def get_live_emergency():
    with get_conn() as c:
        row = c.execute("SELECT * FROM live_emergency WHERE id=1").fetchone()
    if not row:
        return None
    d = dict(row)
    d['patient'] = json.loads(d.get('patient_json') or '{}')
    d['vitals']  = json.loads(d.get('vitals_json') or '{}')
    d['ai']      = json.loads(d.get('ai_json') or '{}')
    return d

def clear_live_emergency():
    with get_conn() as c:
        c.execute("""
            UPDATE live_emergency SET active=0, ambulance_uid=NULL, accepted_by=NULL,
            patient_json='{}', vitals_json='{}', ai_json='{}' WHERE id=1
        """)
        c.commit()

# ─────────────────────────────────────────────────────────────
# HOSPITAL REQUESTS (ambulance → hospital)
# Added for: 20-second accept/decline flow, redirect logic
# ─────────────────────────────────────────────────────────────

_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS hospital_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ambulance_uid   TEXT NOT NULL,
    hospital_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined | redirected
    requested_at    REAL NOT NULL,   -- epoch seconds
    resolved_at     REAL
);
"""

def _ensure_request_table():
    with get_conn() as c:
        c.executescript(_REQUEST_SCHEMA)
        c.commit()

def send_hospital_request(ambulance_uid, hospital_id):
    """Create a new pending request from ambulance to hospital."""
    _ensure_request_table()
    import time as _t
    with get_conn() as c:
        # Cancel any previous pending request by this ambulance
        c.execute(
            "UPDATE hospital_requests SET status='redirected' WHERE ambulance_uid=? AND status='pending'",
            (ambulance_uid,)
        )
        c.execute(
            "INSERT INTO hospital_requests (ambulance_uid, hospital_id, status, requested_at) VALUES (?,?,?,?)",
            (ambulance_uid, hospital_id, 'pending', _t.time())
        )
        c.commit()

def get_pending_request_for_hospital(hospital_uid):
    """Get the most recent pending request directed at this hospital."""
    _ensure_request_table()
    # Map hospital user_id → hospital_id
    with get_conn() as c:
        h = c.execute("SELECT hospital_id FROM hospitals WHERE user_id=?", (hospital_uid,)).fetchone()
        if not h:
            return None
        hospital_id = h['hospital_id']
        row = c.execute(
            "SELECT * FROM hospital_requests WHERE hospital_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (hospital_id,)
        ).fetchone()
    return dict(row) if row else None

def resolve_hospital_request(hospital_uid, status):
    """Accept or decline the pending request for a hospital."""
    _ensure_request_table()
    import time as _t
    with get_conn() as c:
        h = c.execute("SELECT hospital_id FROM hospitals WHERE user_id=?", (hospital_uid,)).fetchone()
        if not h:
            return
        hospital_id = h['hospital_id']
        c.execute(
            "UPDATE hospital_requests SET status=?, resolved_at=? WHERE hospital_id=? AND status='pending'",
            (status, _t.time(), hospital_id)
        )
        c.commit()

def get_hospital_request_status(ambulance_uid):
    """Ambulance polls for its latest request status."""
    _ensure_request_table()
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM hospital_requests WHERE ambulance_uid=? ORDER BY id DESC LIMIT 1",
            (ambulance_uid,)
        ).fetchone()
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────
# EQUIPMENT ALLOCATION (checklist → equipment count sync)
# ─────────────────────────────────────────────────────────────

# Mapping of checklist item names → equipment keys
CHECKLIST_EQUIPMENT_MAP = {
    'ICU Bed Cleared':        'ICU Beds',
    'Ventilator Ready':       'Ventilators',
    'Resuscitation Team':     None,        # staff, not tracked as equipment
    'Blood Bank Contacted':   'Blood Bank Units',
    'Emergency Bay Open':     None,
    'Physician Notified':     None,
    'Defibrillator Ready':    'Defibrillators',
    'Cardiac Monitor':        'Cardiac Monitors',
    'Oxygen Cylinder':        'Oxygen Cylinders',
}

def allocate_equipment(user_id, resource_key, quantity=1):
    """Decrement an equipment count when a checklist item is completed."""
    with get_conn() as c:
        row = c.execute("SELECT equipment_json FROM hospitals WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return False
        eq = json.loads(row['equipment_json'] or '{}')
        if resource_key in eq:
            eq[resource_key] = max(0, eq[resource_key] - quantity)
            c.execute(
                "UPDATE hospitals SET equipment_json=? WHERE user_id=?",
                (json.dumps(eq), user_id)
            )
            # Also sync icu_beds / ventilators columns
            if resource_key == 'ICU Beds':
                c.execute("UPDATE hospitals SET icu_beds=? WHERE user_id=?", (eq[resource_key], user_id))
            if resource_key == 'Ventilators':
                c.execute("UPDATE hospitals SET ventilators=? WHERE user_id=?", (eq[resource_key], user_id))
            c.commit()
            return True
        return False

def get_hospital_equipment(user_id):
    """Return the current equipment dict for a hospital."""
    with get_conn() as c:
        row = c.execute("SELECT equipment_json FROM hospitals WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row['equipment_json'] or '{}') if row else {}
