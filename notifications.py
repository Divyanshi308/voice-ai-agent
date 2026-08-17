import asyncio
import logging
from typing import Optional

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from config import config

logger = logging.getLogger(__name__)

SMS_CHAR_LIMIT = 160
MAX_MULTI_SMS_SEGMENTS = 3
MAX_MULTI_SMS_CHARS = SMS_CHAR_LIMIT * MAX_MULTI_SMS_SEGMENTS


class NotificationManager:
    def __init__(self) -> None:
        self.client = TwilioClient(
            config.twilio_account_sid, config.twilio_auth_token
        )
        self.twilio_phone_number = config.twilio_phone_number
        self.templates: dict[str, str] = {
            "en": (
                "Hi {name}, your case #{case_number} has been created. "
                "Issue: {summary}. Our team will respond within {timeframe}."
            ),
            "hi": (
                "Namaste {name}, aapka case #{case_number} ban gaya hai. "
                "Issue: {summary}. Hamari team {timeframe} mein jawab degi."
            ),
        }
        self.default_timeframe = "24 hours"

    def _select_template(self, language: str) -> str:
        return self.templates.get(language, self.templates["en"])

    def _format_message(
        self,
        name: str,
        case_number: str,
        summary: str,
        language: str,
        timeframe: Optional[str] = None,
    ) -> str:
        template = self._select_template(language)
        resolved_timeframe = timeframe or self.default_timeframe
        return template.format(
            name=name,
            case_number=case_number,
            summary=summary,
            timeframe=resolved_timeframe,
        )

    @staticmethod
    def _check_message_length(message: str) -> tuple[bool, int]:
        length = len(message)
        within_limit = length <= MAX_MULTI_SMS_CHARS
        segments = (length + SMS_CHAR_LIMIT - 1) // SMS_CHAR_LIMIT
        return within_limit, segments

    def _send_sms_sync(
        self, phone_number: str, body: str
    ) -> dict:
        message = self.client.messages.create(
            body=body,
            from_=self.twilio_phone_number,
            to=phone_number,
        )
        return {
            "sid": message.sid,
            "status": message.status,
            "error_code": message.error_code,
            "error_message": message.error_message,
        }

    async def send_sms(
        self,
        phone_number: str,
        case_number: str,
        summary: str,
        language: str = "en",
        name: str = "Customer",
        timeframe: Optional[str] = None,
    ) -> bool:
        if not phone_number or not phone_number.startswith("+"):
            logger.error(
                "invalid_phone_number phone=%s case_number=%s",
                phone_number,
                case_number,
            )
            return False

        formatted_message = self._format_message(
            name=name,
            case_number=case_number,
            summary=summary,
            language=language,
            timeframe=timeframe,
        )

        within_limit, segments = self._check_message_length(formatted_message)

        if not within_limit:
            logger.warning(
                "message_too_long phone=%s case_number=%s length=%d max=%d",
                phone_number,
                case_number,
                len(formatted_message),
                MAX_MULTI_SMS_CHARS,
            )
            return False

        logger.info(
            "sending_sms phone=%s case_number=%s language=%s segments=%d",
            phone_number,
            case_number,
            language,
            segments,
        )

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, self._send_sms_sync, phone_number, formatted_message
            )
        except TwilioRestException as exc:
            logger.error(
                "twilio_error phone=%s case_number=%s error_code=%s message=%s",
                phone_number,
                case_number,
                getattr(exc, "code", "N/A"),
                str(exc),
            )
            return False
        except Exception as exc:
            logger.error(
                "sms_send_failed phone=%s case_number=%s error=%s",
                phone_number,
                case_number,
                exc,
            )
            return False

        if result["error_code"]:
            logger.error(
                "sms_delivery_error phone=%s case_number=%s sid=%s error_code=%s error_message=%s",
                phone_number,
                case_number,
                result["sid"],
                result["error_code"],
                result["error_message"],
            )
            return False

        logger.info(
            "sms_sent phone=%s case_number=%s sid=%s status=%s segments=%d",
            phone_number,
            case_number,
            result["sid"],
            result["status"],
            segments,
        )

        return True
