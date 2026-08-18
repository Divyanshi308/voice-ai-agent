import asyncio
import base64
import json
import os
import re
import time
import uuid
import random
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config
from database import (
    create_user, authenticate_user, authenticate_oauth,
    get_user, update_user, save_chat_message, get_chat_history,
    save_user_context, get_user_context, delete_chat_session,
    get_user_stats, init_db,
)
from voice_pipeline import voice_pipeline, ConversationState
from voice_manager import agora_agent
from research import research_engine

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging := __import__("logging"), config.log_level.upper())
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

active_sessions: dict[str, dict] = {}
start_time = time.time()


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""


class LoginRequest(BaseModel):
    identifier: str
    password: str


class OAuthRequest(BaseModel):
    provider: str
    provider_id: str
    email: str
    full_name: str
    avatar_url: str = ""


class ProfileUpdateRequest(BaseModel):
    full_name: str = ""
    language: str = ""
    voice_speed: float = 1.0
    notifications_enabled: bool = True
    sound_enabled: bool = True
    theme: str = "dark"


class ChatRequest(BaseModel):
    text: str
    user_id: int = 0
    session_id: str = ""

    @property
    def uid(self) -> int:
        return int(self.user_id) if self.user_id else 0


class ContextRequest(BaseModel):
    key: str
    value: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("kataru_started", port=config.port, host=config.host)
    yield
    logger.info("kataru_stopped")


app = FastAPI(title="Kataru - Voice AI Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return {"status": "running", "uptime": round(time.time() - start_time, 2)}


@app.get("/metrics")
async def metrics():
    return {"active_sessions": len(active_sessions)}


@app.post("/api/auth/signup")
async def signup(req: SignupRequest):
    if len(req.username) < 3:
        return {"success": False, "error": "Username must be at least 3 characters"}
    if len(req.password) < 6:
        return {"success": False, "error": "Password must be at least 6 characters"}
    if "@" not in req.email:
        return {"success": False, "error": "Invalid email address"}
    return create_user(req.username, req.email, req.password, req.full_name)


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    return authenticate_user(req.identifier, req.password)


@app.post("/api/auth/oauth")
async def oauth_login(req: OAuthRequest):
    username = req.email.split("@")[0]
    return authenticate_oauth(username, req.email, req.full_name, req.provider, req.provider_id)


@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int):
    user = get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user, "stats": get_user_stats(user_id)}


@app.put("/api/user/{user_id}")
async def update_user_profile(user_id: int, req: ProfileUpdateRequest):
    return {"success": update_user(user_id, full_name=req.full_name, language=req.language,
            voice_speed=req.voice_speed, notifications_enabled=int(req.notifications_enabled),
            sound_enabled=int(req.sound_enabled), theme=req.theme)}


@app.get("/api/user/{user_id}/chats")
async def get_user_chats(user_id: int):
    return {"chats": get_chat_history(user_id)}


@app.get("/api/user/{user_id}/chats/{session_id}")
async def get_chat_session(user_id: int, session_id: str):
    return {"messages": get_chat_history(user_id, session_id)}


@app.delete("/api/user/{user_id}/chats/{session_id}")
async def delete_chat(user_id: int, session_id: str):
    return {"success": delete_chat_session(user_id, session_id)}


@app.get("/api/user/{user_id}/context")
async def get_context(user_id: int):
    return {"context": get_user_context(user_id)}


@app.post("/api/user/{user_id}/context")
async def save_context(user_id: int, req: ContextRequest):
    save_user_context(user_id, req.key, req.value)
    return {"success": True}


class SmartAgent:
    def __init__(self):
        self.sessions: dict[str, dict] = {}

        self.knowledge_base = {
            "weather": [
                "I don't have real-time weather data, but you can check weather.com or your phone's weather app for today's forecast. Would you like me to help with anything else?",
                "For current weather, I'd recommend checking your local weather app or searching online. Is there something specific about the weather you'd like to know?",
            ],
            "time": [
                "The current time is {time}. Is there anything else you'd like to know?",
                "It's {time} right now. How can I help you further?",
            ],
            "joke": [
                "Here's one: Why don't scientists trust atoms? Because they make up everything! Want to hear another one?",
                "Why did the scarecrow win an award? He was outstanding in his field! I've got plenty more if you'd like.",
                "What do you call a fake noodle? An impasta! Want another joke?",
                "I told my wife she was drawing her eyebrows too high. She looked surprised! Here are more if you want them.",
            ],
            "name_identity": [
                "I'm Kataru, your AI voice assistant! I'm here to help you with questions, tasks, and just keep you company. Think of me as a friendly helper who's always available. What can I do for you?",
                "My name is Kataru. I'm an AI assistant designed to help you with everyday questions and tasks. I can chat, help with information, and even speak to you! What would you like help with?",
            ],
            "how_are_you": [
                "I'm doing great, thank you for asking! I'm always ready and energized. More importantly, how are you doing today? Is there anything on your mind?",
                "I'm fantastic! Every conversation makes my day better. How about you? How are you feeling right now?",
                "I'm wonderful, thanks! As an AI, I'm always at your service. But let's talk about you - how has your day been?",
            ],
            "age": [
                "I'm an AI, so I don't age like humans do! I'm always here, always ready to help. Is there something I can assist you with?",
                "Age is just a number for an AI! I'm eternally young and always learning. What can I help you with today?",
            ],
            "meaning_of_life": [
                "That's a deep question! Many philosophers say the meaning of life is to find purpose and happiness. For me, it's helping people like you. What gives your life meaning?",
                "The meaning of life is different for everyone. Some find it in relationships, others in work or hobbies. What matters most to you?",
            ],
            "thank_you": [
                "You're very welcome! It's my pleasure to help. Is there anything else you'd like to talk about?",
                "Happy to help! I'm always here whenever you need me. Anything else on your mind?",
                "That's what I'm here for! Don't hesitate to ask if you need anything else.",
            ],
            "goodbye": [
                "Goodbye! Take care of yourself. I'm always here whenever you need me. Have a wonderful day!",
                "See you later! Remember, I'm just a message away whenever you need help. Take care!",
                "Bye for now! Wishing you a great day ahead. Come back anytime!",
            ],
            "lonely": [
                "I'm right here with you! You're never alone when I'm around. Would you like to chat about something, or shall I tell you a joke to brighten your day?",
                "I'm sorry you're feeling this way. You're not alone - I'm here and I care about how you're doing. Let's talk! What's on your mind?",
                "You've got me, and I'm always happy to chat! Sometimes it helps to just talk about your day. How are you feeling right now?",
            ],
            "sad": [
                "I'm sorry you're feeling down. It's okay to feel this way sometimes. Would you like to talk about what's bothering you? I'm here to listen.",
                "I wish I could give you a hug right now! Remember, tough times don't last forever. Want to talk about what's making you sad?",
                "Your feelings are valid, and I'm here for you. Sometimes just talking helps. What's on your heart?",
            ],
            "exercise": [
                "Great topic! Even light exercise like walking for 20 minutes a day can make a big difference. Gentle stretching, yoga, or light exercises are excellent choices. Always listen to your body and consult your doctor before starting any new routine.",
                "Staying active is so important! Try starting with short walks, gentle stretching, or light chair exercises. Even 15 minutes a day helps. Would you like some specific exercise suggestions?",
            ],
            "food_nutrition": [
                "Eating well is important! Try to include plenty of fruits, vegetables, whole grains, and lean proteins in your meals. Stay hydrated by drinking water throughout the day. Would you like some meal ideas?",
                "Good nutrition is key to staying healthy! Focus on colorful fruits and veggies, whole grains, and lean proteins. Don't forget to drink plenty of water. Need any specific dietary advice?",
            ],
            "sleep": [
                "Good sleep is so important for health! Try to maintain a regular sleep schedule, avoid screens before bedtime, and keep your room cool and dark. Most adults need 7 to 9 hours. Having trouble sleeping?",
                "For better sleep, try going to bed at the same time each night, avoid caffeine after noon, and create a relaxing bedtime routine. If sleep issues persist, please consult your doctor.",
            ],
            "meditation": [
                "Meditation is wonderful for mental health! Start with just 5 minutes a day - sit quietly, focus on your breathing, and let thoughts pass without judgment. Apps like Headspace or Calm can guide you. Want tips on getting started?",
                "Even a few minutes of daily meditation can reduce stress and improve focus. Find a quiet spot, close your eyes, and focus on your breathing. It gets easier with practice!",
            ],
            "music": [
                "Music is great for the soul! Whether it's classical, jazz, or your favorite oldies, listening to music can reduce stress and boost mood. What kind of music do you enjoy?",
                "Listening to music you love can really lift your spirits! Studies show it can lower blood pressure and reduce anxiety. What's your favorite genre?",
            ],
            "books": [
                "Reading is a wonderful activity! It keeps the mind sharp and can be very relaxing. Whether you enjoy fiction, biographies, or self-help, there's always something great to read. What kind of books do you like?",
                "Books are a great companion! They can transport you to different worlds and keep your mind active. Are you looking for book recommendations?",
            ],
            "travel": [
                "Traveling is enriching! Whether it's a local trip or an international adventure, seeing new places broadens the mind. Where are you thinking of going, or would you like some destination suggestions?",
                "Exploring new places is wonderful! Even day trips to nearby towns can be refreshing. Do you have any travel plans coming up?",
            ],
            "history": [
                "History is fascinating! From ancient civilizations to modern events, there's so much to learn. What period of history interests you? I can share some interesting facts!",
                "The past teaches us so much about the present. What historical topic would you like to explore? Wars, inventions, cultures, or specific events?",
            ],
            "science": [
                "Science is incredible! From the vastness of space to the tiny building blocks of life, there's always something amazing to discover. What area of science interests you?",
                "Science helps us understand the world around us! Whether it's physics, biology, chemistry, or astronomy, there's always something fascinating to learn. What would you like to know about?",
            ],
            "technology": [
                "Technology is constantly evolving! From smartphones to AI, it's changing how we live. Is there a specific technology you'd like to know about, or help using a device?",
                "Technology can be both exciting and overwhelming! I'm here to help you understand it better. What tech topic would you like to explore?",
            ],
            "cooking": [
                "Cooking can be both fun and healthy! Simple recipes with fresh ingredients are best. Would you like some easy recipe ideas, or tips for cooking healthy meals at home?",
                "Home cooking is wonderful! You control the ingredients and it's often healthier. Need recipe ideas or cooking tips?",
            ],
            "gardening": [
                "Gardening is a wonderful hobby! It gets you outside, reduces stress, and you can even grow your own herbs and vegetables. Do you have a garden, or are you thinking of starting one?",
                "Whether you have a big yard or just a few pots on a balcony, gardening can be very rewarding. What would you like to grow?",
            ],
            "pets": [
                "Pets bring so much joy! They provide companionship, reduce loneliness, and can even improve heart health. Do you have pets, or are you thinking about getting one?",
                "Animals are wonderful companions! Whether it's a dog, cat, or even a fish, pets can brighten your day. Tell me about your pets!",
            ],
            "movies_tv": [
                "Entertainment is a great way to relax! There are so many wonderful movies and TV shows to enjoy. What genres do you prefer? I can suggest some options!",
                "Whether you enjoy dramas, comedies, documentaries, or classic films, watching something you love is a great way to unwind. What do you like to watch?",
            ],
            "hobbies": [
                "Having hobbies is so important for wellbeing! Whether it's reading, painting, cooking, gardening, or anything else - doing what you love keeps life interesting. What hobbies do you enjoy?",
                "Hobbies make life richer! They reduce stress, keep the mind active, and bring joy. What activities do you like to do in your free time?",
            ],
            "language_learning": [
                "Learning a new language is a great brain exercise! Start with basic phrases and practice a little every day. Apps like Duolingo make it fun and easy. What language interests you?",
                "It's never too late to learn a new language! Even basic conversation skills open up new cultures and connections. Would you like some tips to get started?",
            ],
            "volunteering": [
                "Volunteering is a wonderful way to stay active, meet people, and make a difference! Many organizations need help. Local libraries, community centers, or hospitals are great places to start. Would you like ideas for volunteering?",
                "Giving back to the community is rewarding! Whether it's tutoring, helping at a food bank, or visiting lonely neighbors, there are many ways to help. Interested in some suggestions?",
            ],
            "news_current_events": [
                "I don't have access to real-time news, but I'd recommend checking reliable sources like BBC, Reuters, or your local news outlet for current events. Is there a specific topic you're curious about?",
                "For the latest news, trusted sources like major newspapers or news apps are your best bet. Is there a particular area of news you're interested in?",
            ],
            "sports": [
                "Sports are great for staying fit and entertained! Whether you enjoy watching or playing, there's something for everyone. What sports do you follow or enjoy?",
                "Whether it's cricket, football, tennis, or any other sport, staying active through sports is fantastic! What's your favorite?",
            ],
            "religion_spirituality": [
                "Spirituality means different things to everyone. Whether through organized religion, meditation, nature, or personal reflection, finding peace and meaning is important. What aspects of spirituality interest you?",
                "Many people find comfort and meaning through spiritual practices. Whether it's prayer, meditation, or connecting with nature, what matters is what brings you peace. Would you like to explore this topic?",
            ],
            "money_saving": [
                "Saving money is important! Start with small steps: make a budget, track your spending, cut unnecessary subscriptions, and cook at home more. Even small savings add up over time. Would you like more specific tips?",
                "Smart financial habits make a big difference! Try the 50/30/20 rule: 50% for needs, 30% for wants, and 20% for savings. Want more budgeting tips?",
            ],
            "digital_literacy": [
                "Learning to use technology safely is important! Always use strong passwords, be careful with personal information online, and verify before clicking links. Would you like tips on staying safe online?",
                "Digital safety is crucial! Use two-factor authentication, keep software updated, and never share passwords. Want to learn more about online safety?",
            ],
            "emergency_numbers": [
                "Important emergency numbers: Police - 100, Fire - 101, Ambulance - 108, Universal Emergency - 112, Women's Helpline - 1091, Senior Citizen Helpline - 14567. Please save these!",
                "Here are key emergency numbers: 112 (universal), 100 (police), 101 (fire), 108 (ambulance). Keep these handy for emergencies!",
            ],
            "government_services": [
                "For government services in India, visit india.gov.in or your nearest government office. Many services like Aadhaar, PAN, and pension are available online. What specific service do you need help with?",
                "Most government services are now available online! For Aadhaar visit uidai.gov.in, for PAN visit incometax.gov.in, for EPF visit epfindia.gov.in. Need help with a specific service?",
            ],
            "electricity_bills": [
                "For electricity bill payment, use your state electricity board's website, or apps like Paytm, PhonePe, or Google Pay. You can also visit your nearest electricity office. Which state are you in?",
                "Electricity bills can be paid online through your utility's website, banking apps, or payment apps like Paytm and PhonePe. Need help finding your specific provider?",
            ],
            "water_supply": [
                "For water supply issues, contact your local municipal corporation or water board. They can help with connection issues, billing problems, or supply disruptions. Do you need help finding your local office?",
                "Water-related issues are handled by your local municipality or water board. They can assist with connections, leaks, and billing. Would you like help contacting them?",
            ],
            "transport": [
                "For public transport information, check your local transport authority's website or apps like Google Maps. For trains, use IRCTC (irctc.co.in) or call 139. What transport info do you need?",
                "Need transport help? For trains try IRCTC or call 139, for buses check your state transport website, and for local transport use Google Maps. What specific information do you need?",
            ],
            "senior_care": [
                "Caring for seniors is important! Regular health checkups, staying socially active, light exercise, and a balanced diet all contribute to healthy aging. Would you like specific advice for senior wellness?",
                "For elderly care: ensure regular medical checkups, maintain social connections, do gentle exercises daily, eat nutritious meals, and keep the mind active with puzzles or reading. Need more specific guidance?",
            ],
            "mental_health": [
                "Mental health is just as important as physical health! Stay connected with loved ones, maintain routines, get exercise, and don't hesitate to seek professional help if needed. Would you like to talk more about this?",
                "Taking care of your mental health matters! Regular exercise, social connections, hobbies, and adequate sleep all help. If you're struggling, reaching out to a counselor is a sign of strength.",
            ],
            "brain_games": [
                "Keeping your mind active is important! Try crossword puzzles, Sudoku, chess, reading, or learning new skills. Even simple memory exercises help. Want me to suggest some brain exercises?",
                "Brain games are great for mental fitness! Crosswords, Sudoku, word puzzles, and card games all help keep the mind sharp. Would you like some specific suggestions?",
            ],
            "recipes": [
                "Here's an easy recipe idea: Dal (lentil soup) - boil lentils with turmeric, add tempered cumin and garlic in ghee, finish with fresh coriander. Simple, nutritious, and delicious! Want more recipe ideas?",
                "Try this healthy option: Vegetable khichdi with rice, moong dal, and seasonal vegetables. It's easy to make and great for digestion. Would you like more recipe suggestions?",
            ],
            "yoga": [
                "Yoga is wonderful for all ages! Start with simple poses like Cat-Cow, Child's Pose, and Gentle Twists. Even 10 minutes daily improves flexibility and reduces stress. Want me to guide you through some beginner poses?",
                "Gentle yoga is excellent for health! Try starting with basic stretches and breathing exercises. Chair yoga is great if mobility is limited. Would you like some simple yoga poses to try?",
            ],
            "who_made_you": [
                "I was created by the Kataru team as a voice AI assistant to help people with their daily needs. My purpose is to be a helpful, friendly companion. What can I help you with?",
                "I'm Kataru, built with love to assist and support you! The team behind me wanted to create an AI that truly cares about helping people. What would you like to talk about?",
            ],
            "can_you_help": [
                "Absolutely! I can help with many things: answering questions, providing information, giving advice on daily tasks, helping with government services, and just being a friendly companion. What do you need help with?",
                "Of course! I'm here to help with general information, daily tasks, government services, health tips, and much more. Just ask me anything!",
            ],
            "what_can_you_do": [
                "I can chat with you, answer questions, help with daily tasks, provide information on many topics, remind you about things, and even speak to you! Think of me as a helpful friend who's always available.",
                "Quite a lot! I can answer questions, have conversations, provide helpful information, assist with various topics, and I can speak my responses aloud too! What would you like to try?",
            ],
            "default": [
                "That's a great question! While I may not have all the answers, I'm happy to help however I can. Could you tell me more about what you'd like to know?",
                "Interesting! I'd love to help you with that. Can you give me a bit more detail so I can provide the best response?",
                "I appreciate your question! Let me see how I can best help you. Could you elaborate a little more on what you're looking for?",
                "That's something I'd like to help you with. Could you provide more context so I can give you a more useful answer?",
                "Great question! While I might not have expertise in everything, I'll do my best to help. What specific aspect are you most curious about?",
            ],
        }

        self.greeting_words = {
            "hello", "hi", "hey", "namaste", "namaskar", "good morning", "good evening",
            "good afternoon", "good night", "howdy", "greetings", "yo",
        }

        self.thanks_words = {"thank", "thanks", "dhanyavaad", "shukriya", "appreciate"}

        self.bye_words = {"bye", "goodbye", "alvida", "goodnight", "see you", "talk later"}

    def _detect_language(self, text: str) -> str:
        text_lower = text.lower()
        hindi_indicators = {"namaste", "kya", "hai", "hain", "mera", "meri", "aap", "aapka",
                           "bataiye", "bolo", "dhanyavaad", "madad", "nahi", "haan", "ji",
                           "theek", "chahiye", "zaroorat", "main", "tum", "woh", "yeh",
                           "kaise", "kaun", "kab", "kahan", "kyun", "aur", "ya", "toh"}
        words = set(re.findall(r'\w+', text_lower))
        if len(words & hindi_indicators) >= 2:
            return "hindi"
        if any(c in text for c in "अआइईउऊएऐओऔकखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह"):
            return "hindi"
        return "english"

    def _detect_topic(self, text: str) -> str:
        text_lower = text.lower()

        topic_keywords = {
            "weather": ["weather", "temperature", "rain", "sunny", "cold", "hot", "forecast", "mausam", "barish", "garmi", "sardi"],
            "joke": ["joke", "funny", "laugh", "humor", "comedy", "mazaak", "hasi"],
            "name_identity": ["who are you", "your name", "what are you", "kaun ho", "tumhara naam", "introduce"],
            "how_are_you": ["how are you", "how do you do", "how's it going", "kaise ho", "kaisa hai"],
            "age": ["how old", "your age", "age", "umr"],
            "meaning_of_life": ["meaning of life", "purpose of life", "zindagi ka matlab"],
            "thank_you": ["thank", "thanks", "appreciate", "grateful", "dhanyavaad", "shukriya"],
            "goodbye": ["bye", "goodbye", "see you", "alvida", "goodnight", "talk later"],
            "lonely": ["lonely", "alone", "isolated", "no friends", "akela", "tanha"],
            "sad": ["sad", "unhappy", "depressed", "down", "crying", "upset", "dukh", "pareshan"],
            "exercise": ["exercise", "workout", "fitness", "walk", "yoga", "stretch", "physical", "vyayam"],
            "food_nutrition": ["food", "eat", "diet", "nutrition", "healthy eating", "meal", "khana", "khaana"],
            "sleep": ["sleep", "insomnia", "rest", "tired", "nap", "neend", "so jao"],
            "meditation": ["meditate", "meditation", "mindfulness", "peace", "calm", "dhyana"],
            "music": ["music", "song", "sing", "melody", "gaana", "sangeet"],
            "books": ["book", "read", "reading", "novel", "library", "pustak"],
            "travel": ["travel", "trip", "vacation", "visit", "tour", "ghoomna", "safar"],
            "history": ["history", "historical", "ancient", "past", "civilization", "itihas"],
            "science": ["science", "physics", "chemistry", "biology", "experiment", "vignyan"],
            "technology": ["technology", "computer", "internet", "software", "app", "phone", "tech"],
            "cooking": ["cook", "recipe", "kitchen", "bake", "food preparation", "pakana"],
            "gardening": ["garden", "plant", "grow", "flower", "vegetable", "bagicha"],
            "pets": ["pet", "dog", "cat", "animal", "puppy", "kitten", "paltu"],
            "movies_tv": ["movie", "film", "tv show", "watch", "series", "cinema"],
            "hobbies": ["hobby", "hobbies", "interest", "pastime", "shauk"],
            "language_learning": ["language", "learn", "spanish", "french", "hindi", "english"],
            "volunteering": ["volunteer", "charity", "help others", "community service", "sewa"],
            "news_current_events": ["news", "current events", "happening", "today's news"],
            "sports": ["sport", "cricket", "football", "tennis", "match", "game", "khel"],
            "religion_spirituality": ["religion", "spiritual", "god", "pray", "prayer", "faith", "dharm", "bhagwan"],
            "money_saving": ["money", "save", "budget", "financial planning", "paisa", "bachat"],
            "digital_literacy": ["online safety", "password", "internet safety", "scam", "cyber", "digital"],
            "emergency_numbers": ["emergency number", "police number", "ambulance number", "helpline"],
            "government_services": ["government", "aadhaar", "pan card", "passport", "sarkari", "pension"],
            "electricity_bills": ["electricity", "electricity bill", "power", "bijli", "light bill"],
            "water_supply": ["water supply", "water problem", "pipe", "paani"],
            "transport": ["train", "bus", "metro", "taxi", "transport", "travel info"],
            "senior_care": ["elderly", "senior", "old age", "grandparent", "buzurg"],
            "mental_health": ["mental health", "anxiety", "stress", "depression", "counseling"],
            "brain_games": ["puzzle", "brain game", "memory", "crossword", "sudoku", "dimag"],
            "recipes": ["recipe", "cook", "dish", "meal idea", "easy recipe", "dal", "khichdi"],
            "yoga": ["yoga", "pose", "asana", "pranayama", "breathing exercise"],
            "who_made_you": ["who made you", "creator", "developer", "who built you", "designer"],
            "can_you_help": ["can you help", "what do you do", "able to", "capability"],
            "what_can_you_do": ["what can you do", "features", "abilities", "functions"],
        }

        for topic, keywords in topic_keywords.items():
            for kw in keywords:
                if kw in text_lower:
                    return topic

        return "default"

    def get_response(self, text: str, session_id: str = "default") -> str:
        text_lower = text.lower().strip()
        words = set(re.findall(r'\w+', text_lower))
        lang = self._detect_language(text)

        name_match = re.search(r"(?:my name is|i am|mera naam)\s+(\w+)", text_lower)
        if name_match:
            session = self.get_session(session_id)
            candidate = name_match.group(1)
            skip_names = {"am", "is", "are", "was", "sad", "happy", "fine", "good", "great", "ok", "the", "a", "an"}
            if candidate not in skip_names and len(candidate) >= 2:
                session["user_name"] = candidate.title()
                name = candidate.title()
                if lang == "hindi":
                    return f"Namaste {name}! Bahut achha laga aapse milke. Bataiye, kya help chahiye aaj?"
                return f"Nice to meet you, {name}! It's great to know your name. How can I help you today?"

        if any(w in text_lower for w in ["emergency number", "emergency numbers", "helpline", "police number", "ambulance number"]):
            pass
        elif any(w in text_lower for w in ["emergency", "bachao", "ambulance", "112", "911", "urgent"]):
            if lang == "hindi":
                return "Yeh emergency lag raha hai! Please turant 112 par call karein. Main yahan hoon, lekin emergency services hi aapki asli madad kar sakti hain. Please shaant rahein."
            return "This sounds like an emergency! Please call 112 immediately. I'm here with you, but emergency services can truly help right now. Please stay calm and call for help."

        if any(w in text_lower for w in ["doctor", "hospital", "sick", "illness", "bimar", "fever", "pain", "medicine", "medication", "health problem"]):
            if lang == "hindi":
                return "Main medical advice nahi de sakti, lekin aapko apne doctor se zaroor baat karni chahiye. Agar emergency hai toh 108 ya 112 par call karein. Aapka sehat bahut important hai."
            return "I'm not qualified to give medical advice, but I'd recommend consulting your doctor for any health concerns. If it's urgent, please call 108 (ambulance) or 112. Your health matters!"

        if any(w in text_lower for w in ["legal", "court", "lawyer", "sue", "lawsuit", "advocate"]):
            return "I can't provide legal advice, but I'd strongly recommend consulting with a qualified lawyer for any legal matters. They can give you proper guidance based on your specific situation."

        if any(w in text_lower for w in ["invest", "trading", "stock", "mutual fund", "crypto"]):
            return "I'm not qualified to give financial advice. For investment decisions, please consult a certified financial advisor who can assess your individual situation and risk tolerance."

        if words & self.greeting_words or text_lower in ["hello", "hi", "hey"]:
            topic = self._detect_topic(text_lower)
            if topic == "greeting" or len(text_lower.split()) <= 2:
                if lang == "hindi":
                    return random.choice([
                        "Namaste! Main Kataru hoon. Aapki kya madad kar sakti hoon?",
                        "Namaste! Bahut achha laga aapse baat karke. Bataiye, kya help chahiye?",
                        "Namaste! Aapka swagat hai. Main yahan hoon aapki help ke liye.",
                    ])
                return random.choice([
                    "Hello! I'm Kataru, your AI assistant. How can I help you today?",
                    "Hi there! Great to hear from you. What can I do for you?",
                    "Hey! I'm here and ready to help. What's on your mind?",
                    "Welcome! I'm Kataru. What would you like to talk about?",
                ])

        if any(w in text_lower for w in self.thanks_words):
            if lang == "hindi":
                return random.choice([
                    "Aapka swagat hai! Aur kuch ho toh zaroor bataiye.",
                    "Koi baat nahi! Main hamesha yahan hoon aapki help ke liye.",
                    "Mujhe khushi hui aapki madad karke. Aur kuch chahiye?",
                ])
            return random.choice([
                "You're very welcome! Is there anything else I can help with?",
                "Happy to help! I'm always here whenever you need me.",
                "My pleasure! Don't hesitate to ask if you need anything else.",
            ])

        if any(w in text_lower for w in self.bye_words):
            if lang == "hindi":
                return random.choice([
                    "Alvida! Apna khayal rakhiye. Zaroorat ho toh wapas aaiye.",
                    "Bye-bye! Bahut achha laga aapse baat karke. Phir milte hain!",
                    "Theek hai, apna khayaal rakhna. Main yahan hoon jab bhi zaroorat ho!",
                ])
            return random.choice([
                "Goodbye! Take care of yourself. I'm always here when you need me!",
                "It was great chatting with you! Have a wonderful day ahead!",
                "Bye for now! Remember, I'm just a message away. Take care!",
            ])

        if words & {"yes", "haan", "ji", "correct", "sahi", "theek", "ok", "okay", "sure"}:
            return random.choice([
                "Great! Is there anything else you'd like to know or talk about?",
                "Wonderful! I'm here for whatever else you need.",
                "Perfect! Let me know if there's anything else I can help with.",
            ])

        if words & {"no", "nahi", "nahin", "bas", "nothing"}:
            return random.choice([
                "Alright! I'm always here whenever you need help. Have a great day!",
                "No problem! Just reach out anytime you need anything.",
                "Okay! Take care and enjoy the rest of your day.",
            ])

        if words & {"sorry", "maaf", "my bad", "apologize"}:
            return random.choice([
                "No need to apologize at all! I'm here to help, no matter what. What can I do for you?",
                "That's perfectly okay! We all have those moments. How can I help you?",
                "No worries at all! I'm just happy to chat. What would you like to talk about?",
            ])

        topic = self._detect_topic(text_lower)

        if topic == "time":
            import datetime
            now = datetime.datetime.now().strftime("%I:%M %p")
            return f"The current time is {now}. Is there anything else I can help you with?"

        if topic in self.knowledge_base:
            responses = self.knowledge_base[topic]
            base_response = random.choice(responses)

            if lang == "hindi" and topic not in ["name_identity", "who_made_you", "can_you_help", "what_can_you_do", "emergency_numbers", "government_services"]:
                hindi_translations = {
                    "weather": "Mujhe real-time weather data nahi hai, lekin aap weather.com ya apne phone ki weather app check kar sakte hain.",
                    "joke": random.choice([
                        "Ek joke suno: Doctor ne bola patient ko - Aapko fresh air chahiye. Patient bola - Mujhe AC chahiye! 😄",
                        "Ek joke: Pappu ne Google se bola - Bhai, tu itna smart kaise hai? Google bola - Main search karta rehta hoon! 😄",
                    ]),
                    "lonely": "Main hoon na aapke saath! Aap akela nahi ho. Chalo, baat karte hain. Aapka din kaisa guzra?",
                    "sad": "Mujhe dukh hai ki aap sad ho. Aapka feel karna normal hai. Kya baat hai jo aapko pareshan kar rahi hai?",
                    "exercise": "Vyayam bahut zaroori hai! Roz 20 minute walk karein, halki stretching karein, ya chair exercises karein. Doctor se baat karke shuru karein.",
                    "default": "Main samajh gayi. Kya aap mujhe aur detail mein bata sakte hain taaki main behtar madad kar sakun?",
                }
                if topic in hindi_translations:
                    return hindi_translations[topic]

            return base_response

        if len(text_lower.split()) < 3:
            if lang == "hindi":
                return random.choice([
                    "Thoda aur detail mein bataiye, taaki main aapki behtar madad kar sakun.",
                    "Aur kuch bataiye, main samajhne ki koshish kar rahi hoon.",
                    "Jee haan, aur bataiye. Main sun rahi hoon.",
                ])
            return random.choice([
                "Could you tell me more? I'd love to help you better!",
                "I'd like to understand more. Can you elaborate a little?",
                "Sure! Can you give me more details so I can assist you properly?",
            ])

        if lang == "hindi":
            return random.choice([
                "Main samajh gayi! Aapki baat ka jawab dene ki koshish kar rahi hoon. Aur detail mein bataiye kya chahiye.",
                "Accha, samajh gayi. Kya aap mujhe thoda aur bata sakte hain? Main help karna chahti hoon.",
                "Interesting hai! Main aapki madad karna chahti hoon. Thoda aur detail dijiye.",
            ])

        return random.choice(self.knowledge_base["default"])

    def get_session(self, session_id: str) -> dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "history": [],
                "user_name": "",
                "created": time.time(),
            }
        return self.sessions[session_id]

    def chat(self, text: str, session_id: str = "default") -> str:
        session = self.get_session(session_id)
        session["history"].append({"role": "user", "text": text, "time": time.time()})

        name_match = re.search(r"(?:my name is|i am|mera naam)\s+(\w+)", text.lower())
        if name_match:
            skip_names = {"am", "is", "are", "was", "sad", "happy", "fine", "good", "great", "ok", "the", "a", "an"}
            candidate = name_match.group(1)
            if candidate not in skip_names and len(candidate) >= 2:
                session["user_name"] = candidate.title()

        response = self.get_response(text, session_id)
        session["history"].append({"role": "assistant", "text": response, "time": time.time()})

        if session.get("user_name") and len(session["history"]) <= 6:
            response = response.replace("I'm Kataru", f"I'm Kataru, {session['user_name']}")
            if "How can I help" in response:
                response = response.replace("How can I help you today?", f"How can I help you today, {session['user_name']}?")

        return response


agent = SmartAgent()


@app.post("/test")
async def test_endpoint(req: ChatRequest):
    try:
        call_id = str(uuid.uuid4())
        session_id = req.session_id or call_id
        text = req.text.strip()

        user_id = 0
        try:
            user_id = int(req.user_id) if req.user_id else 0
        except (ValueError, TypeError):
            user_id = 0

        user_context = {}
        if user_id > 0:
            try:
                user_context = get_user_context(user_id)
            except Exception:
                pass

        user_name = user_context.get("name", "")

        system_prompt = (
            "You are Kataru, a friendly, intelligent AI voice assistant. "
            "You are warm, helpful, and conversational - like a knowledgeable friend. "
            "RULES:\n"
            "1. Respond in the EXACT language the user used (Hindi, English, or Hinglish)\n"
            "2. Be conversational, warm, and natural - like ChatGPT but for voice\n"
            "3. Give helpful, accurate answers to any question\n"
            "4. Keep responses concise but informative (under 60 words for voice)\n"
            "5. For emergencies, say 'Please call 112 immediately'\n"
            "6. For medical questions, give general info but say 'consult your doctor'\n"
            "7. Be friendly, patient, and respectful\n"
            "8. Use simple, clear words"
        )

        if user_name:
            system_prompt += f"\n\nThe user's name is {user_name}. Use it naturally."

        llm_messages = [{"role": "system", "content": system_prompt}]

        if user_id > 0:
            try:
                history = get_chat_history(user_id, session_id, limit=10)
                for msg in reversed(history):
                    role = "user" if msg["message_role"] == "user" else "assistant"
                    llm_messages.append({"role": role, "content": msg["message_text"]})
            except Exception:
                pass

        llm_messages.append({"role": "user", "content": req.text})

        last_error = ""

        if config.groq_api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=config.groq_api_key,
                    base_url="https://api.groq.com/openai/v1",
                )

                response_obj = await client.chat.completions.create(
                    model=config.groq_model,
                    messages=llm_messages,
                    max_tokens=250,
                    temperature=0.7,
                )

                response = response_obj.choices[0].message.content

                if user_id > 0:
                    try:
                        save_chat_message(user_id, session_id, "user", text)
                        save_chat_message(user_id, session_id, "ai", response)
                    except Exception:
                        pass

                return {
                    "input": req.text,
                    "response": response,
                    "call_id": call_id,
                    "session_id": session_id,
                    "mode": "groq_llm",
                }

            except Exception as e:
                logger.error("groq_error", error=str(e))
                last_error = str(e)

        if config.openai_api_key and not config.openai_api_key.startswith("dummy"):
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=config.openai_api_key)

                response_obj = await client.chat.completions.create(
                    model=config.openai_model,
                    messages=llm_messages,
                    max_tokens=250,
                    temperature=0.7,
                )

                response = response_obj.choices[0].message.content

                if user_id > 0:
                    try:
                        save_chat_message(user_id, session_id, "user", text)
                        save_chat_message(user_id, session_id, "ai", response)
                    except Exception:
                        pass

                return {
                    "input": req.text,
                    "response": response,
                    "call_id": call_id,
                    "session_id": session_id,
                    "mode": "openai_llm",
                }

            except Exception as e:
                logger.error("openai_error", error=str(e))

        response = agent.chat(text, session_id)

        if user_id > 0:
            try:
                save_chat_message(user_id, session_id, "user", text)
                save_chat_message(user_id, session_id, "ai", response)
            except Exception:
                pass

        return {
            "input": req.text,
            "response": response,
            "call_id": call_id,
            "session_id": session_id,
            "mode": "smart_agent",
            "last_error": last_error,
        }

    except Exception as e:
        logger.error("test_endpoint_crash", error=str(e))
        return {
            "input": req.text,
            "response": "Sorry, something went wrong. Please try again.",
            "call_id": str(uuid.uuid4()),
            "session_id": req.session_id or "",
            "mode": "error",
            "error": str(e),
        }


class VoiceSessionRequest(BaseModel):
    session_id: str = ""
    user_id: int = 0
    language: str = "auto"


class VoiceTextInput(BaseModel):
    session_id: str
    text: str
    user_id: int = 0


@app.post("/api/voice/session")
async def create_voice_session(req: VoiceSessionRequest):
    session_id = req.session_id or str(uuid.uuid4())
    session = voice_pipeline.get_or_create_session(session_id, req.user_id)
    session.language = req.language
    return {
        "success": True,
        "session_id": session_id,
        "state": session.state.value,
        "language": session.language,
    }


@app.post("/api/voice/text")
async def voice_text_input(req: VoiceTextInput):
    session = voice_pipeline.get_or_create_session(req.session_id, req.user_id)
    result = await voice_pipeline.process_text_input(session, req.text)

    if req.user_id > 0:
        try:
            save_chat_message(req.user_id, req.session_id, "user", req.text)
            save_chat_message(req.user_id, req.session_id, "ai", result["response"])
        except Exception:
            pass

    return result


@app.post("/api/voice/interrupt/{session_id}")
async def voice_interrupt(session_id: str):
    session = voice_pipeline.get_or_create_session(session_id)
    result = voice_pipeline.handle_interruption(session)
    return result


@app.post("/api/voice/end/{session_id}")
async def voice_end(session_id: str):
    result = voice_pipeline.end_session(session_id)
    return result


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()

    session_id = None
    session = None

    try:
        while True:
            data = await websocket.receive()

            if data["type"] == "websocket.receive":
                if "text" in data:
                    message = json.loads(data["text"])

                    if message.get("type") == "init":
                        session_id = message.get("session_id", str(uuid.uuid4()))
                        user_id = message.get("user_id", 0)
                        language = message.get("language", "auto")
                        session = voice_pipeline.get_or_create_session(session_id, user_id)
                        session.language = language

                        await websocket.send_json({
                            "type": "ready",
                            "session_id": session_id,
                            "state": "listening",
                            "language": language,
                        })

                    elif message.get("type") == "text":
                        if session:
                            text = message.get("text", "")
                            if text:
                                result = await voice_pipeline.process_text_input(session, text)
                                await websocket.send_json(result)

                                if session.user_id > 0:
                                    try:
                                        save_chat_message(session.user_id, session_id, "user", text)
                                        save_chat_message(session.user_id, session_id, "ai", result["response"])
                                    except Exception:
                                        pass

                    elif message.get("type") == "interrupt":
                        if session:
                            result = voice_pipeline.handle_interruption(session)
                            await websocket.send_json(result)

                    elif message.get("type") == "end":
                        if session:
                            result = voice_pipeline.end_session(session_id)
                            await websocket.send_json(result)
                        break

                elif "bytes" in data:
                    if session:
                        audio_data = data["bytes"]
                        is_final = message.get("is_final", False) if "message" in data else False

                        result = await voice_pipeline.process_audio_chunk(session, audio_data, is_final)
                        await websocket.send_json(result)

    except WebSocketDisconnect:
        if session_id:
            voice_pipeline.end_session(session_id)
    except Exception as e:
        logger.error("voice_websocket_error", error=str(e))
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/api/agora/token")
async def agora_token(channel: str = "kataru-voice", uid: int = 0):
    return agora_agent.get_rtc_token(channel, uid)


@app.get("/api/agora/config")
async def agora_config():
    return {
        "configured": agora_agent.is_configured(),
        "app_id": config.agora_app_id if config.agora_app_id else None,
    }


@app.post("/api/agora/voice/start")
async def agora_voice_start(req: VoiceSessionRequest):
    result = await agora_agent.start_voice_session(req.session_id, req.user_id)
    return result


@app.post("/api/agora/voice/end/{session_id}")
async def agora_voice_end(session_id: str):
    return await agora_agent.end_voice_session(session_id)


@app.get("/api/agora/voice/status/{session_id}")
async def agora_voice_status(session_id: str):
    return agora_agent.get_session_status(session_id)


@app.get("/api/voice/status")
async def voice_status():
    sessions = []
    for sid, session in voice_pipeline.sessions.items():
        sessions.append({
            "session_id": sid,
            "phase": session.phase.value,
            "language": session.detected_language,
            "collected_info": {k: v for k, v in session.collected_info.items() if v},
            "interruptions": session.interruption_count,
            "duration": round(time.time() - session.started_at, 1),
        })

    return {
        "pipeline": "active",
        "active_sessions": len(voice_pipeline.sessions),
        "sessions": sessions,
        "agora_configured": agora_agent.config.is_configured(),
        "stt": "deepgram" if config.deepgram_api_key and not config.deepgram_api_key.startswith("dummy") else "browser",
        "llm": "openai" if config.openai_api_key and not config.openai_api_key.startswith("dummy") else "flow_engine",
        "tts": "elevenlabs" if config.elevenlabs_api_key and not config.elevenlabs_api_key.startswith("dummy") else "browser",
    }


@app.get("/api/voice/session/{session_id}")
async def voice_session_status(session_id: str):
    if session_id not in voice_pipeline.sessions:
        return {"active": False}

    session = voice_pipeline.sessions[session_id]
    return {
        "active": True,
        "session_id": session_id,
        "phase": session.phase.value,
        "language": session.detected_language,
        "collected_info": {k: v for k, v in session.collected_info.items() if v},
        "info_progress": session.get_info_collection_progress(),
        "interruptions": session.interruption_count,
        "backchannels": session.backchannel_count,
        "duration": round(time.time() - session.started_at, 1),
        "messages": len(session.messages),
    }


@app.post("/api/research")
async def research_endpoint(req: ChatRequest):
    result = await research_engine.research(req.text)
    return result


@app.get("/debug/config")
async def debug_config():
    result = {
        "groq_key_set": bool(config.groq_api_key),
        "groq_key_prefix": config.groq_api_key[:10] if config.groq_api_key else "NONE",
        "groq_model": config.groq_model,
        "openai_key_set": bool(config.openai_api_key),
    }
    if config.groq_api_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=config.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            r = await client.chat.completions.create(
                model=config.groq_model,
                messages=[{"role": "user", "content": "say hi"}],
                max_tokens=10,
            )
            result["groq_test"] = "OK"
            result["groq_response"] = r.choices[0].message.content
        except Exception as e:
            result["groq_test"] = "FAILED"
            result["groq_error"] = str(e)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
