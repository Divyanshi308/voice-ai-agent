# Multilingual Voice AI Agent

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white)](https://openai.com/)
[![Deepgram](https://img.shields.io/badge/Deepgram-000000?logo=deepgram&logoColor=white)](https://deepgram.com/)
[![ElevenLabs](https://img.shields.io/badge/ElevenLabs-6C47FF?logo=elevenlabs&logoColor=white)](https://elevenlabs.io/)

Real-time conversational voice agent that handles inbound calls with multilingual ASR, LLM-powered dialogue, guardrails, and automated ticketing.

## Architecture

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                    Voice AI Agent                           │
                        │                                                             │
Caller  ───WebSocket───►│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐  │
  (Telnyx)              │  │   ASR   │───►│   LLM   │───►│ Guard-  │───►│   TTS   │──┼──► Caller
                        │  │Deepgram │    │ OpenAI  │    │ rails   │    │ElevenLbs│  │
                        │  └─────────┘    └────┬────┘    └────┬────┘    └─────────┘  │
                        │                      │              │                       │
                        │                ┌─────▼──────┐  ┌───▼──────┐                │
                        │                │  Dialogue   │  │Analytics │                │
                        │                │  Manager    │  │(PG+Redis)│                │
                        │                └─────┬──────┘  └──────────┘                │
                        │                      │                                      │
                        │                ┌─────▼──────┐    ┌──────────┐               │
                        │                │ Ticketing   │───►│SMS Notify│               │
                        │                │ (Zendesk)   │    │ (Twilio) │               │
                        │                └────────────┘    └──────────┘               │
                        └─────────────────────────────────────────────────────────────┘

Data Flow:
  1. Caller audio stream ──► Deepgram Nova-3 (multi-lang ASR)
  2. Transcript ──► Guardrails (medical/emergency/legal/financial check)
  3. Safe transcript ──► OpenAI GPT-4o (response + sentiment + intent)
  4. Response ──► Guardrails (output safety check)
  5. Safe response ──► ElevenLabs Turbo v2.5 (streaming TTS) ──► Caller
  6. All events ──► Dialogue Manager (state tracking) ──► Analytics (PostgreSQL + Redis)
  7. On escalation: Zendesk ticket created + Twilio SMS sent to caller
```

## Features

1. **Multilingual Support** — Real-time language detection via Deepgram Nova-3 multi-language model; agent responds in the caller's language automatically
2. **Code-Switching** — Tracks language switches mid-conversation and maintains correct response language throughout the call
3. **Noise Resilience** — ASR confidence scoring with automatic recovery prompts when audio quality drops below threshold (<0.5 avg confidence triggers escalation)
4. **Guardrails** — Input/output safety filters blocking medical advice, legal advice, financial advice, and emergency handling with immediate escalation protocols
5. **Human Escalation** — Automatic warm transfer to human agents when sentiment degrades (3x frustrated, 2x angry), max turns reached, or guardrails triggered
6. **Zendesk Ticketing** — Auto-creates tickets with full transcript, collected data, intent classification, urgency level, and AI-generated summary on every call
7. **SMS Notifications** — Sends caller confirmation via Twilio with case number, issue summary, and response timeframe in their detected language
8. **Conversation Memory** — Sliding-window context (last 10 turns), field auto-extraction (phone/email/location), and structured state tracked per call via Redis
9. **Sentiment Tracking** — Per-utterance sentiment analysis (calm/stressed/frustrated/angry) with historical trend for escalation decisions
10. **Real-Time Streaming** — Full-duplex WebSocket audio pipeline with 20ms chunk streaming, barge-in detection, and <300ms first-byte TTS latency

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- API keys for:
  - [Deepgram](https://console.deepgram.com/) — Speech-to-Text
  - [OpenAI](https://platform.openai.com/) — LLM
  - [ElevenLabs](https://elevenlabs.io/) — Text-to-Speech
  - [Zendesk](https://www.zendesk.com/) — Ticketing (optional)
  - [Twilio](https://www.twilio.com/) — SMS notifications (optional)
  - [Telnyx](https://www.telnyx.com/) — Telephony (optional, for production)

## Quick Start

```bash
git clone https://github.com/you/voice-ai-agent
cd voice-ai-agent && cp .env.example .env  # fill in your API keys
docker-compose up
```

Server starts at `http://localhost:8000`. Test endpoint:

```bash
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, I need help with my billing"}'
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DEEPGRAM_API_KEY` | Yes | Deepgram API key for speech-to-text |
| `OPENAI_API_KEY` | Yes | OpenAI API key for GPT-4o LLM |
| `OPENAI_MODEL` | No | Model name (default: `gpt-4o`) |
| `ELEVENLABS_API_KEY` | Yes | ElevenLabs API key for text-to-speech |
| `ELEVENLABS_VOICE_ID` | No | Voice ID (default: `rachel`) |
| `ELEVENLABS_MODEL` | No | TTS model (default: `eleven_turbo_v2_5`) |
| `REDIS_URL` | Yes | Redis connection string (default: `redis://localhost:6379`) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `PORT` | No | Server port (default: `8000`) |
| `HOST` | No | Server host (default: `0.0.0.0`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `ENVIRONMENT` | No | `development`, `staging`, or `production` |
| `TELNYX_API_KEY` | No | Telnyx API key for telephony |
| `TELNYX_PHONE_NUMBER` | No | Telnyx purchased phone number (E.164) |
| `ZENDESK_API_KEY` | No | Zendesk API token |
| `ZENDESK_EMAIL` | No | Zendesk agent email for API auth |
| `ZENDESK_SUBDOMAIN` | No | Zendesk subdomain (`yourcompany.zendesk.com`) |
| `TWILIO_ACCOUNT_SID` | No | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | No | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | No | Twilio purchased phone number (E.164) |
| `HUMAN_TRANSFER_NUMBER` | No | Human agent phone number for warm transfer |
| `HUMAN_AGENT_NAME` | No | Human agent name for handoff message |
| `ESCALATION_CONFIDENCE_THRESHOLD` | No | Escalate below this ASR confidence (default: `0.7`) |
| `MAX_TURNS_BEFORE_ESCALATION` | No | Force escalation after N turns (default: `10`) |
| `EMERGENCY_NUMBERS` | No | Comma-separated emergency numbers (default: `112,911`) |

## Testing

```bash
pytest tests/ -v
```

## Deployment

| Option | Command | Notes |
|---|---|---|
| **Local** | `python main.py` | Requires local Redis + PostgreSQL |
| **Docker** | `docker-compose up` | All services bundled; recommended for dev/staging |
| **AWS** | ECS Fargate or EC2 + RDS + ElastiCache | Use ALB for WebSocket support; enable CloudWatch logging |
| **GCP** | Cloud Run + Cloud SQL + Memorystore | Cloud Run supports WebSockets natively |
| **Azure** | Container Apps + Azure Database for PostgreSQL + Azure Cache | Use Dapr sidecar for service discovery |

## Cost Estimate

| Component | Per 5-min call |
|---|---|
| Deepgram Nova-3 | ~$0.008 |
| OpenAI GPT-4o | ~$0.02-0.04 |
| ElevenLabs Turbo v2.5 | ~$0.01-0.02 |
| Zendesk API | Free tier |
| Twilio SMS | ~$0.008 |
| **Total per call** | **$0.05 - $0.08** |

*Estimates based on ~15 turns, 200-word average response length, gpt-4o pricing.*

## License

MIT
