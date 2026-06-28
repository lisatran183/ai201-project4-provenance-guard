import os
import re
import uuid
import json
import math
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
LOG_FILE = "audit_log.json"

# ── Audit log helpers ────────────────────────────────────────────────────────

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

# ── Signal 1: LLM Classifier (Groq) ─────────────────────────────────────────

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
    print(f"DEBUG LLM raw: {repr(raw)}")

    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()

    try:
        result = json.loads(raw)
        score = float(result["score"])
    except (json.JSONDecodeError, KeyError):
        match = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        score = float(match.group(1)) if match else 0.5

    return max(0.0, min(1.0, score))

# ── Signal 2: Stylometric Analyzer ──────────────────────────────────────────

def stylometric_classify(text):
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    words = text.split()
    
    if len(sentences) < 2 or len(words) < 10:
        print("DEBUG Stylo: text too short, returning 0.5")
        return 0.5

    # Metric 1: Sentence length variance
    # AI text tends to have uniform sentence lengths (low variance)
    lengths = [len(s.split()) for s in sentences]
    mean_len = sum(lengths) / len(lengths)
    variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
    std_dev = math.sqrt(variance)
    # Normalize: low std_dev (uniform) → high AI score
    length_score = max(0.0, min(1.0, 1.0 - (std_dev / 8.0)))

    # Metric 2: Type-token ratio (vocabulary diversity)
    unique_words = set(w.lower().strip('.,!?;:"\'') for w in words)
    ttr = len(unique_words) / len(words)
    # Adjusted: AI typically has TTR around 0.6-0.75
    ttr_score = max(0.0, min(1.0, 1.0 - (ttr / 0.65)))

    # Metric 3: Punctuation density
    # AI text tends to have moderate, predictable punctuation
    punct_count = sum(1 for c in text if c in '.,;:!?-()[]')
    punct_density = punct_count / len(words)
    # Very low or very high punctuation → more human
    # Moderate punctuation (0.1-0.2 per word) → more AI
    punct_score = max(0.0, min(1.0, 1.0 - abs(punct_density - 0.15) / 0.15))

    # Metric 4: Average sentence complexity
    # AI tends toward consistently medium-length sentences
    avg_len = mean_len
    # Very short (<8) or very long (>25) avg → more human
    # Medium avg (12-18) → more AI
    complexity_score = max(0.0, min(1.0, 1.0 - abs(avg_len - 15) / 15.0))

    # Combine 4 metrics into one stylo score
    stylo_score = (
        0.35 * length_score +
        0.30 * ttr_score +
        0.20 * punct_score +
        0.15 * complexity_score
    )

    print(f"DEBUG Stylo: length={length_score:.2f} ttr={ttr_score:.2f} "
          f"punct={punct_score:.2f} complexity={complexity_score:.2f} "
          f"combined={stylo_score:.2f}")

    return round(max(0.0, min(1.0, stylo_score)), 4)

# ── Confidence Scoring: Option B (Agreement-Sensitive Blending) ──────────────

def combine_scores(llm_score, stylo_score):
    diff = abs(llm_score - stylo_score)

    if diff <= 0.2:
        # Signals agree: weighted average (LLM 60%, stylo 40%)
        combined = 0.6 * llm_score + 0.4 * stylo_score
    else:
        # Signals disagree: force into uncertain band
        avg = (llm_score + stylo_score) / 2
        combined = max(0.4, min(0.6, avg))

    print(f"DEBUG Confidence: llm={llm_score:.2f} stylo={stylo_score:.2f} "
          f"diff={diff:.2f} combined={combined:.2f}")

    return round(combined, 4)

# ── Transparency Label ───────────────────────────────────────────────────────

def get_label(confidence):
    if confidence > 0.65:
        return (
            "Our system found strong indicators that this content was AI-generated. "
            "This label is based on automated analysis and may not be perfect. "
            "If you are the creator and believe this is wrong, you can submit an appeal."
        )
    elif confidence < 0.35:
        return (
            "Our system found strong indicators that this content was written by a human. "
            "Automated analysis is not perfect — if you have concerns, "
            "you can flag this content for review."
        )
    else:
        return (
            "Our system could not confidently determine whether this content was written "
            "by a human or generated by AI. It may be a mix of both, or the writing style "
            "may have unusual characteristics. If you are the creator, you can provide "
            "context through an appeal."
        )

def get_attribution(confidence):
    if confidence > 0.65:
        return "likely_ai"
    elif confidence < 0.35:
        return "likely_human"
    return "uncertain"

# ── Routes ───────────────────────────────────────────────────────────────────

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
    stylo_score = stylometric_classify(text)
    confidence = combine_scores(llm_score, stylo_score)
    attribution = get_attribution(confidence)
    label = get_label(confidence)

    append_log({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "status": "classified"
    })

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "label": label,
        "status": "classified"
    })

@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": read_log()})

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
