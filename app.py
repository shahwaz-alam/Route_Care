from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json, os, sys, random, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import db
from ai.ai_logic import get_ai_decision, get_audit_logs

app = Flask(__name__)
app.secret_key = 'routecare_secret_2024'
app.jinja_env.globals.update(enumerate=enumerate)

with app.app_context():
    db.init_db()

# ─────────────────────────────────────────────────────────────
def mock_vitals():
    return {
        'hr':     random.randint(55, 135),
        'bp_sys': random.randint(85, 175),
        'bp_dia': random.randint(55, 110),
        'spo2':   random.randint(82, 100),
        'temp':   round(random.uniform(35.2, 40.1), 1),
    }

def is_critical(vitals):
    """Return True if vitals indicate a critical condition."""
    hr  = vitals.get('hr', 80)
    sys = vitals.get('bp_sys', 120)
    sp  = vitals.get('spo2', 98)
    tmp = vitals.get('temp', 37)
    return (hr < 50 or hr > 130 or sys < 80 or sys > 180 or sp < 88 or tmp < 34 or tmp > 41)

def hospitals_to_ai_format(hospitals):
    result = []
    for h in hospitals:
        result.append({
            'id':          h['hospital_id'],
            'name':        h['name'],
            'address':     h['address'],
            'distance_km': h['distance_km'],
            'eta_minutes': h['eta_minutes'],
            'icu_beds':    h['icu_beds'],
            'ventilators': h['ventilators'],
            'trauma_unit': h['trauma_unit'],
            'cardiac_unit':h['cardiac_unit'],
            'specialties': h['specialties'],
            'contact':     h['contact'],
            'lat':         h['lat'],
            'lng':         h['lng'],
            'rating':      h['rating'],
        })
    return result

# ─────────────────────────────────────────────────────────────
# PUBLIC
# ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    role  = request.args.get('role', 'ambulance')
    if request.method == 'POST':
        role = request.form.get('role', 'ambulance')
        uid  = request.form.get('user_id', '').strip()
        pw   = request.form.get('password', '').strip()
        user = db.verify_user(uid, pw, role)
        if user:
            session['user_id'] = uid
            session['role']    = role
            session['name']    = user['name']
            return redirect(url_for('hospital_dashboard') if role == 'hospital' else url_for('patient_form'))
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error, role=role)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error, success = None, None
    role = request.args.get('role', 'ambulance')
    if request.method == 'POST':
        role    = request.form.get('role', 'ambulance')
        uid     = request.form.get('user_id', '').strip()
        pw      = request.form.get('password', '').strip()
        confirm = request.form.get('confirm', '').strip()
        name    = request.form.get('name', '').strip()
        if not uid or not pw or not name:
            error = 'All fields are required.'
        elif pw != confirm:
            error = 'Passwords do not match.'
        elif db.user_exists(uid):
            error = f'User ID "{uid}" is already taken.'
        else:
            if role == 'ambulance':
                vehicle = request.form.get('vehicle', '').strip()
                db.create_user(uid, role, name, pw, {'vehicle': vehicle})
                success = f'Ambulance account created! You can now log in as {uid}.'
            else:
                address     = request.form.get('address', '').strip()
                contact     = request.form.get('contact', '').strip()
                dist        = float(request.form.get('distance_km', 5.0) or 5.0)
                eta         = int(request.form.get('eta_minutes', 12) or 12)
                icu         = int(request.form.get('icu_beds', 5) or 5)
                vents       = int(request.form.get('ventilators', 3) or 3)
                trauma      = request.form.get('trauma_unit') == 'on'
                cardiac     = request.form.get('cardiac_unit') == 'on'
                lat         = float(request.form.get('lat', 13.05) or 13.05)
                lng         = float(request.form.get('lng', 80.25) or 80.25)
                specs_raw   = request.form.get('specialties', 'General')
                specialties = [s.strip() for s in specs_raw.split(',') if s.strip()]
                equipment  = {'ICU Beds': icu, 'Ventilators': vents,
                              'Defibrillators': 2, 'CT Scanner': 1, 'MRI Machine': 1,
                              'Blood Bank Units': 20, 'Oxygen Cylinders': 15, 'Cardiac Monitors': 4}
                facilities = {'Cardiac ICU': cardiac, 'Trauma Bay': trauma,
                              'Burns Unit': False, 'NICU': False, 'Dialysis': False, 'Cath Lab': False}
                db.create_user(uid, role, name, pw, {})
                db.create_hospital(uid, name, address, contact, dist, eta,
                                   icu, vents, trauma, cardiac, lat, lng,
                                   specialties, equipment, facilities)
                db.add_doctor(uid, f'Dr. {name.split()[0]} Chief', 'ER Physician', True)
                success = f'Hospital "{name}" registered! Log in as {uid}.'
    return render_template('signup.html', error=error, success=success, role=role)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ─────────────────────────────────────────────────────────────
# AMBULANCE
# ─────────────────────────────────────────────────────────────
@app.route('/patient', methods=['GET', 'POST'])
def patient_form():
    if 'user_id' not in session or session.get('role') != 'ambulance':
        return redirect(url_for('login'))
    if request.method == 'POST':
        fields = ['name','age','gender','blood_group','emergency','contact',
                  'symptoms','conditions','preferred_hospital','pickup_location',
                  'pickup_lat','pickup_lng']
        session['patient'] = {k: request.form.get(k, '') for k in fields}
        return redirect(url_for('emergency'))
    return render_template('patient_form.html')

@app.route('/emergency')
def emergency():
    if 'user_id' not in session or session.get('role') != 'ambulance':
        return redirect(url_for('login'))
    patient = session.get('patient', {
        'name':'John Doe','age':'45','gender':'Male','blood_group':'O+',
        'emergency':'Cardiac Arrest','contact':'9876543210',
        'symptoms':'Chest pain, shortness of breath','conditions':'Hypertension',
        'preferred_hospital':'','pickup_location':'','pickup_lat':'','pickup_lng':''
    })
    all_hosp  = db.get_all_hospitals()
    hosp_fmt  = hospitals_to_ai_format(all_hosp)
    vitals = mock_vitals()
    vitals['emergency_type'] = patient.get('emergency', 'General')
    ai = get_ai_decision(vitals, hosp_fmt, patient=patient, ambulance_uid=session['user_id'])
    db.set_live_emergency(session['user_id'], patient, vitals, ai)
    critical = is_critical(vitals)
    return render_template('emergency.html', patient=patient, vitals=vitals,
                           ai=ai, hospitals=hosp_fmt, is_critical=critical)

@app.route('/api/vitals')
def api_vitals():
    patient  = session.get('patient', {})
    all_hosp = db.get_all_hospitals()
    hosp_fmt = hospitals_to_ai_format(all_hosp)
    vitals   = mock_vitals()
    vitals['emergency_type'] = patient.get('emergency', 'General')
    ai = get_ai_decision(vitals, hosp_fmt, patient=patient, ambulance_uid=session.get('user_id'))
    db.update_live_vitals(vitals, ai)
    critical = is_critical(vitals)
    return jsonify({'vitals': vitals, 'ai': ai, 'is_critical': critical})

@app.route('/api/hospital/<hid>')
def api_hospital(hid):
    h = db.get_hospital_by_id(hid)
    if not h:
        return jsonify({'error': 'not found'}), 404
    doctors = db.get_doctors(h['user_id'])
    return jsonify({**h, 'doctors': doctors})

@app.route('/api/hospitals')
def api_hospitals():
    hosp = hospitals_to_ai_format(db.get_all_hospitals())
    return jsonify(hosp)

# ── Hospital request from ambulance (new) ──────────────────
@app.route('/api/request_hospital', methods=['POST'])
def api_request_hospital():
    """Ambulance sends a request to a specific hospital."""
    if 'user_id' not in session or session.get('role') != 'ambulance':
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json()
    hospital_id = data.get('hospital_id')
    db.send_hospital_request(session['user_id'], hospital_id)
    return jsonify({'ok': True, 'requested_at': time.time()})

@app.route('/api/hospital_request_status')
def api_hospital_request_status():
    """Ambulance polls to check if hospital accepted/declined."""
    if 'user_id' not in session or session.get('role') != 'ambulance':
        return jsonify({'error': 'unauthorized'}), 401
    status = db.get_hospital_request_status(session['user_id'])
    return jsonify(status or {'status': 'none'})

# ─────────────────────────────────────────────────────────────
# HOSPITAL
# ─────────────────────────────────────────────────────────────
@app.route('/hospital')
def hospital_dashboard():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return redirect(url_for('login', role='hospital'))
    uid   = session['user_id']
    hdata = db.get_hospital_by_user(uid) or {}
    docs  = db.get_doctors(uid)
    em    = db.get_live_emergency()
    return render_template('hospital_dashboard.html',
                           user_id=uid,
                           hname=session.get('name', uid),
                           hdata=hdata,
                           doctors=docs,
                           emergency_active=bool(em and em.get('active')),
                           emergency=em or {})

@app.route('/api/emergency_status')
def api_emergency_status():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    uid = session['user_id']
    em  = db.get_live_emergency()
    if not em or not em.get('active'):
        return jsonify({'active': False})
    accepted = em.get('accepted_by') == uid
    ai = em.get('ai', {})
    hrs = (ai.get('hospitals_ranked') or [{}])
    eta = hrs[0].get('eta_minutes', 0) if hrs else 0

    # Check if this hospital has a pending request
    req = db.get_pending_request_for_hospital(uid)
    has_request = bool(req)
    request_id  = req.get('id') if req else None
    request_ts  = req.get('requested_at') if req else None

    return jsonify({
        'active':          True,
        'patient':         em.get('patient', {}),
        'ai_summary':      ai.get('hospital_alert', ''),
        'condition':       ai.get('condition', ''),
        'condition_color': ai.get('condition_color', ''),
        'stability':       ai.get('stability_score', 0),
        'eta':             eta,
        'timestamp':       em.get('updated_at', ''),
        'accepted_by':     em.get('accepted_by'),
        'vitals':          em.get('vitals') if accepted else None,
        'ai_full':         ai                if accepted else None,
        'has_request':     has_request,
        'request_id':      request_id,
        'request_ts':      request_ts,
        'ambulance_lat':   em.get('patient', {}).get('pickup_lat', ''),
        'ambulance_lng':   em.get('patient', {}).get('pickup_lng', ''),
    })

@app.route('/api/accept_emergency', methods=['POST'])
def api_accept_emergency():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    uid = session['user_id']
    db.accept_live_emergency(uid)
    # Also resolve pending request as accepted
    db.resolve_hospital_request(uid, 'accepted')
    em = db.get_live_emergency()
    return jsonify({'ok': True, 'vitals': em.get('vitals', {}), 'ai': em.get('ai', {})})

@app.route('/api/decline_emergency', methods=['POST'])
def api_decline_emergency():
    """Hospital declines the incoming request."""
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    uid = session['user_id']
    db.resolve_hospital_request(uid, 'declined')
    return jsonify({'ok': True})

@app.route('/api/update_equipment', methods=['POST'])
def api_update_equipment():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json()
    db.update_hospital_equipment(session['user_id'],
                                  data.get('equipment', {}),
                                  data.get('facilities', {}))
    return jsonify({'ok': True})

@app.route('/api/allocate_resource', methods=['POST'])
def api_allocate_resource():
    """Allocate a resource from checklist — decrements equipment count."""
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json()
    resource_key = data.get('resource')   # e.g. "ICU Beds"
    quantity     = data.get('quantity', 1)
    uid = session['user_id']
    result = db.allocate_equipment(uid, resource_key, quantity)
    return jsonify({'ok': result, 'equipment': db.get_hospital_equipment(uid)})

@app.route('/api/toggle_doctor', methods=['POST'])
def api_toggle_doctor():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    doc_id = request.get_json().get('doctor_id')
    doc    = db.toggle_doctor(doc_id)
    doctors = db.get_doctors(session['user_id'])
    return jsonify({'ok': True, 'doctors': doctors})

@app.route('/api/add_doctor', methods=['POST'])
def api_add_doctor():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    data    = request.get_json()
    doctors = db.add_doctor(session['user_id'],
                             data.get('name', 'Dr. New'),
                             data.get('specialty', 'General'),
                             data.get('on_duty', True))
    return jsonify({'ok': True, 'doctors': doctors})

@app.route('/api/delete_doctor', methods=['POST'])
def api_delete_doctor():
    if 'user_id' not in session or session.get('role') != 'hospital':
        return jsonify({'error': 'unauthorized'}), 401
    doc_id  = request.get_json().get('doctor_id')
    doctors = db.delete_doctor(doc_id, session['user_id'])
    return jsonify({'ok': True, 'doctors': doctors})

@app.route('/audit')
def audit_log_view():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    logs = get_audit_logs(50)
    return render_template('audit_log.html', logs=logs, role=session.get('role'))

@app.route('/api/audit_logs')
def api_audit_logs():
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(get_audit_logs(50))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
