import asyncio
import re
import json
from typing import Optional
from urllib.parse import quote_plus

import structlog
from config import config

logger = structlog.get_logger()


class WebSearchEngine:
    def __init__(self):
        self.search_providers = {
            "duckduckgo": self._search_duckduckgo,
            "brave": self._search_brave,
        }

    async def search(self, query: str, num_results: int = 5) -> list[dict]:
        results = []

        try:
            results = await self._search_duckduckgo(query, num_results)
        except Exception as e:
            logger.error("duckduckgo_search_error", error=str(e))

        if not results:
            try:
                results = await self._search_brave(query, num_results)
            except Exception as e:
                logger.error("brave_search_error", error=str(e))

        if not results:
            results = self._get_fallback_results(query)

        return results[:num_results]

    async def _search_duckduckgo(self, query: str, num_results: int = 5) -> list[dict]:
        import httpx

        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                results = []

                if data.get("AbstractText"):
                    results.append({
                        "title": data.get("Heading", "Result"),
                        "snippet": data.get("AbstractText", ""),
                        "url": data.get("AbstractURL", ""),
                        "source": data.get("AbstractSource", "DuckDuckGo"),
                    })

                for topic in data.get("RelatedTopics", [])[:num_results]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({
                            "title": topic.get("Text", "")[:80],
                            "snippet": topic.get("Text", ""),
                            "url": topic.get("FirstURL", ""),
                            "source": "DuckDuckGo",
                        })

                if not results and data.get("Answer"):
                    results.append({
                        "title": "Direct Answer",
                        "snippet": data["Answer"],
                        "url": "",
                        "source": "DuckDuckGo",
                    })

                return results[:num_results]

            return []

    async def _search_brave(self, query: str, num_results: int = 5) -> list[dict]:
        import httpx

        brave_key = getattr(config, "brave_api_key", "")
        if not brave_key:
            return []

        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": brave_key,
        }
        params = {
            "q": query,
            "count": num_results,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                results = []
                for item in data.get("web", {}).get("results", [])[:num_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("description", ""),
                        "url": item.get("url", ""),
                        "source": "Brave Search",
                    })
                return results

            return []

    def _get_fallback_results(self, query: str) -> list[dict]:
        query_lower = query.lower()

        knowledge_base = {
            "weather": {
                "title": "Weather Information",
                "snippet": "For current weather, check your local weather app or website like weather.com. I cannot provide real-time weather data.",
                "url": "https://weather.com",
            },
            "news": {
                "title": "Latest News",
                "snippet": "For the latest news, check reliable news sources like BBC, Reuters, or your local news channel.",
                "url": "",
            },
            "doctor": {
                "title": "Medical Consultation",
                "snippet": "For medical advice, please consult a qualified doctor. I cannot provide medical diagnosis or treatment recommendations.",
                "url": "",
            },
            "hospital": {
                "title": "Hospital Information",
                "snippet": "To find nearby hospitals, search on Google Maps or call 108 for emergency medical services in India.",
                "url": "",
            },
            "emergency": {
                "title": "Emergency Services",
                "snippet": "For emergencies in India, call 112 (Police/Fire/Ambulance). For medical emergencies, call 108.",
                "url": "",
            },
            "bill": {
                "title": "Bill Payment",
                "snippet": "For bill payments, you can use online banking, mobile apps, or visit your nearest service center.",
                "url": "",
            },
            "account": {
                "title": "Account Management",
                "snippet": "For account-related queries, please have your account number ready. I can help you with basic account information.",
                "url": "",
            },
            "pension": {
                "title": "Pension Information",
                "snippet": "For pension-related queries, contact your pension provider or visit the EPFO website (epfindia.gov.in) for EPF-related matters.",
                "url": "https://epfindia.gov.in",
            },
            "government": {
                "title": "Government Services",
                "snippet": "For government services, visit your nearest government office or check the official government portal (india.gov.in).",
                "url": "https://india.gov.in",
            },
            "aadhar": {
                "title": "Aadhaar Services",
                "snippet": "For Aadhaar-related services, visit uidai.gov.in or call 1947. You can update details, download e-Aadhaar, or check status.",
                "url": "https://uidai.gov.in",
            },
            "pan": {
                "title": "PAN Card Services",
                "snippet": "For PAN card application or changes, visit the NSDL or UTIITSL website. PAN is required for financial transactions.",
                "url": "https://www.incometax.gov.in",
            },
            "electricity": {
                "title": "Electricity Bill",
                "snippet": "For electricity bill payment, use your state electricity board's website or apps like Paytm, PhonePe, or Google Pay.",
                "url": "",
            },
            "water": {
                "title": "Water Supply",
                "snippet": "For water supply issues, contact your local municipal corporation or water board office.",
                "url": "",
            },
            "train": {
                "title": "Train Information",
                "snippet": "For train schedules and booking, use the IRCTC website (irctc.co.in) or Rail Yatri app. For inquiries, call 139.",
                "url": "https://www.irctc.co.in",
            },
            "bus": {
                "title": "Bus Services",
                "snippet": "For bus schedules, check your state transport website or apps like RedBus, AbhiBus. For local buses, contact your local bus depot.",
                "url": "",
            },
            "flight": {
                "title": "Flight Information",
                "snippet": "For flight bookings and status, check airline websites or apps like MakeMyTrip, Cleartrip. For flight status, call the airline.",
                "url": "",
            },
        }

        for keyword, info in knowledge_base.items():
            if keyword in query_lower:
                info["source"] = "Knowledge Base"
                return [info]

        return [{
            "title": "Information",
            "snippet": f"I can help you with general information about {query}. For specific details, please contact the relevant authority or visit their official website.",
            "url": "",
            "source": "Kataru",
        }]


class ResearchEngine:
    def __init__(self):
        self.search_engine = WebSearchEngine()

    async def research(self, query: str, context: str = "") -> dict:
        search_results = await self.search_engine.search(query, num_results=3)

        research_context = self._build_research_context(query, search_results, context)

        answer = await self._generate_research_answer(query, research_context)

        return {
            "query": query,
            "answer": answer,
            "sources": [{"title": r["title"], "url": r["url"], "source": r["source"]} for r in search_results],
            "has_research": len(search_results) > 0,
        }

    def _build_research_context(self, query: str, search_results: list[dict], context: str = "") -> str:
        parts = []

        if context:
            parts.append(f"Conversation context: {context}")

        if search_results:
            parts.append("Search results:")
            for i, result in enumerate(search_results, 1):
                parts.append(f"{i}. {result['title']}: {result['snippet']}")
                if result.get("url"):
                    parts.append(f"   Source: {result['url']}")

        return "\n".join(parts)

    async def _generate_research_answer(self, query: str, context: str) -> str:
        if not config.openai_api_key or config.openai_api_key.startswith("dummy"):
            return self._generate_demo_answer(query, context)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=config.openai_api_key)

            system_prompt = (
                "You are Kataru, a helpful multilingual customer support agent. "
                "You have access to research information to answer user questions. "
                "RULES:\n"
                "1. Use the research context to provide accurate answers\n"
                "2. Respond in the same language as the user (Hindi, English, or Hinglish)\n"
                "3. Keep responses under 40 words for voice, but can be longer for text\n"
                "4. Always be helpful and provide actionable information\n"
                "5. If you cannot find specific information, say so honestly\n"
                "6. NEVER provide medical diagnosis - say 'Please consult your doctor'\n"
                "7. NEVER provide legal advice - say 'Please consult a lawyer'\n"
                "8. NEVER provide financial advice - say 'Please consult a financial advisor'\n"
                "9. For emergencies, say 'Please call 112 immediately'\n"
                "10. Cite sources when available"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Research context:\n{context}\n\nUser question: {query}"},
            ]

            response_obj = await client.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                max_tokens=200,
                temperature=0.7,
            )

            return response_obj.choices[0].message.content

        except Exception as e:
            logger.error("research_llm_error", error=str(e))
            return self._generate_demo_answer(query, context)

    def _generate_demo_answer(self, query: str, context: str) -> str:
        query_lower = query.lower()

        if any(w in query_lower for w in ["emergency", "bachao", "ambulance", "112", "911", "urgent"]):
            return "For emergencies in India, call 112 immediately. For medical emergencies, call 108. Stay calm and provide your location to the operator."

        if any(w in query_lower for w in ["doctor", "hospital", "sick", "medicine", "fever", "pain"]):
            return "For medical issues, please consult a qualified doctor. I cannot provide medical diagnosis. For emergencies, call 108."

        if any(w in query_lower for w in ["legal", "court", "lawyer"]):
            return "For legal matters, please consult a qualified lawyer. I cannot provide legal advice."

        if any(w in query_lower for w in ["aadhar", "aadhaar"]):
            return "For Aadhaar services, visit uidai.gov.in or call 1947. You can update details, download e-Aadhaar, or check status online."

        if any(w in query_lower for w in ["pan", "pan card"]):
            return "For PAN card services, visit the NSDL website (tin-nsdl.com) or UTIITSL. You can apply for new PAN or make changes online."

        if any(w in query_lower for w in ["pension", "pf", "epf"]):
            return "For EPF/pension queries, visit epfindia.gov.in or call the EPFO helpline. You can check balance and claim status online."

        if any(w in query_lower for w in ["bill", "payment"]):
            return "For bill payments, you can use online banking, mobile apps like Paytm/PhonePe, or visit your nearest service center."

        if any(w in query_lower for w in ["electricity", "bijli"]):
            return "For electricity bill payment, use your state electricity board website or apps like Paytm, PhonePe, Google Pay."

        if any(w in query_lower for w in ["train", "railway"]):
            return "For train information, visit irctc.co.in or call 139. You can check schedules, book tickets, and track trains."

        if any(w in query_lower for w in ["weather", "mausam"]):
            return "For weather information, check weather.com or your local weather app. I cannot provide real-time weather data."

        if any(w in query_lower for w in ["government", "sarkari"]):
            return "For government services, visit india.gov.in or your nearest government office. Many services are available online."

        if any(w in query_lower for w in ["hello", "hi", "namaste"]):
            return "Namaste! I am here to help you. You can ask me about government services, bill payments, emergency numbers, or any general information."

        if any(w in query_lower for w in ["thank", "dhanyavaad"]):
            return "You are welcome! I am always here to help. Is there anything else you need?"

        if any(w in query_lower for w in ["bye", "goodbye"]):
            return "Goodbye! Take care. I am here whenever you need help."

        return f"I can help you with information about {query}. For specific details, please contact the relevant authority. How else can I assist you?"


research_engine = ResearchEngine()
