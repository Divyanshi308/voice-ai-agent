# VoiceShield AI

**Multilingual Voice AI Customer Support Agent**

Built for the [EchoSphere: Agora Conversational AI Hackathon 2026](https://unstop.com/hackathons/echosphere-agora-conversational-ai-hackathon-knotic-1723695)

---

## Problem

Customer service phone lines have long wait times, language barriers, and inconsistent support quality. In India alone, 67% of customers abandon calls due to poor experience. Existing solutions are either expensive human-only systems or basic chatbots that can't handle real conversations.

## Solution

VoiceShield AI is a real-time multilingual voice agent that:

- **Speaks** your language (Hindi, English, Hinglish)
- **Listens** actively with interruption handling (barge-in)
- **Remembers** context across the entire conversation
- **Acts** by creating support tickets and sending SMS notifications
- **Escalates** to human agents when needed

---

## Architecture

```
┌─────────┐     ┌──────────┐     ┌───────────┐     ┌─────────┐     ┌───────────┐
│  User   │────▶│ Agora RTC│────▶│  Convo AI │────▶│  GPT-4o │────▶│ ElevenLabs│
│ (Voice) │◀────│   SDK    │◀────│  Engine   │◀────│   LLM   │◀────│    TTS    │
└─────────┘     └──────────┘     └───────────┘     └─────────┘     └───────────┘
                     │                                    │
                     ▼                                    ▼
              ┌──────────────┐                    ┌──────────────┐
              │ Deepgram STT │                    │  Guardrails  │
              │ (Speech→Text)│                    │  (Safety)    │
              └──────────────┘                    └──────────────┘
                                                          │
                     ┌────────────────────────────────────┤
                     ▼                                    ▼
              ┌──────────────┐                    ┌──────────────┐
              │   Zendesk    │                    │    Twilio    │
              │  (Tickets)   │                    │   (SMS)      │
              └──────────────┘                    └──────────────┘
```

---

## Features

### Core Voice Features

| Feature | Description | Status |
|---------|-------------|--------|
| **Real-time Conversation** | Live voice chat via Agora RTC | ✅ |
| **Interruption Handling** | AI stops when user speaks (barge-in) | ✅ |
| **Streaming Response** | AI speaks while still generating | ✅ |
| **Instant Acknowledgment** | "Let me check that..." within 200ms | ✅ |
| **Backchanneling** | "mhm", "I see", "go on" while listening | ✅ |
| **Context Memory** | Remembers earlier conversation points | ✅ |
| **Emotional Intelligence** | Voice adapts to user sentiment | ✅ |
| **Multilingual** | Hindi + English + Hinglish | ✅ |

### Safety & Intelligence

| Feature | Description |
|---------|-------------|
| **Guardrails** | Blocks medical/legal advice |
| **Emergency Detection** | Auto-escalates to 112/911 |
| **Sentiment Analysis** | Detects frustrated/angry/calm users |
| **Confidence Tracking** | Knows when audio quality is poor |
| **Human Escalation** | Transfers to human when needed |

### Integrations

| Service | Purpose |
|---------|---------|
| **Agora RTC** | Real-time voice transmission |
| **Agora Convo AI** | STT → LLM → TTS orchestration |
| **OpenAI GPT-4o** | AI brain for conversation |
| **ElevenLabs** | Natural text-to-speech |
| **Deepgram** | Speech-to-text |
| **Zendesk** | Support ticket creation |
| **Twilio** | SMS notifications |

---

## Tech Stack

```
Frontend:  HTML5, CSS3, JavaScript, Three.js, Agora Web SDK
Backend:   Python 3.11+, FastAPI, WebSocket
AI:        OpenAI GPT-4o, ElevenLabs TTS, Deepgram STT
Voice:     Agora Conversational AI Engine, Agora RTC SDK
Database:  PostgreSQL, Redis
Hosting:   Render (free tier)
```

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/Divyanshi308/voice-ai-agent.git
cd voice-ai-agent
```

### 2. Install

```bash
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 3. Configure

Copy `.env.example` to `.env` and add your API keys:

```env
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate
OPENAI_API_KEY=your_openai_key
ELEVENLABS_API_KEY=your_elevenlabs_key
DEEPGRAM_API_KEY=your_deepgram_key
```

### 4. Run

```bash
python main.py
```

Open http://localhost:8000

---

## Demo

**Live Demo:** https://voice-ai-agent.onrender.com

### What to Try

1. **Click the microphone** → Start a voice conversation
2. **Say "I need help with my bill"** → AI responds with empathy
3. **Interrupt the AI** → It stops and listens (barge-in)
4. **Say "my name is Rahul"** → AI remembers your name
5. **Say "what's my name?"** → AI recalls from context

---

## How Agora Is Used

### Agora Conversational AI Engine
- Primary orchestration layer for STT → LLM → TTS pipeline
- Handles real-time voice capture and delivery
- Manages interruption detection and response

### Agora RTC SDK
- Real-time audio transmission between client and server
- Low-latency voice streaming (< 500ms)
- Connection quality monitoring

### Integration Flow

```
1. User speaks into microphone
2. Agora RTC captures audio → sends to server
3. Agora Convo AI Engine processes:
   a. Deepgram converts speech to text
   b. GPT-4o generates response
   c. ElevenLabs converts text to speech
4. Agora RTC streams audio back to user
5. Total latency: < 800ms end-to-end
```

---

## Project Structure

```
voice-ai-agent/
├── main.py                 # FastAPI server + WebSocket handlers
├── pipeline.py             # Core conversation pipeline with interruption handling
├── llm.py                  # GPT-4o engine with streaming
├── tts.py                  # ElevenLabs TTS with emotional voice
├── asr.py                  # Deepgram speech-to-text
├── guardrails.py           # Safety guardrails
├── dialogue.py             # Conversation state manager
├── ticketing.py            # Zendesk integration
├── notifications.py        # Twilio SMS
├── analytics.py            # PostgreSQL + Redis logging
├── config.py               # Pydantic settings
├── agora_integration.py    # Agora token generation
├── static/
│   └── index.html          # 3D dashboard with voice interface
├── tests/
│   └── test_pipeline.py    # Unit tests
├── Dockerfile              # Container build
├── requirements.txt        # Python dependencies
└── .env                    # Environment variables
```

---

## Known Limitations

- Free tier has 300-second call limit
- Requires microphone permission in browser
- Voice quality depends on network latency
- Agora free tier has limited concurrent users

---

## Team

- **Divyanshi** - Developer

---

## Acknowledgments

- [Agora](https://www.agora.io) for Conversational AI Platform
- [OpenAI](https://openai.com) for GPT-4o
- [ElevenLabs](https://elevenlabs.io) for Natural TTS
- [Deepgram](https://deepgram.com) for Speech-to-Text
- [KNOTiC](https://knotichq.com) for organizing EchoSphere 2026

---

## License

MIT
