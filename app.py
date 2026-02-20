from datetime import datetime
from flask import Flask, render_template, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory store for patient submissions (resets on server restart)
# ---------------------------------------------------------------------------
submissions = []

# ---------------------------------------------------------------------------
# Symptom catalogue – each symptom has a weight used in priority scoring.
# "Critical" symptoms (weight ≥ 3) can trigger High Priority on their own
# when combined with Severe severity.
# ---------------------------------------------------------------------------
SYMPTOMS = {
    "Chest Pain":           {"weight": 5, "critical": True},
    "Difficulty Breathing":  {"weight": 5, "critical": True},
    "High Fever":           {"weight": 4, "critical": True},
    "Severe Headache":      {"weight": 3, "critical": True},
    "Allergic Reaction":    {"weight": 4, "critical": True},
    "Bleeding":             {"weight": 3, "critical": True},
    "Dizziness":            {"weight": 2, "critical": False},
    "Nausea/Vomiting":      {"weight": 2, "critical": False},
    "Abdominal Pain":       {"weight": 2, "critical": False},
    "Fatigue":              {"weight": 1, "critical": False},
}

SEVERITY_MULTIPLIER = {
    "Mild":     1.0,
    "Moderate": 1.5,
    "Severe":   2.0,
}

# ---------------------------------------------------------------------------
# Safety instructions & next-steps per priority level
# ---------------------------------------------------------------------------
PRIORITY_DATA = {
    "High": {
        "color": "red",
        "label": "High Priority – Immediate Attention Required",
        "instructions": [
            "🚨 Seek immediate medical attention — call emergency services or go to the nearest emergency room.",
            "Do NOT drive yourself; ask someone to take you or call an ambulance.",
            "If experiencing chest pain, sit upright and stay as calm as possible while waiting for help.",
            "If there is active bleeding, apply firm pressure with a clean cloth.",
            "Do not eat or drink anything until assessed by a medical professional.",
        ],
        "next_steps": [
            "Proceed to the nearest Emergency Room immediately.",
            "Carry a list of current medications and allergies with you.",
            "Inform the ER staff of all symptoms listed above.",
        ],
        "reassurance": (
            "We understand this may feel overwhelming, but you are doing the right thing by seeking help. "
            "Emergency teams are trained to handle situations exactly like yours. Stay as calm as you can — help is available."
        ),
    },
    "Medium": {
        "color": "orange",
        "label": "Medium Priority – Prompt Medical Consultation Advised",
        "instructions": [
            "⚠️ Schedule an urgent appointment with your doctor or visit an urgent-care clinic today.",
            "Stay hydrated — drink small sips of water or an electrolyte solution.",
            "Monitor your temperature every 2 hours if you have a fever.",
            "Rest in a comfortable, well-ventilated area.",
            "Avoid strenuous physical activity until evaluated.",
        ],
        "next_steps": [
            "Contact your primary-care physician within the next few hours.",
            "If symptoms worsen before your appointment, go to the Emergency Room.",
            "Keep a written log of symptom changes to share with your doctor.",
        ],
        "reassurance": (
            "Your symptoms warrant professional attention, but they do not appear to be immediately life-threatening. "
            "Getting checked promptly is the best course of action. You are taking good care of yourself."
        ),
    },
    "Low": {
        "color": "green",
        "label": "Low Priority – Self-Care & Monitoring",
        "instructions": [
            "✅ Your symptoms suggest a low level of urgency at this time.",
            "Stay hydrated and get plenty of rest.",
            "Take over-the-counter medications for symptom relief as appropriate (e.g., paracetamol for mild fever).",
            "Eat light, nutritious meals to support recovery.",
            "Avoid caffeine, alcohol, and heavy foods.",
        ],
        "next_steps": [
            "Monitor your symptoms over the next 24–48 hours.",
            "Schedule a routine check-up with your doctor if symptoms persist beyond 48 hours.",
            "Return here or call a health helpline if new or worsening symptoms develop.",
        ],
        "reassurance": (
            "It's great that you're paying attention to how you feel. Based on the information provided, "
            "rest and self-care should help you recover. Don't hesitate to seek medical advice if anything changes."
        ),
    },
}


# ---------------------------------------------------------------------------
# Triage scoring engine
# ---------------------------------------------------------------------------
def compute_triage(severity: str, selected_symptoms: list[str]) -> dict:
    """
    Computes a triage priority from the patient's severity rating and
    selected symptoms.

    Scoring rules
    ─────────────
    1. Sum the weights of all selected symptoms.
    2. Multiply by the severity multiplier  (Mild ×1, Moderate ×1.5, Severe ×2).
    3. Auto-escalate to High if severity is Severe AND any critical symptom
       is present.
    4. Map the final score to a priority bucket:
         score ≥ 7  → High
         4 ≤ score < 7  → Medium
         score < 4  → Low
    """
    multiplier = SEVERITY_MULTIPLIER.get(severity, 1.0)

    raw_score = sum(
        SYMPTOMS[s]["weight"] for s in selected_symptoms if s in SYMPTOMS
    )
    score = round(raw_score * multiplier, 1)

    has_critical = any(
        SYMPTOMS[s]["critical"] for s in selected_symptoms if s in SYMPTOMS
    )

    # --- determine priority ---
    if severity == "Severe" and has_critical:
        priority = "High"
    elif score >= 7:
        priority = "High"
    elif score >= 4:
        priority = "Medium"
    else:
        priority = "Low"

    return {
        "score": score,
        "priority": priority,
        **PRIORITY_DATA[priority],
        "selected_symptoms": selected_symptoms,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Render the patient intake form."""
    return render_template("index.html", symptoms=list(SYMPTOMS.keys()))


@app.route("/triage", methods=["POST"])
def triage():
    """Process the intake form, store the case, and render the triage result."""
    name     = request.form.get("name", "").strip()
    age      = request.form.get("age", "").strip()
    contact  = request.form.get("contact", "").strip()
    severity = request.form.get("severity", "Mild")
    symptoms = request.form.getlist("symptoms")
    description = request.form.get("description", "").strip()

    result = compute_triage(severity, symptoms)

    # Save to in-memory store so the hospital dashboard can display it
    submissions.append({
        "name":        name,
        "age":         age,
        "contact":     contact,
        "severity":    severity,
        "symptoms":    symptoms,
        "description": description,
        "priority":    result["priority"],
        "score":       result["score"],
        "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    })

    return render_template(
        "result.html",
        name=name,
        age=age,
        contact=contact,
        severity=severity,
        description=description,
        result=result,
    )


@app.route("/hospital")
def hospital():
    """Hospital authority dashboard – lists all patient submissions."""
    return render_template("hospital.html", submissions=submissions)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
