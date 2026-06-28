import os
import uuid
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
LOG_FILE = "audit_log.json"

def read_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return json.load(f)

def append_log(entry):
    entries = read_log()
    entries.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)

def llm_classify(text):
    prompt = f"""Analyze this text. Respond with ONLY this exact JSON format, nothing else:
{{"score": 0.0, "reasoning": "one sentence here"}}

Replace 0.0 with a float between 0.0 (human) and 1.0 (AI).
Replace the reasoning with one sentence. Use only plain ASCII characters.

Text: {text}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    print(f"DEBUG Groq: {repr(raw)}")

    # Clean code fences
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    # Extract just the score using a fallback if JSON fails
    try:
        result = json.loads(raw)
        score = float(result["score"])
    except (json.JSONDecodeError, KeyError):
        # Fallback: find the score value directly
        import re
        match = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        if match:
            score = float(match.group(1))
        else:
            score = 0.5  # default to uncertain if all else fails

    return max(0.0, min(1.0, score))

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()
    if not data or "text" not in data or "creator_id" not in data:
        return jsonify({"error": "missing fields"}), 400
    text = data["text"]
    creator_id = data["creator_id"]
    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    llm_score = llm_classify(text)
    confidence = round(llm_score, 4)
    attribution = "likely_ai" if confidence > 0.65 else ("likely_human" if confidence < 0.35 else "uncertain")
    label = "Likely AI-generated" if confidence > 0.65 else ("Likely human-written" if confidence < 0.35 else "Uncertain")
    append_log({"content_id": content_id, "creator_id": creator_id, "timestamp": timestamp,
                "attribution": attribution, "confidence": confidence, "llm_score": llm_score,
                "stylo_score": None, "status": "classified"})
    return jsonify({"content_id": content_id, "attribution": attribution,
                    "confidence": confidence, "label": label, "status": "classified"})

@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": read_log()})

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
