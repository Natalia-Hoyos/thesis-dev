# Gladys 🤖

**Gladys** is a social companion robot designed to support cognitive health and reduce social isolation in older adults.

Developed as part of a QUT engineering thesis in collaboration with [Stones Corner AI](https://stonescornerai.com), 
Gladys delivers engaging, voice-based interactions through natural conversation, cognitive games, and reminiscence activities.

---

## What Gladys Does

Gladys interacts with users through natural voice conversation and can deliver a range of evidence-based cognitive health activities:

- **Social conversation** — checks in on the user's wellbeing and engages in friendly, natural dialogue
- **Cognitive games** — runs voice-based trivia and word games at adjustable difficulty levels

A connected screen (TV or monitor) provides optional visual scaffolding to support activities like crosswords or show visual prompts.

---

## Tech Stack

- **Voice agent framework:** [LiveKit Agents](https://github.com/livekit/agents)
- **Language:** Python
- **LLM:** External LLM API (via LiveKit Agents pipeline)
---

## How It Works

Gladys uses a **hybrid approach** for cognitive activities:

- **Deterministic selection** — questions are pulled from a curated static bank using tracked index sets per difficulty, ensuring no repeats within a session
- **Conversational LLM delivery** — Gladys handles natural dialogue, follow-up, encouragement, and transitions, making interactions feel warm and engaging rather than mechanical

This separates reliability (question selection) from naturalness (conversation), capturing the benefits of both.

---

## Getting Started

### Prerequisites

- Python 3.9+
- LiveKit Agents SDK
- API key for your chosen LLM provider

---

## Design Goals

Gladys is specifically designed for older adult users. Key design principles include:

- **Voice-first** — no typing required; interaction is entirely conversational
- **Low cognitive load** — clear, simple language; optional screen support for visual context
- **Warmth** — Gladys is friendly, patient, and encouraging

---

## Research Context

This project is part of a QUT engineering thesis investigating:

> *"How can a social companion robot be designed and implemented to deliver cognitive health-supporting activities to older adults through natural conversation and interactive games?"*

**Supervisors & Partners:**
- Academic supervisor: Associate Professor Laurianne Sitbon (QUT)
- Industry partners: Dr. Josh Arnold & Dr. Scott Heath (Stones Corner AI)

---

## Status

> ⚠️ This project is under active development as part of a 2026 thesis. Expect frequent changes.

| Feature | Status |
|---|---|
| Voice conversation | ✅ In progress |
| Trivia game | ✅ In progress |

---

## License

This project is developed for academic research purposes. License TBD.
