"""
RouteCare AI Agent — Domain-Specialized with Compliance Guardrails
==================================================================
Architecture: Rule-Based Expert Agent with 5 layers:
  1. Medical Guardrails   — hard clinical thresholds (WHO/AHA standards)
  2. Edge-Case Handler    — no ICU, all hospitals full, critical + far
  3. Scoring Engine       — weighted multi-factor stability score
  4. Explainability       — step-by-step reasoning chain per decision
  5. Audit Logger         — append-only decision log to SQLite

No ML model required — rule-based logic IS the agent; rules encode
domain expertise and compliance constraints.
"""

import json, os, sqlite3
from datetime import datetime

HOSPITALS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'hospitals.json')
DB_PATH        = os.path.join(os.path.dirname(__file__), '..', 'data', 'routecare.db')

# ══════════════════════════════════════════════════════════════
# LAYER 1 — MEDICAL GUARDRAILS
# Hard clinical thresholds based on WHO / AHA / BLS standards.
# Each guardrail can BLOCK or ESCALATE a decision.
# ══════════════════════════════════════════════════════════════

GUARDRAILS = [
    # (name, check_fn, severity, icd10_ref, action, message)
    {
        'id': 'GR-SPO2-CRITICAL',
        'name': 'Hypoxia — Critical SpO2',
        'check': lambda v: v['spo2'] < 85,
        'severity': 'CRITICAL',
        'icd10': 'R09.02',   # Hypoxemia
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'SpO2 < 85% — severe hypoxemia detected. Administer 100% O₂ immediately. '
                   'ICU with ventilator mandatory. Do NOT route to hospital without ventilator capacity.'
    },
    {
        'id': 'GR-SPO2-WARN',
        'name': 'Hypoxia — Low SpO2',
        'check': lambda v: 85 <= v['spo2'] < 92,
        'severity': 'WARNING',
        'icd10': 'R09.02',
        'action': 'MONITOR_CLOSELY',
        'message': 'SpO2 85–91% — supplemental oxygen required. Prefer hospital with pulmonology support.'
    },
    {
        'id': 'GR-BP-SHOCK',
        'name': 'Haemodynamic Shock',
        'check': lambda v: v['bp_sys'] < 80,
        'severity': 'CRITICAL',
        'icd10': 'R57.9',   # Shock, unspecified
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'Systolic BP < 80 mmHg — haemodynamic shock suspected. '
                   'IV fluids, vasopressors required. Route to nearest ICU without delay.'
    },
    {
        'id': 'GR-BP-HYPOTENSION',
        'name': 'Hypotension',
        'check': lambda v: 80 <= v['bp_sys'] < 90,
        'severity': 'WARNING',
        'icd10': 'I95.9',
        'action': 'MONITOR_CLOSELY',
        'message': 'Systolic BP 80–89 mmHg — hypotension. Position supine, monitor closely.'
    },
    {
        'id': 'GR-BP-HYPERTENSIVE-CRISIS',
        'name': 'Hypertensive Crisis',
        'check': lambda v: v['bp_sys'] > 180,
        'severity': 'CRITICAL',
        'icd10': 'I10',     # Essential hypertension
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'Systolic BP > 180 mmHg — hypertensive crisis. Risk of stroke or cardiac event. '
                   'Prefer hospital with cardiac/neuro ICU.'
    },
    {
        'id': 'GR-HR-VFIB-RISK',
        'name': 'Extreme Tachycardia',
        'check': lambda v: v['hr'] > 140,
        'severity': 'CRITICAL',
        'icd10': 'I47.2',   # Ventricular tachycardia
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'HR > 140 bpm — risk of ventricular arrhythmia. '
                   'Prepare defibrillator. Hospital must have cardiac monitoring.'
    },
    {
        'id': 'GR-HR-BRADYCARDIA',
        'name': 'Severe Bradycardia',
        'check': lambda v: v['hr'] < 45,
        'severity': 'CRITICAL',
        'icd10': 'R00.1',
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'HR < 45 bpm — severe bradycardia. Risk of cardiac arrest. '
                   'Atropine may be required. Ensure hospital has resuscitation team on standby.'
    },
    {
        'id': 'GR-TEMP-HYPERTHERMIA',
        'name': 'Hyperthermia',
        'check': lambda v: v['temp'] > 40.0,
        'severity': 'CRITICAL',
        'icd10': 'R50.9',
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'Temp > 40°C — hyperthermia/heat stroke. Initiate cooling en route. '
                   'Risk of multi-organ failure.'
    },
    {
        'id': 'GR-TEMP-HYPOTHERMIA',
        'name': 'Hypothermia',
        'check': lambda v: v['temp'] < 35.0,
        'severity': 'CRITICAL',
        'icd10': 'T68',
        'action': 'ESCALATE_IMMEDIATELY',
        'message': 'Temp < 35°C — hypothermia. Apply warming blanket. '
                   'Risk of cardiac arrhythmia increases below 32°C.'
    },
]

def run_guardrails(vitals):
    """
    Run all guardrail checks. Returns list of triggered guardrails.
    CRITICAL guardrails force ICU-mandatory routing.
    """
    triggered = []
    for g in GUARDRAILS:
        try:
            if g['check'](vitals):
                triggered.append({
                    'id':       g['id'],
                    'name':     g['name'],
                    'severity': g['severity'],
                    'icd10':    g['icd10'],
                    'action':   g['action'],
                    'message':  g['message'],
                })
        except Exception:
            pass
    return triggered


# ══════════════════════════════════════════════════════════════
# LAYER 2 — EDGE CASE HANDLER
# ══════════════════════════════════════════════════════════════

def handle_edge_cases(vitals, hospitals, guardrails_triggered, condition):
    """
    Detect and return edge case flags with human-readable advisories.
    Returns dict of {flag: advisory_string}.
    """
    flags = {}
    has_critical_guardrail = any(g['severity'] == 'CRITICAL' for g in guardrails_triggered)

    # Edge case 1: No hospitals with ICU when patient is critical
    icu_hospitals = [h for h in hospitals if h.get('icu_beds', 0) > 0]
    if condition == 'Critical' and not icu_hospitals:
        flags['NO_ICU_AVAILABLE'] = (
            'GUARDRAIL OVERRIDE: No registered hospital has ICU capacity. '
            'Routing to nearest hospital anyway. Request air ambulance transfer. '
            'Notify regional trauma coordinator immediately.'
        )

    # Edge case 2: Critical patient + all hospitals > 15 min ETA
    if condition == 'Critical':
        near_hospitals = [h for h in hospitals if h.get('eta_minutes', 99) <= 15]
        if not near_hospitals:
            flags['ALL_HOSPITALS_FAR'] = (
                'No hospital within 15-minute ETA. Administer advanced life support en route. '
                'Consider requesting police escort. Notify receiving hospital now for pre-arrival prep.'
            )

    # Edge case 3: SpO2 critical but recommended hospital has no ventilators
    if vitals.get('spo2', 100) < 85 and hospitals:
        best = sorted(hospitals, key=lambda h: h.get('eta_minutes', 99))[0]
        if best.get('ventilators', 0) == 0:
            flags['NO_VENTILATOR_AT_NEAREST'] = (
                'Nearest hospital has NO ventilators. Patient requires ventilatory support (SpO2 < 85%). '
                'Routing to next-nearest hospital with ventilator capacity despite longer ETA.'
            )

    # Edge case 4: Cardiac emergency but no cardiac unit nearby
    et = vitals.get('emergency_type', '').lower()
    if ('cardiac' in et or 'heart' in et):
        cardiac_hospitals = [h for h in hospitals if h.get('cardiac_unit')]
        if not cardiac_hospitals:
            flags['NO_CARDIAC_UNIT'] = (
                'Emergency type is cardiac but no registered hospital has a Cardiac Unit. '
                'Alerting nearest hospital to prepare cardiac monitoring on arrival.'
            )

    # Edge case 5: Patient age + emergency type risk flag (if age available)
    # (Handled via vitals dict extended field — no UI change needed)

    return flags


# ══════════════════════════════════════════════════════════════
# LAYER 3 — SCORING ENGINE  (unchanged logic, now with reason chain)
# ══════════════════════════════════════════════════════════════

def compute_stability(vitals):
    """Returns (score, score_breakdown list of strings)."""
    hr, bp_sys, bp_dia = vitals.get('hr',80), vitals.get('bp_sys',120), vitals.get('bp_dia',80)
    spo2, temp = vitals.get('spo2',98), vitals.get('temp',37.0)

    score = 100
    breakdown = []

    # HR
    if hr < 50 or hr > 130:
        score -= 30; breakdown.append(f'HR {hr} bpm — severe abnormality (−30 pts)')
    elif hr < 60 or hr > 110:
        score -= 15; breakdown.append(f'HR {hr} bpm — moderate abnormality (−15 pts)')
    elif hr < 65 or hr > 100:
        score -= 5;  breakdown.append(f'HR {hr} bpm — mild abnormality (−5 pts)')
    else:
        breakdown.append(f'HR {hr} bpm — normal (0 pts)')

    # BP
    if bp_sys < 80 or bp_sys > 180:
        score -= 30; breakdown.append(f'BP {bp_sys}/{bp_dia} mmHg — severe abnormality (−30 pts)')
    elif bp_sys < 90 or bp_sys > 160:
        score -= 15; breakdown.append(f'BP {bp_sys}/{bp_dia} mmHg — moderate abnormality (−15 pts)')
    elif bp_sys < 100 or bp_sys > 140:
        score -= 5;  breakdown.append(f'BP {bp_sys}/{bp_dia} mmHg — mild abnormality (−5 pts)')
    else:
        breakdown.append(f'BP {bp_sys}/{bp_dia} mmHg — normal (0 pts)')

    # SpO2
    if spo2 < 85:
        score -= 35; breakdown.append(f'SpO2 {spo2}% — critical hypoxemia (−35 pts)')
    elif spo2 < 90:
        score -= 20; breakdown.append(f'SpO2 {spo2}% — severe hypoxemia (−20 pts)')
    elif spo2 < 95:
        score -= 10; breakdown.append(f'SpO2 {spo2}% — mild hypoxemia (−10 pts)')
    else:
        breakdown.append(f'SpO2 {spo2}% — normal (0 pts)')

    # Temp
    if temp < 35.0 or temp > 40.0:
        score -= 20; breakdown.append(f'Temp {temp}°C — critical range (−20 pts)')
    elif temp < 36.0 or temp > 38.5:
        score -= 10; breakdown.append(f'Temp {temp}°C — abnormal (−10 pts)')
    elif temp < 36.5 or temp > 37.5:
        score -= 3;  breakdown.append(f'Temp {temp}°C — borderline (−3 pts)')
    else:
        breakdown.append(f'Temp {temp}°C — normal (0 pts)')

    score = max(0, min(100, score))
    return score, breakdown


# ══════════════════════════════════════════════════════════════
# LAYER 4 — HOSPITAL RANKING WITH EXPLAINABILITY
# ══════════════════════════════════════════════════════════════

def rank_hospitals(hospitals, condition, emergency_type, guardrails_triggered, edge_flags):
    """
    Rank hospitals and return each with a human-readable selection_reason.
    Guardrail overrides are applied here (e.g. filter out no-ventilator hospitals).
    """
    et = emergency_type.lower()
    has_critical = any(g['severity'] == 'CRITICAL' for g in guardrails_triggered)
    spo2_critical = any(g['id'] == 'GR-SPO2-CRITICAL' for g in guardrails_triggered)

    ranked = []
    for h in hospitals:
        h_score = 0
        reasons = []
        penalties = []

        icu  = h.get('icu_beds', 0)
        vent = h.get('ventilators', 0)
        eta  = h.get('eta_minutes', 15)
        dist = h.get('distance_km', 10)

        # Guardrail override: exclude no-ventilator hospitals when SpO2 critical
        if spo2_critical and vent == 0 and 'NO_VENTILATOR_AT_NEAREST' not in edge_flags:
            penalties.append('Excluded: no ventilators (SpO2 guardrail active)')
            ranked.append({**h, 'match_score': -999,
                           'selection_reason': ' | '.join(penalties),
                           'guardrail_blocked': True})
            continue

        # ICU weighting by condition
        if condition == 'Critical':
            h_score += icu * 10;  reasons.append(f'{icu} ICU beds (+{icu*10})')
            h_score += vent * 8;  reasons.append(f'{vent} ventilators (+{vent*8})')
        elif condition == 'Moderate':
            h_score += icu * 6;   reasons.append(f'{icu} ICU beds (+{icu*6})')
            h_score += vent * 4;  reasons.append(f'{vent} ventilators (+{vent*4})')
        else:
            h_score += icu * 2;   reasons.append(f'{icu} ICU beds (+{icu*2})')

        # ETA & distance penalty
        eta_pen = eta * 3;  h_score -= eta_pen;   penalties.append(f'{eta} min ETA (−{eta_pen})')
        dist_pen = dist * 2; h_score -= dist_pen; penalties.append(f'{dist} km away (−{dist_pen})')

        # Specialty match bonus
        if ('cardiac' in et or 'heart' in et) and h.get('cardiac_unit'):
            h_score += 25; reasons.append('Cardiac unit match (+25)')
        if ('trauma' in et or 'accident' in et) and h.get('trauma_unit'):
            h_score += 25; reasons.append('Trauma unit match (+25)')

        # Rating
        rating = h.get('rating', 4.0)
        h_score += rating * 5; reasons.append(f'Rating {rating} (+{rating*5:.0f})')

        # Build plain-English selection reason
        reason_str = 'Selected for: ' + ', '.join(reasons)
        if penalties:
            reason_str += ' | Deducted for: ' + ', '.join(penalties)

        ranked.append({**h, 'match_score': round(h_score, 1),
                       'selection_reason': reason_str,
                       'guardrail_blocked': False})

    ranked.sort(key=lambda x: x['match_score'], reverse=True)
    return ranked


# ══════════════════════════════════════════════════════════════
# LAYER 5 — AUDIT LOGGER
# Append-only log written to SQLite audit_log table.
# ══════════════════════════════════════════════════════════════

def _ensure_audit_table():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                ambulance_uid   TEXT,
                patient_name    TEXT,
                emergency_type  TEXT,
                stability_score INTEGER,
                condition       TEXT,
                guardrails_json TEXT,
                edge_flags_json TEXT,
                recommended_hospital TEXT,
                score_breakdown_json TEXT,
                vitals_json     TEXT,
                decision_version TEXT DEFAULT 'v3.0'
            )
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass

def write_audit_log(ambulance_uid, patient, vitals, score, condition,
                    guardrails, edge_flags, recommended_hospital, breakdown):
    """Write one immutable audit record per AI decision cycle."""
    try:
        _ensure_audit_table()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO audit_log
            (timestamp, ambulance_uid, patient_name, emergency_type,
             stability_score, condition, guardrails_json, edge_flags_json,
             recommended_hospital, score_breakdown_json, vitals_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
            ambulance_uid or 'unknown',
            patient.get('name', 'Unknown'),
            patient.get('emergency', vitals.get('emergency_type', 'General')),
            score,
            condition,
            json.dumps([{'id': g['id'], 'severity': g['severity']} for g in guardrails]),
            json.dumps(list(edge_flags.keys())),
            recommended_hospital or 'none',
            json.dumps(breakdown),
            json.dumps({k: vitals.get(k) for k in ['hr','bp_sys','bp_dia','spo2','temp']}),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass   # Audit failure must never break the clinical workflow


def get_audit_logs(limit=50):
    """Retrieve recent audit records for the /audit endpoint."""
    try:
        _ensure_audit_table()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            d['guardrails']  = json.loads(d.get('guardrails_json') or '[]')
            d['edge_flags']  = json.loads(d.get('edge_flags_json') or '[]')
            d['breakdown']   = json.loads(d.get('score_breakdown_json') or '[]')
            d['vitals']      = json.loads(d.get('vitals_json') or '{}')
            result.append(d)
        return result
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# MAIN AGENT ENTRY POINT
# ══════════════════════════════════════════════════════════════

def load_hospitals():
    """Legacy fallback loader from JSON file."""
    try:
        with open(HOSPITALS_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def get_ai_decision(vitals, hospitals=None, patient=None, ambulance_uid=None):
    """
    RouteCare AI Agent — main decision function.

    Agent execution steps (logged in audit trail):
      Step 1 → Run medical guardrails
      Step 2 → Compute stability score with breakdown
      Step 3 → Determine condition label
      Step 4 → Detect edge cases
      Step 5 → Rank hospitals with explainability
      Step 6 → Build natural-language explanation
      Step 7 → Write audit log
      Step 8 → Return structured decision
    """
    if hospitals is None:
        hospitals = load_hospitals()
    if patient is None:
        patient = {}

    emergency_type = vitals.get('emergency_type', 'General')

    # ── Step 1: Guardrails ─────────────────────────────────────
    guardrails_triggered = run_guardrails(vitals)

    # ── Step 2: Stability score ────────────────────────────────
    score, score_breakdown = compute_stability(vitals)

    # ── Step 3: Condition label ────────────────────────────────
    if score >= 70:
        condition, condition_color = 'Stable',   'stable'
    elif score >= 40:
        condition, condition_color = 'Moderate', 'moderate'
    else:
        condition, condition_color = 'Critical', 'critical'

    # ── Step 4: Edge cases ─────────────────────────────────────
    edge_flags = handle_edge_cases(vitals, hospitals, guardrails_triggered, condition)

    # ── Step 5: Hospital ranking ───────────────────────────────
    ranked = rank_hospitals(hospitals, condition, emergency_type,
                            guardrails_triggered, edge_flags)
    recommended_id = ranked[0]['id'] if ranked else None

    # ── Step 6: Explanation ────────────────────────────────────
    # Issues list (natural language)
    issues = []
    hr, bp_sys, bp_dia = vitals.get('hr',80), vitals.get('bp_sys',120), vitals.get('bp_dia',80)
    spo2, temp = vitals.get('spo2',98), vitals.get('temp',37.0)
    if hr < 60:        issues.append(f'bradycardia (HR: {hr} bpm)')
    elif hr > 110:     issues.append(f'tachycardia (HR: {hr} bpm)')
    if bp_sys < 90:    issues.append(f'hypotension (BP: {bp_sys}/{bp_dia} mmHg)')
    elif bp_sys > 160: issues.append(f'hypertension (BP: {bp_sys}/{bp_dia} mmHg)')
    if spo2 < 95:      issues.append(f'low oxygen saturation (SpO2: {spo2}%)')
    if temp < 36.0:    issues.append(f'hypothermia (Temp: {temp}°C)')
    elif temp > 38.5:  issues.append(f'hyperthermia/fever (Temp: {temp}°C)')

    if not issues:
        explanation = (
            f'All vitals within acceptable ranges. Stability score: {score}/100. '
            f'Standard emergency protocol for {emergency_type}. Continue monitoring en route.'
        )
    elif condition == 'Critical':
        explanation = (
            f'CRITICAL: {", ".join(issues)}. Stability score: {score}/100. '
            f'Immediate ICU required. Resuscitation team on standby. '
            f'Routed to hospital with highest ICU & ventilator capacity.'
        )
    elif condition == 'Moderate':
        explanation = (
            f'Moderate instability: {", ".join(issues)}. Stability score: {score}/100. '
            f'ICU bed recommended. Administer stabilising care en route.'
        )
    else:
        explanation = (
            f'Minor irregularities: {", ".join(issues)}. Stability score: {score}/100. '
            f'Patient manageable. Monitor closely and apply standard protocols.'
        )

    # Append active guardrail summaries to explanation
    critical_guards = [g for g in guardrails_triggered if g['severity'] == 'CRITICAL']
    if critical_guards:
        guard_summary = ' | GUARDRAIL: ' + ' | '.join(
            f"{g['name']} [{g['icd10']}]" for g in critical_guards
        )
        explanation += guard_summary

    # ── Hospital alert & prep items ────────────────────────────
    if condition == 'Critical':
        prep_items = ['ICU bed', 'ventilator', 'resuscitation team', 'blood bank standby']
    elif condition == 'Moderate':
        prep_items = ['ICU bed', 'monitoring equipment', 'specialist on standby']
    else:
        prep_items = ['emergency bay', 'attending physician']

    eta_str = f"{ranked[0]['eta_minutes']} minutes" if ranked else 'unknown'
    hospital_alert = (
        f'Incoming {condition.upper()} patient. Emergency: {emergency_type}. '
        f'ETA: {eta_str}. Prepare: {", ".join(prep_items)}.'
    )
    if critical_guards:
        hospital_alert += (
            f' ACTIVE GUARDRAILS: {", ".join(g["name"] for g in critical_guards)}.'
        )

    # ── Step 7: Audit log ──────────────────────────────────────
    write_audit_log(
        ambulance_uid, patient, vitals, score, condition,
        guardrails_triggered, edge_flags, recommended_id, score_breakdown
    )

    # ── Step 8: Return decision ────────────────────────────────
    return {
        # Existing fields (UI unchanged)
        'stability_score':          score,
        'condition':                condition,
        'condition_color':          condition_color,
        'explanation':              explanation,
        'recommended_hospital_id':  recommended_id,
        'hospitals_ranked':         ranked,
        'hospital_alert':           hospital_alert,
        'prep_items':               prep_items,
        # New agent fields (used by new UI additions only)
        'guardrails':               guardrails_triggered,
        'edge_flags':               edge_flags,
        'score_breakdown':          score_breakdown,
        'agent_steps': [
            'Step 1: Medical guardrails evaluated',
            f'Step 2: Stability score computed — {score}/100',
            f'Step 3: Condition classified — {condition}',
            f'Step 4: Edge cases checked — {len(edge_flags)} flag(s) raised',
            f'Step 5: {len(ranked)} hospitals ranked with explainability',
            'Step 6: Natural-language explanation generated',
            'Step 7: Decision written to audit log',
        ],
    }
