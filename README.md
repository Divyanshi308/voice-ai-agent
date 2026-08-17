# VoxAssist

**AI That Speaks, Listens, and Cares**

Built for the [EchoSphere: Agora Conversational AI Hackathon 2026](https://unstop.com/hackathons/echosphere-agora-conversational-ai-hackathon-knotic-1723695)

---

## Problem

In India, over 150 million elderly people live alone or with aging spouses. They struggle with:
- **Medicine management** - Forgetting to take medications on time
- **Daily tasks** - Difficulty with grocery shopping, bill payments, appointments
- **Emergency situations** - No one to call when they need immediate help
- **Loneliness** - Lack of companionship and social interaction

Existing solutions are either expensive human caregivers or basic reminder apps that can't hold real conversations.

## Solution

VoxAssist is a multilingual voice AI agent that:
- **Speaks naturally** in Hindi, English, or Hinglish
- **Remembers** your medications, appointments, and preferences
- **Detects emergencies** and provides immediate guidance
- **Adapts** its voice tone based on your emotional state
- **Interrupts gracefully** - you can barge in anytime

---

## Architecture

```
┌─────────────┐     ┌──────────┐     ┌──────────────┐     ┌─────────┐
│   Elderly   │────▶│  Agora   │────▶│   Agora      │────▶│  GPT-4o │
│   User      │◀────│   RTC    │◀────│   ConvoAI    │◀────│   LLM   │
│   (Voice)   │     │   SDK    │     │   Engine     │     │         │
└─────────────┘     └──────────┘     └──────────────┘     └─────────┘
                           │                  │
                           │                  │
                     ┌─────┴──────┐     ┌─────┴──────┐
                     │  Deepgram  │     │ ElevenLabs │
                     │  STT       │     │  TTS       │
                     └────────────┘     └────────────┘
```

### How It Works

1. **User speaks** into their phone/device
2. **Agora RTC** captures audio with low latency (<500ms)
3. **Agora ConvoAI Engine** orchestrates the pipeline:
   - Deepgram converts speech to text
   - GPT-4o generates appropriate response
   - ElevenLabs converts text to natural speech
4. **Response streams** back through Agora RTC
5. **Total latency**: <800ms end-to-end

### Key Agora Features Used

| Feature | Purpose |
|---------|---------|
| **Agora ConvoAI Engine** | Orchestrates STT → LLM → TTS pipeline |
| **Agora RTC SDK** | Real-time audio streaming |
| **Interruption Detection** | User can barge in while AI speaks |
| **Turn Detection** | Knows when user starts/stops speaking |
| **Multilingual STT** | Hindi + English + Hinglish recognition |

---

## Features

### Voice Features
- **Real-time conversation** via Agora Conversational AI
- **Interruption handling** - barge in anytime
- **Streaming response** - AI speaks while generating
- **Multilingual** - Hindi, English, Hinglish
- **Natural voice** - ElevenLabs emotional TTS

### Safety Features
- **Emergency detection** - Auto-provides emergency numbers
- **Medicine reminders** - Tracks medication schedules
- **Guardrails** - Blocks harmful advice
- **Human escalation** - Transfers to real person when needed

### Intelligence Features
- **Context memory** - Remembers conversation history
- **Sentiment analysis** - Detects emotional state
- **Intent classification** - Understands what user wants
- **Confidence tracking** - Knows when audio is unclear

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
# REQUIRED - Agora Conversational AI
AGORA_APP_ID=your_agora_app_id
AGORA_APP_CERTIFICATE=your_agora_app_certificate

# Optional - BYOK (Bring Your Own Keys)
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
2. **Say "Namaste"** → AI responds in Hindi
3. **Say "I need my medicine"** → AI helps with medicine reminder
4. **Interrupt the AI** → It stops and listens (barge-in)
5. **Switch languages** → AI follows your language choice

---

## Tech Stack

```
Frontend:  HTML5, CSS3, JavaScript, Three.js
Backend:   Python 3.11+, FastAPI
Voice:     Agora Conversational AI Engine
STT:       Deepgram (via Agora)
LLM:       OpenAI GPT-4o
TTS:       ElevenLabs (via Agora)
Hosting:   Render (free tier)
```

---

## Project Structure

```
voice-ai-agent/
├── main.py                 # FastAPI server + Agora integration
├── config.py               # Pydantic settings
├── static/
│   └── index.html          # 3D dashboard with voice interface
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container build
├── README.md               # This file
└── .env                    # Environment variables (not committed)
```

---

## How Agora Is Used

### Primary Platform: Agora Conversational AI Engine

VoxAssist uses **Agora Conversational AI** as the primary voice platform, not just as an RTC layer. The entire STT → LLM → TTS pipeline is orchestrated by Agora's engine.

### Integration Flow

```python
from agora_agent import Agent, Agora, Area, DeepgramSTT, OpenAI, ElevenLabsTTS

# Create Agora client
client = Agora(area=Area.US, app_id="...", app_certificate="...")

# Build agent with vendors
agent = Agent(client=client).with_stt(
    DeepgramSTT(model="nova-3", language="multi")
).with_llm(
    OpenAI(model="gpt-4o-mini", system_messages=[...])
).with_tts(
    ElevenLabsTTS(key="...", voice_id="rachel")
)

# Start session
session = agent.create_session(channel="...", agent_uid="1")
session.start()
```

### Why Agora?

1. **Single SDK** - Handles STT, LLM, TTS orchestration
2. **Low latency** - <800ms end-to-end
3. **Built-in interruption** - No custom code needed
4. **Multilingual** - Native support for Hindi/English
5. **Scalable** - Enterprise-grade infrastructure

---

## Known Limitations

- Free tier has 300-second call limit
- Requires microphone permission in browser
- Voice quality depends on network latency
- Agora free tier has limited concurrent users

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
