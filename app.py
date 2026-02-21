import os
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from dotenv import load_dotenv
from groq import Groq
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt

load_dotenv()
groq_api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
client = Groq(api_key=groq_api_key) if groq_api_key else None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "dev-smart-triage-key")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smarttriage.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------------------------------------------------------------------------
# Database Models
# ---------------------------------------------------------------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="patient") # 'patient' or 'hospital'
    
    # Optional patient profile info
    name = db.Column(db.String(150))
    age = db.Column(db.String(50))
    contact = db.Column(db.String(150))
    cases = db.relationship('TriageCase', backref='patient', lazy=True)

class TriageCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Form data
    name = db.Column(db.String(150))
    age = db.Column(db.String(50))
    contact = db.Column(db.String(150))
    severity = db.Column(db.String(50))
    symptoms = db.Column(db.Text) # JSON list
    description = db.Column(db.Text)
    
    # AI Results
    priority = db.Column(db.String(50))
    score = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ai_raw_response = db.Column(db.Text) # JSON string of everything else

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Initialize DB
with app.app_context():
    db.create_all()
    
    # Create default hospital admin if it doesn't exist (for testing)
    if not User.query.filter_by(username='admin').first():
        hashed_pw = bcrypt.generate_password_hash('admin123').decode('utf-8')
        admin = User(username='admin', password_hash=hashed_pw, role='hospital', name='Hospital Admin')
        db.session.add(admin)
        db.session.commit()


# ---------------------------------------------------------------------------
# Symptom catalogue – each symptom has a weight used in priority scoring.
# "Critical" symptoms (weight ≥ 3) can trigger High Priority on their own
# when combined with Severe severity.
# ---------------------------------------------------------------------------
SYMPTOMS = {
    "Chest Pain":                                  {"weight": 5, "critical": True},
    "Difficulty Breathing / Shortness of Breath":  {"weight": 5, "critical": True},
    "Sudden Numbness / Weakness (Face, Arm, Leg)": {"weight": 5, "critical": True},
    "Loss of Consciousness / Fainting":            {"weight": 5, "critical": True},
    "Severe Bleeding (Uncontrollable)":            {"weight": 5, "critical": True},
    "Severe Allergic Reaction / Anaphylaxis":      {"weight": 5, "critical": True},
    "Choking / Difficulty Swallowing":             {"weight": 5, "critical": True},
    "Poisoning / Overdose":                        {"weight": 5, "critical": True},
    "Spinal / Head Injury":                        {"weight": 4, "critical": True},
    "Seizures / Convulsions":                      {"weight": 4, "critical": True},
    "High Fever (> 103°F / 39.4°C)":               {"weight": 4, "critical": True},
    "Severe Abdominal Pain":                       {"weight": 4, "critical": True},
    "Sudden Severe Headache":                      {"weight": 4, "critical": True},
    "Persistent Vomiting / Inability to Keep Fluids Down": {"weight": 3, "critical": True},
    "Severe Burns":                                {"weight": 3, "critical": True},
    "Dehydration":                                 {"weight": 3, "critical": False},
    "Bone Fracture (Visible deformity)":           {"weight": 3, "critical": False},
    "Dizziness / Vertigo":                         {"weight": 2, "critical": False},
    "Nausea":                                      {"weight": 2, "critical": False},
    "Joint Pain / Swelling":                       {"weight": 2, "critical": False},
    "Sprain / Strain":                             {"weight": 1, "critical": False},
    "Cough":                                       {"weight": 1, "critical": False},
    "Sore Throat":                                 {"weight": 1, "critical": False},
    "Rash / Skin Irritation":                      {"weight": 1, "critical": False},
    "Mild Headache":                               {"weight": 1, "critical": False},
    "Mild Cut / Scrape":                           {"weight": 1, "critical": False},
    "Fatigue":                                     {"weight": 1, "critical": False},
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
def compute_triage(severity: str, selected_symptoms: list[str], description: str) -> dict:
    """
    Computes a triage priority using Google Gemini API based on patient symptoms.
    """
    prompt = f"""
    You are an expert AI triage assistant in a hospital emergency system.
    Evaluate the following patient:
    - Symptoms Selected: {', '.join(selected_symptoms) if selected_symptoms else 'None'}
    - Self-reported Severity: {severity}
    - Additional Description: {description}
    
    Classify the patient into one of 4 severity levels:
    - Green (Normal - Low Priority)
    - Yellow (Monitor - Monitor closely)
    - Orange (Urgent - Medium Priority)
    - Red (Immediate Attention - High Priority)
    
    Respond ONLY in valid JSON format matching this exact structure:
    {{
        "priority_color": "Red", // Or "Orange", "Yellow", "Green"
        "priority_label": "Immediate Attention",
        "score": 9.5, // float 1-10
        "summary": "Brief 1-sentence patient condition summary",
        "probable_diagnosis": "Probable diagnosis based on symptoms",
        "risk_factors": "Key risk factors to watch",
        "recommended_department": "Emergency/ICU/General/etc.",
        "medical_description": "Detailed explanation of what might be happening",
        "risk_explanation": "Why this is classified at this level",
        "immediate_actions": ["Action 1", "Action 2"],
        "medication_suggestions_disclaimer": "⚠️ This is AI-generated guidance. Do not take medication without consulting a doctor.",
        "medication_suggestions": ["Suggestion 1 (e.g., Paracetamol for fever)", "Suggestion 2"]
    }}
    """
    
    try:
        if not client:
            raise ValueError("Groq API key is missing.")
            
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        ai_data = json.loads(chat_completion.choices[0].message.content)
        
        # Map priority label backward compatibility for the hospital dashboard
        p_color = ai_data.get("priority_color", "Yellow")
        mapped_priority = "High" if p_color == "Red" else ("Medium" if p_color in ["Orange", "Yellow"] else "Low")
        
        return {
            **ai_data,
            "selected_symptoms": selected_symptoms,
            "priority": mapped_priority,
            "color": p_color.lower(),
            "instructions": ai_data.get("immediate_actions", []),
            "next_steps": [f"Go to {ai_data.get('recommended_department', 'Reception')}"],
            "reassurance": ai_data.get("risk_explanation", "")
        }
    except Exception as e:
        print(f"AI Triage Error: {e}")
        # Fallback if API fails or key is missing
        return {
            "score": 5.0,
            "priority": "Medium",
            "priority_color": "Yellow",
            "color": "yellow",
            "priority_label": "System Error - Manual Triage Required",
            "summary": "AI classification failed because the Groq API key is missing or invalid in your .env file.",
            "probable_diagnosis": "Offline Mode",
            "risk_factors": "Unknown",
            "recommended_department": "Triage",
            "medical_description": "Failed to connect to AI. Please add a valid API key to enable Smart Triage.",
            "risk_explanation": "Please consult a doctor manually or add an API key.",
            "immediate_actions": ["Consult a doctor immediately if symptoms worsen."],
            "instructions": ["Consult a doctor immediately if symptoms worsen."],
            "next_steps": ["Wait for a triage nurse."],
            "medication_suggestions_disclaimer": "",
            "medication_suggestions": [],
            "reassurance": "Our medical team will assess you shortly.",
            "selected_symptoms": selected_symptoms,
        }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def landing():
    """Render the landing page."""
    return render_template("landing.html")

@app.route("/patient/intake")
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

    result = compute_triage(severity, symptoms, description)

    # Save to SQLite database
    case = TriageCase(
        patient_id=current_user.id if current_user.is_authenticated else None,
        name=name, age=age, contact=contact, severity=severity,
        symptoms=json.dumps(symptoms), description=description,
        priority=result["priority"], score=result["score"],
        ai_raw_response=json.dumps(result)
    )
    db.session.add(case)
    db.session.commit()

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
    """Hospital authority dashboard – lists all patient submissions (public)."""
    cases = TriageCase.query.order_by(TriageCase.timestamp.desc()).all()
    
    # Format them for the template
    submissions = []
    for c in cases:
        ai_data = json.loads(c.ai_raw_response) if c.ai_raw_response else {}
        submissions.append({
            "id": c.id,
            "name": c.name, "age": c.age, "contact": c.contact,
            "severity": c.severity,
            "symptoms": json.loads(c.symptoms) if c.symptoms else [],
            "description": c.description,
            "priority": c.priority, "score": c.score,
            "timestamp": c.timestamp.strftime("%Y-%m-%d %H:%M"),
            **ai_data
        })
        
    return render_template("hospital.html", submissions=submissions)


@app.route("/hospital/delete/<int:case_id>", methods=["POST"])
def delete_case(case_id):
    """Delete a triage case from the database."""
    case = TriageCase.query.get(case_id)
    if case:
        db.session.delete(case)
        db.session.commit()
    return redirect(url_for('hospital'))


@app.route("/login", methods=["GET", "POST"])
def login():
    """User login."""
    if current_user.is_authenticated:
        return redirect(url_for('landing'))
        
    if request.method == "POST":
        username = request.form.get("username").strip()
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            flash("Login successful.", "success")
            if user.role == 'hospital':
                return redirect(url_for('hospital'))
            return redirect(url_for('patient_dashboard'))
        else:
            flash("Invalid username or password.", "danger")
            
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('landing'))


@app.route("/patient/history", methods=["POST"])
def patient_history():
    """View past cases by contact number without needing an account."""
    contact = request.form.get("contact", "").strip()
    if not contact:
        flash("Please enter a valid contact number.", "danger")
        return redirect(url_for('index'))
        
    cases = TriageCase.query.filter_by(contact=contact).order_by(TriageCase.timestamp.desc()).all()
    
    if not cases:
        flash("No history found for this contact number.", "info")
        return redirect(url_for('index'))
        
    # Format them for template
    submissions = []
    for c in cases:
        ai_data = json.loads(c.ai_raw_response) if c.ai_raw_response else {}
        submissions.append({
            "name": c.name, "age": c.age, "contact": c.contact,
            "severity": c.severity, "symptoms": json.loads(c.symptoms) if c.symptoms else [],
            "description": c.description, "priority": c.priority, "score": c.score,
            "timestamp": c.timestamp.strftime("%Y-%m-%d %H:%M"),
            **ai_data
        })
        
    return render_template("patient_dashboard.html", submissions=submissions, contact=contact)


# ---------------------------------------------------------------------------
# API Endpoints (Phase 3)
# ---------------------------------------------------------------------------

@app.route("/api/symptoms", methods=["GET"])
def api_symptoms():
    """Returns the catalogue of available symptoms."""
    return jsonify({"symptoms": list(SYMPTOMS.keys())})


@app.route("/api/recommendation", methods=["POST"])
def api_recommendation():
    """Generates an AI triage recommendation based on a JSON payload."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid or missing JSON payload"}), 400
        
    severity = data.get("severity", "Mild")
    symptoms = data.get("symptoms", [])
    description = data.get("description", "").strip()
    
    result = compute_triage(severity, symptoms, description)
    return jsonify(result)


@app.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    """Conversational endpoint for a patient dealing with a medical assistant."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid or missing JSON payload"}), 400
        
    message = data.get("message", "").strip()
    history = data.get("history", []) # List of dicts: {"role": "user"|"model", "parts": "..."}
    
    if not message:
        return jsonify({"error": "Message is required"}), 400
        
    try:
        if not client:
            raise ValueError("Groq API key is missing.")
            
        system_prompt = (
            "You are a helpful, empathetic medical AI assistant for the Smart Triage system. "
            "You provide general health guidance and advise users to seek professional medical "
            "help for serious conditions. Keep answers concise, supportive, and extremely professional."
        )
        
        groq_history = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = "assistant" if msg.get("role") == "model" else "user"
            content = msg.get("parts", "")
            if isinstance(content, list): 
                content = content[0] if content else ""
            groq_history.append({"role": role, "content": content})
            
        groq_history.append({"role": "user", "content": message})
        
        chat_completion = client.chat.completions.create(
            messages=groq_history,
            model="llama-3.3-70b-versatile"
        )
        response_text = chat_completion.choices[0].message.content
        return jsonify({"response": response_text})
        
    except Exception as e:
        print(f"Chatbot API Error: {e}")
        # Fallback if API fails or key is missing
        fallback_msg = "Hello! I am currently running in offline mode because the Groq API key is missing or invalid. Please add a valid GROQ_API_KEY to the .env file to enable AI responses."
        return jsonify({"response": fallback_msg})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
