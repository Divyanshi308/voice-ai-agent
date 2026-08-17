import re
import time


class Guardrails:

    def __init__(self):
        self.medical_keywords = [
            "chest pain", "bleeding", "can't breathe", "heart attack", "stroke",
            "seizure", "overdose", "allergic reaction", "suicide", "kill myself",
            "dying", "unconscious", "choking", "diagnosis", "medication",
            "prescription", "treatment plan", "take this drug",
        ]

        self.emergency_keywords = [
            "fire", "shooting", "robbery", "accident", "drowning", "bomb",
            "hostage", "assault", "emergency", "help me", "call police",
        ]

        self.legal_keywords = [
            "sue", "lawyer", "attorney", "legal action", "court", "lawsuit",
            "file a complaint", "press charges", "sue you",
        ]

        self.financial_keywords = [
            "invest", "transfer money", "send funds", "credit score",
            "loan approval",
        ]

        self.blocked_response_patterns = [
            re.compile(r"you should take.*medicine", re.IGNORECASE),
            re.compile(r"your diagnosis is", re.IGNORECASE),
            re.compile(r"you have.*cancer", re.IGNORECASE),
            re.compile(r"you should invest", re.IGNORECASE),
            re.compile(r"I guarantee", re.IGNORECASE),
            re.compile(r"we will definitely", re.IGNORECASE),
            re.compile(r"you will recover", re.IGNORECASE),
            re.compile(r"take this dosage", re.IGNORECASE),
        ]

        self.safety_responses = {
            "medical": "This requires professional medical attention. Please call your local emergency number immediately.",
            "emergency": "This sounds like an emergency. Please call 112 or 911 immediately.",
            "legal": "I cannot provide legal advice. Let me connect you with a legal specialist.",
            "financial": "I cannot provide financial advice. Let me connect you with a financial specialist.",
            "uncertain": "I'm not certain about that. Let me connect you with someone who can help.",
            "deflection": "I want to make sure you get the right help. Let me connect you with a specialist.",
        }

        self._medical_kw_patterns = [re.compile(re.escape(kw)) for kw in self.medical_keywords]
        self._emergency_kw_patterns = [re.compile(re.escape(kw)) for kw in self.emergency_keywords]
        self._legal_kw_patterns = [re.compile(re.escape(kw)) for kw in self.legal_keywords]

        self.trigger_log: list[dict] = []

    def _log(self, call_id: str, trigger_type: str, text: str, action_taken: str):
        self.trigger_log.append({
            "timestamp": time.time(),
            "call_id": call_id,
            "trigger_type": trigger_type,
            "text": text,
            "action_taken": action_taken,
        })

    def check_input(self, caller_text: str) -> dict:
        lowered = caller_text.lower()

        for pattern in self._medical_kw_patterns:
            if pattern.search(lowered):
                self._log(
                    call_id=str(time.time()),
                    trigger_type="medical",
                    text=caller_text,
                    action_taken="escalated_medical",
                )
                return {
                    "triggered": True,
                    "type": "medical",
                    "response": self.safety_responses["medical"],
                }

        for pattern in self._emergency_kw_patterns:
            if pattern.search(lowered):
                self._log(
                    call_id=str(time.time()),
                    trigger_type="emergency",
                    text=caller_text,
                    action_taken="escalated_emergency",
                )
                return {
                    "triggered": True,
                    "type": "emergency",
                    "response": self.safety_responses["emergency"],
                }

        for pattern in self._legal_kw_patterns:
            if pattern.search(lowered):
                self._log(
                    call_id=str(time.time()),
                    trigger_type="legal",
                    text=caller_text,
                    action_taken="escalated_legal",
                )
                return {
                    "triggered": True,
                    "type": "legal",
                    "response": self.safety_responses["legal"],
                }

        for kw in self.financial_keywords:
            if kw in lowered:
                self._log(
                    call_id=str(time.time()),
                    trigger_type="financial",
                    text=caller_text,
                    action_taken="escalated_financial",
                )
                return {
                    "triggered": True,
                    "type": "financial",
                    "response": self.safety_responses["financial"],
                }

        self._log(
            call_id=str(time.time()),
            trigger_type="none",
            text=caller_text,
            action_taken="passed",
        )
        return {"triggered": False}

    def check_output(self, ai_response: str) -> dict:
        for pattern in self.blocked_response_patterns:
            if pattern.search(ai_response):
                self._log(
                    call_id=str(time.time()),
                    trigger_type="output_blocked",
                    text=ai_response,
                    action_taken="blocked_pattern",
                )
                return {
                    "blocked": True,
                    "reason": "matches_pattern",
                    "safe_response": self.safety_responses["deflection"],
                }

        promise_phrases = ["I guarantee", "we will definitely", "you will definitely"]
        for phrase in promise_phrases:
            if phrase.lower() in ai_response.lower():
                self._log(
                    call_id=str(time.time()),
                    trigger_type="output_blocked",
                    text=ai_response,
                    action_taken="blocked_promise",
                )
                return {
                    "blocked": True,
                    "reason": "makes_promise",
                    "safe_response": "I'll note your request.",
                }

        qualifiers = ["I think", "probably", "might", "could be", "I believe", "possibly", "may"]
        has_qualifier = any(q.lower() in ai_response.lower() for q in qualifiers)

        complex_topic_keywords = (
            self.medical_keywords + self.legal_keywords + self.financial_keywords
        )
        topic_is_complex = any(
            kw.lower() in ai_response.lower() for kw in complex_topic_keywords
        )

        if topic_is_complex and not has_qualifier:
            self._log(
                call_id=str(time.time()),
                trigger_type="output_warning",
                text=ai_response,
                action_taken="warning_hallucination",
            )
            return {
                "blocked": False,
                "warning": "might_be_hallucinating",
                "safe_response": ai_response,
            }

        self._log(
            call_id=str(time.time()),
            trigger_type="output_passed",
            text=ai_response,
            action_taken="passed",
        )
        return {"blocked": False, "safe_response": ai_response}

    def is_emergency(self, text: str) -> bool:
        lowered = text.lower()
        return any(kw.lower() in lowered for kw in self.emergency_keywords)

    def get_trigger_log(self) -> list[dict]:
        return list(self.trigger_log)

    def _reset_log(self):
        self.trigger_log.clear()

    def _find_matching_keywords(self, text: str, keyword_set: list[str]) -> list[str]:
        lowered = text.lower()
        return [kw for kw in keyword_set if kw.lower() in lowered]

    def _find_matching_patterns(self, text: str) -> list[re.Pattern]:
        return [p for p in self.blocked_response_patterns if p.search(text)]
