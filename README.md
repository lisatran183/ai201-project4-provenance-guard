# Provenance Guard

A backend API that classifies submitted creative writing as likely AI-generated
or likely human-written, scores confidence in that classification, surfaces a
plain-language transparency label, and provides an appeals workflow for
contested decisions.

---

## Architecture Overview

A submitted piece of text travels through six components:

1. **POST /submit endpoint** — receives `text` and `creator_id`, assigns a
   unique `content_id`
2. **Signal 1: LLM Classifier (Groq)** — sends text to `llama-3.3-70b-versatile`
   and returns a score from 0.0 (human) to 1.0 (AI)
3. **Signal 2: Stylometric Analyzer** — computes four statistical properties
   of the text and returns a combined score from 0.0 to 1.0
4. **Confidence Scoring Engine** — applies Option B agreement-sensitive
   blending to combine both signals into a single confidence score
5. **Transparency Label Generator** — maps the confidence score to one of
   three plain-language label variants
6. **Audit Log** — writes a structured JSON entry capturing all signals,
   scores, and status for every submission and appeal

Appeals flow separately: POST /appeal looks up the original entry by
`content_id`, updates its status to `under_review`, appends an appeal event
entry to the audit log, and returns a confirmation.

---

## Detection Signals

### Signal 1: LLM Classifier (Groq — llama-3.3-70b-versatile)

**What it measures:** Semantic and stylistic coherence holistically — phrasing
patterns, hedging language, vocabulary choices, and narrative structure. It
reads the overall "feel" of the text.

**Why it differs between human and AI writing:** AI-generated text tends toward
smooth, evenly-paced prose with consistent register. Human writing is more
variable — it drifts, uses idiom inconsistently, and reflects a single person's
voice rather than a statistical average.

**What it misses:** It is a black box. It cannot explain why it scored a piece
the way it did, and it can be confidently wrong — especially on text that was
human-written but heavily polished or edited.

### Signal 2: Stylometric Analyzer (pure Python)

**What it measures:** Four statistical properties of the text's structure:
- Sentence length variance — how much sentence lengths vary across the piece
- Type-token ratio (TTR) — vocabulary diversity (unique words / total words)
- Punctuation density — punctuation marks per word
- Average sentence complexity — average words per sentence

**Why it differs between human and AI writing:** AI text tends to be
statistically uniform — consistent sentence lengths, moderate vocabulary
diversity, predictable punctuation. Human writing is more variable and
irregular.

**What it misses:** Formal human writers — academics, legal writers, non-native
English speakers writing carefully — produce text that looks statistically
similar to AI output. This signal will over-flag polished human writing.

---

## Confidence Scoring

### Approach: Option B — Agreement-Sensitive Blending

The two signals are combined based on whether they agree or disagree:

- **If signals agree** (|llm_score - stylo_score| <= 0.2):
  `confidence = 0.6 * llm_score + 0.4 * stylo_score`
- **If signals disagree** (|llm_score - stylo_score| > 0.2):
  `confidence = clamp(average, 0.4, 0.6)`

When signals disagree significantly, the score is forced into the uncertain
band regardless of individual values. Disagreement between signals is itself
evidence of uncertainty — the honest answer is "we don't know" rather than
letting one signal override the other.

LLM gets 60% weight because it captures semantic meaning that stylometrics
cannot. But when they diverge, neither should dominate.

### Thresholds

| Confidence Range | Label Category       |
|------------------|----------------------|
| 0.00 – 0.34      | Likely human-written |
| 0.35 – 0.65      | Uncertain            |
| 0.66 – 1.00      | Likely AI-generated  |

### Validation — Two Example Submissions

**High-confidence case (AI text):**
```json
{
  "text": "Artificial intelligence represents a transformative paradigm shift...",
  "llm_score": 0.8,
  "stylo_score": 0.4898,
  "confidence": 0.6,
  "attribution": "uncertain"
}
```

**Lower-confidence case (human text):**
```json
{
  "text": "ok so i finally tried that new ramen place downtown and honestly?...",
  "llm_score": 0.0,
  "stylo_score": 0.3609,
  "confidence": 0.4,
  "attribution": "uncertain"
}
```

The LLM signal correctly scores the AI text at 0.8 and the casual human text
at 0.0 — a meaningful difference of 0.8 between the two. The stylometric
signal is more conservative, producing scores in the 0.35–0.50 range for both.
Because the two signals disagree by more than 0.2 in both cases, Option B
forces the combined score into the uncertain band. This is the correct behavior:
the system acknowledges uncertainty rather than committing to a wrong answer.

---

## Transparency Label

All three variants are shown exactly as they appear in API responses:

**High-confidence AI (confidence > 0.65):**
> "Our system found strong indicators that this content was AI-generated.
> This label is based on automated analysis and may not be perfect.
> If you are the creator and believe this is wrong, you can submit an appeal."

**Uncertain (confidence 0.35 – 0.65):**
> "Our system could not confidently determine whether this content was written
> by a human or generated by AI. It may be a mix of both, or the writing style
> may have unusual characteristics. If you are the creator, you can provide
> context through an appeal."

**High-confidence human (confidence < 0.35):**
> "Our system found strong indicators that this content was written by a human.
> Automated analysis is not perfect — if you have concerns, you can flag this
> content for review."

---

## Rate Limiting

**Limits chosen:** 10 requests per minute, 100 requests per day per IP address.

**Reasoning:** A legitimate creator submitting their own work would rarely
send more than 1–2 submissions per minute — most writing sessions involve
drafting, not rapid-fire submission. 10 per minute gives comfortable headroom
for normal use while blocking scripts that flood the system. 100 per day
reflects a reasonable upper bound for even a very active user across a full day.

**Rate limit test output** (12 rapid requests, limit is 10/minute):

<img width="126" height="176" alt="Screenshot 2026-06-27 at 6 04 41 PM" src="https://github.com/user-attachments/assets/68e95c1b-5be0-48ec-a218-78a7cbdc4c12" />

---

## Appeals Workflow

Creators can contest a classification by submitting a POST /appeal request
with their `content_id` and `creator_reasoning`. The system updates the
original entry status to `under_review`, appends a separate appeal event
to the audit log, and returns a confirmation.

**Example appeal request:**
```json
{
  "content_id": "f8a1c6d6-f9e5-48cd-b099-ea8b957311ad",
  "creator_reasoning": "I wrote this myself from personal experience.
  I am a non-native English speaker and my writing style may appear
  more formal than typical."
}
```

**Example appeal response:**
```json
{
  "content_id": "f8a1c6d6-f9e5-48cd-b099-ea8b957311ad",
  "message": "Your appeal has been received and the content is now under review.",
  "status": "appeal_received"
}
```

---

## Audit Log

Every submission and appeal is written to `audit_log.json`. Sample entries:

```json
[
  {
    "content_id": "f8a1c6d6-f9e5-48cd-b099-ea8b957311ad",
    "creator_id": "test-user-1",
    "timestamp": "2026-06-28T01:02:18.105421+00:00",
    "attribution": "uncertain",
    "confidence": 0.5763,
    "llm_score": 0.8,
    "stylo_score": 0.3525,
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself from personal experience...",
    "appeal_timestamp": "2026-06-28T01:03:10.624242+00:00"
  },
  {
    "event": "appeal_filed",
    "content_id": "f8a1c6d6-f9e5-48cd-b099-ea8b957311ad",
    "appeal_timestamp": "2026-06-28T01:03:10.624242+00:00",
    "appeal_reasoning": "I wrote this myself from personal experience...",
    "original_attribution": "uncertain",
    "original_confidence": 0.5763,
    "status": "under_review"
  }
]
```

---

## Known Limitations

**Non-native English speakers writing formally** are the most dangerous failure
mode. A carefully written essay by a non-native speaker will have low sentence
length variance and measured vocabulary — both properties the stylometric signal
associates with AI. The LLM signal may also score it moderately high if the
phrasing is unusually smooth. Both signals can agree incorrectly, producing a
high-confidence false positive. This is tied directly to the stylometric
signal's inability to distinguish between "uniform because AI" and "uniform
because careful."

**Very short submissions** (under 50 words) give the stylometric signal almost
nothing to work with. TTR is artificially high for short text, and sentence
length variance is meaningless with fewer than 3 sentences. The system returns
0.5 for short text and falls back entirely to the LLM signal, which increases
false positive risk.

---

## Spec Reflection

**One way the spec helped:** Designing the confidence scoring approach in
planning.md before writing any code meant the Option B blending logic was
completely specified before implementation. When the two signals kept
disagreeing in testing, the spec gave a clear answer for what to do — force
to uncertain — rather than having to make a judgment call mid-implementation.

**One way implementation diverged:** The planning.md spec assumed the
stylometric signal would produce scores in a similar range to the LLM signal,
allowing meaningful weighted averaging. In practice, the stylometric signal
consistently scores in the 0.25–0.55 range regardless of how clearly AI or
human the text is, while the LLM signal spans the full 0.0–0.9 range. This
means Option B's disagreement clause triggers on almost every submission,
making the system more conservative than planned. This is defensible behavior
but it means the stylometric signal contributes less to final scores than
the spec intended.

---

## AI Usage

**Instance 1: Flask app skeleton and LLM classifier function**
I provided the Detection Signals section of planning.md and the architecture
diagram to Claude and asked it to generate the Flask app skeleton with a POST
/submit route stub and the first signal function. The output included the
correct route structure and a working Groq API call. I revised the JSON
parsing logic because the initial version used a simple `json.loads()` call
that failed when Groq returned malformed JSON with escaped apostrophes. I
added a regex fallback (`re.search(r'"score"\s*:\s*([0-9.]+)', raw)`) to
extract the score even when the full JSON parse fails.

**Instance 2: Stylometric analyzer and confidence scoring**
I provided the Detection Signals section, Uncertainty Representation section,
and architecture diagram and asked Claude to generate the stylometric analyzer
function and the Option B confidence scoring logic. The generated scoring
function matched the spec thresholds correctly. I revised the stylometric
metric weights after testing — the initial equal weighting produced scores
that were too uniform, so I adjusted to 0.35 / 0.30 / 0.20 / 0.15 to give
sentence length variance more influence, since it is the most reliable
differentiator between AI and human text in practice.
