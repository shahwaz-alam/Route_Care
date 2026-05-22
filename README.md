# 🚑 RouteCare — AI Emergency Response System

An AI-powered emergency response system that monitors patient vitals in real-time and intelligently reroutes ambulances to the best available hospital.

---

## 🚀 Quick Start

```bash
pip install flask
python app.py
```
Open: http://localhost:5000

---

## 🔑 Demo Login Credentials

| Role       | User ID | Password  |
|------------|---------|-----------|
| Ambulance  | AMB001  | pass123   |
| Ambulance  | AMB002  | pass456   |
| Hospital   | HSP001  | hosp123   |
| Hospital   | HSP002  | hosp456   |

---

## 📁 Project Structure

```
routecare/
├── app.py                  # Flask backend + routes
├── requirements.txt
├── ai/
│   └── ai_logic.py         # AI decision engine
├── data/
│   └── hospitals.json      # Hospital database
├── static/
│   └── css/style.css       # Full stylesheet
└── templates/
    ├── index.html           # Landing page
    ├── login.html           # Login (ambulance + hospital toggle)
    ├── patient_form.html    # Patient intake form
    ├── emergency.html       # Live monitoring + AI + hospital recommendation
    └── hospital_dashboard.html  # Hospital incoming alert view
```

---

## 🧠 AI Logic (`ai/ai_logic.py`)

**Function:** `get_ai_decision(vitals)`

**Input:**
- `hr` — Heart Rate (bpm)
- `bp_sys`, `bp_dia` — Blood Pressure (mmHg)
- `spo2` — Oxygen Saturation (%)
- `temp` — Temperature (°C)
- `emergency_type` — Type of emergency

**Output:**
- `stability_score` — 0–100
- `condition` — Stable / Moderate / Critical
- `explanation` — AI-generated clinical reasoning
- `hospitals_ranked` — Sorted list of hospitals by match score
- `hospital_alert` — Pre-generated alert for receiving hospital
- `prep_items` — Items hospital should prepare

**Scoring Logic:**
- Deducts points for abnormal HR, BP, SpO2, Temperature
- Ranks hospitals by: ICU beds × weight + ventilators × weight − distance penalty + specialty match bonus

---

## 🌟 Key Features

- **Live Vitals Monitor** — HR, BP, SpO2, Temperature with auto-refresh (15s)
- **AI Stability Scoring** — 0–100 score with visual ring + clinical explanation
- **Smart Hospital Matching** — Ranked by ICU beds, ETA, specialty match
- **Hospital Dashboard** — Incoming alert, AI summary, prep checklist, ICU bed map, ETA countdown
- **Auto Reroute** — One-click rerouting to best hospital
