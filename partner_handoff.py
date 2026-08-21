import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

from database import get_connection
from partner_network import DECLINE_PHRASES, get_partner


TERMINAL_CASE_STATUSES = {"completed", "closed", "cancelled"}
INTERNAL_MARKERS = (
    "commission_notes", "partner_request_id", "partner_id=", "client_telegram_id",
    "внутренняя комиссия", "служебная информация",
)
UNSAFE_ACTION_CLAIMS = (
    "бронирование гарантировано", "100% доступно", "вариант подтверждён",
    "вариант подтвержден", "гарантированно доступно",
)
KNOWN_AREAS = {
    "rawai": ("rawai", "раваи"),
    "kata": ("kata", "ката"),
    "karon": ("karon", "карон"),
    "patong": ("patong", "патонг"),
    "kamala": ("kamala", "камала"),
    "bang_tao": ("bang tao", "банг тао"),
}


class OfferHandoffError(RuntimeError):
    pass


class DuplicateOfferSendError(OfferHandoffError):
    pass


class OfferTelegramError(OfferHandoffError):
    pass


class OfferExtractor(Protocol):
    def extract(self, raw_response: str) -> dict: ...


def _now():
    return datetime.now(timezone.utc).isoformat()


def _valid_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except (TypeError, ValueError):
        return False


def _contains_partner_contact(text):
    return bool(
        re.search(r"(?<!\w)@[A-Za-z0-9_]{5,}|(?:t\.me|telegram\.me)/", text, re.I)
        or re.search(r"(?:\+?\d[\d\s()\-]{8,}\d)", text)
    )


def _safe_client_text(text):
    value = re.sub(r"(?<!\w)@[A-Za-z0-9_]{5,}", "", str(text or ""))
    value = re.sub(r"https?://(?:t\.me|telegram\.me)/\S+", "", value, flags=re.I)
    value = re.sub(r"(?:\+?\d[\d\s()\-]{8,}\d)", "", value)
    return " ".join(value.split())


class DeterministicOfferExtractor:
    PRICE_PATTERNS = (
        re.compile(r"(?:฿|₽|\$)\s*\d[\d\s,.]*", re.IGNORECASE),
        re.compile(
            r"\d[\d\s,.]*\s*(?:THB|USD|RUB|бат(?:ов|а)?|руб(?:лей|ля|ль)?)\b",
            re.IGNORECASE,
        ),
    )

    def extract(self, raw_response):
        raw = str(raw_response or "")
        urls = [
            value.rstrip(".,);]")
            for value in re.findall(r"https?://[^\s<>\"]+", raw, re.IGNORECASE)
        ]
        valid_urls = [value for value in urls if _valid_url(value)]
        price_matches = []
        for pattern in self.PRICE_PATTERNS:
            price_matches.extend(match.group(0).strip() for match in pattern.finditer(raw))
        price_matches = list(dict.fromkeys(price_matches))
        price_text = price_matches[0] if len(price_matches) == 1 else None
        currency = self._currency_from_price(price_text)
        conditions = self._extract_conditions(raw)
        description = re.sub(r"https?://[^\s<>\"]+", "", raw).strip()
        return {
            "offer_title": None,
            "offer_description": description or None,
            "price_text": price_text,
            "currency": currency,
            "url": valid_urls[0] if len(valid_urls) == 1 else None,
            "conditions": conditions,
            "all_urls": urls,
            "valid_urls": valid_urls,
            "price_matches": price_matches,
        }

    @staticmethod
    def _currency_from_price(price_text):
        normalized = str(price_text or "").casefold()
        if "thb" in normalized or "бат" in normalized or "฿" in normalized:
            return "THB"
        if "rub" in normalized or "руб" in normalized or "₽" in normalized:
            return "RUB"
        if "usd" in normalized or "$" in normalized:
            return "USD"
        return None

    @staticmethod
    def _extract_conditions(raw):
        lines = []
        for line in str(raw or "").splitlines():
            normalized = line.casefold()
            if any(marker in normalized for marker in ("услов", "депозит", "предоплат")):
                lines.append(line.strip())
        return "\n".join(lines) or None


def _row_to_offer(row):
    if not row:
        return None
    offer = dict(row)
    try:
        offer["validation_reasons"] = json.loads(offer["validation_reasons"] or "[]")
    except json.JSONDecodeError:
        offer["validation_reasons"] = []
    try:
        offer["telegram_metadata"] = json.loads(offer["telegram_metadata"] or "null")
    except json.JSONDecodeError:
        offer["telegram_metadata"] = None
    return offer


def get_offer(offer_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return _row_to_offer(
            connection.execute(
                "SELECT * FROM partner_offers WHERE id = ?", (offer_id,)
            ).fetchone()
        )
    finally:
        connection.close()


def list_offers(limit=20, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT po.*, p.name AS partner_name
            FROM partner_offers po JOIN partners p ON p.id = po.partner_id
            ORDER BY po.id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [_row_to_offer(row) for row in rows]
    finally:
        connection.close()


def get_offer_context(offer_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT po.*, pr.status AS partner_request_status,
                   c.client_id, c.category, c.data AS case_data,
                   c.status AS case_status, cl.telegram_id AS client_telegram_id
            FROM partner_offers po
            JOIN partner_requests pr ON pr.id = po.partner_request_id
            JOIN cases c ON c.id = po.case_id
            JOIN clients cl ON cl.id = c.client_id
            WHERE po.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if not row:
            raise OfferHandoffError("Предложение не найдено")
        result = dict(row)
        try:
            result["case_data"] = json.loads(result["case_data"] or "{}")
        except json.JSONDecodeError:
            result["case_data"] = {}
        return result
    finally:
        connection.close()


def _is_complex_multi_option(raw, extracted):
    normalized = str(raw or "").casefold()
    return (
        len(extracted["all_urls"]) > 1
        or len(extracted["price_matches"]) > 1
        or "вариант 1" in normalized
        or "два варианта" in normalized
        or bool(re.search(r"(?:^|\n)\s*1[.)].*(?:\n).*2[.)]", raw, re.DOTALL))
    )


def validate_partner_offer(
    offer, case, partner, handoff_mode="review", partner_request=None,
    extracted=None,
):
    raw = offer.get("raw_partner_response", "")
    normalized = raw.casefold()
    if any(phrase in normalized for phrase in DECLINE_PHRASES):
        return {"decision": "declined", "reasons": ["partner_declined"], "score": 0.0}

    extracted = extracted or DeterministicOfferExtractor().extract(raw)
    reasons = []
    if handoff_mode != "hybrid":
        reasons.append("global_review_mode")
    if not partner or partner.get("status") != "active":
        reasons.append("partner_not_active")
    if not partner or not bool(partner.get("auto_handoff_enabled")):
        reasons.append("partner_auto_handoff_disabled")
    if not partner_request or partner_request.get("status") != "responded":
        reasons.append("partner_request_not_responded")
    if not case or not case.get("client_id"):
        reasons.append("case_or_client_missing")
    elif case.get("status") in TERMINAL_CASE_STATUSES:
        reasons.append("case_terminal")
    if not raw.strip() or not offer.get("offer_description"):
        reasons.append("insufficient_description")
    if not offer.get("price_text") and not offer.get("url"):
        reasons.append("insufficient_offer_data")
    if _is_complex_multi_option(raw, extracted):
        reasons.append("multiple_options_require_review")
    if ("http" in normalized or "www." in normalized) and not extracted["valid_urls"]:
        reasons.append("invalid_url")
    if len(extracted["valid_urls"]) > 1:
        reasons.append("multiple_urls")
    if extracted["price_matches"] and not offer.get("currency"):
        reasons.append("currency_unclear")
    if any(marker in normalized for marker in INTERNAL_MARKERS):
        reasons.append("internal_data_detected")
    if any(claim in normalized for claim in UNSAFE_ACTION_CLAIMS):
        reasons.append("unsafe_action_claim")
    if _contains_partner_contact(raw):
        reasons.append("partner_contact_detected")
    case_location = str((case.get("data") or {}).get("location") or "").casefold()
    mentioned_areas = {
        canonical for canonical, aliases in KNOWN_AREAS.items()
        if any(alias in normalized for alias in aliases)
    }
    case_areas = {
        canonical for canonical, aliases in KNOWN_AREAS.items()
        if any(alias in case_location for alias in aliases)
    }
    if case_areas and mentioned_areas and case_areas.isdisjoint(mentioned_areas):
        reasons.append("location_conflict")
    metadata = offer.get("telegram_metadata") or {}
    if metadata.get("has_media") and not raw.strip():
        reasons.append("media_without_description")
    if offer.get("parser_error"):
        reasons.append("parser_error")

    reasons = list(dict.fromkeys(reasons))
    decision = "review_required" if reasons else "auto_send"
    score = max(0.0, 1.0 - 0.1 * len(reasons))
    return {"decision": decision, "reasons": reasons, "score": score}


def create_offer_from_partner_response(
    request_id, handoff_mode="review", extractor=None, db_path=None,
):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        request = connection.execute(
            "SELECT * FROM partner_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not request:
            raise OfferHandoffError("Partner request не найден")
        request = dict(request)
        raw_response = request.get("partner_response") or ""
        if request["status"] == "declined" or any(
            phrase in raw_response.casefold() for phrase in DECLINE_PHRASES
        ):
            return None
        if request["status"] != "responded":
            raise OfferHandoffError("Offer можно создать только из responded request")
        existing = connection.execute(
            "SELECT id FROM partner_offers WHERE partner_request_id = ?",
            (request_id,),
        ).fetchone()
        if existing:
            return get_offer(existing[0], db_path)
        case_row = connection.execute(
            "SELECT * FROM cases WHERE id = ?", (request["case_id"],)
        ).fetchone()
        if not case_row:
            raise OfferHandoffError("Кейс не найден")
        case = dict(case_row)
        case["data"] = json.loads(case.get("data") or "{}")
        partner = get_partner(request["partner_id"], db_path)
        extractor = extractor or DeterministicOfferExtractor()
        parser_error = False
        try:
            extracted = extractor.extract(raw_response)
        except Exception:
            parser_error = True
            extracted = {
                "offer_title": None,
                "offer_description": raw_response or None,
                "price_text": None,
                "currency": None,
                "url": None,
                "conditions": None,
                "all_urls": [],
                "valid_urls": [],
                "price_matches": [],
            }
        metadata = request.get("partner_response_metadata")
        try:
            parsed_metadata = json.loads(metadata) if metadata else None
        except json.JSONDecodeError:
            parsed_metadata = {"metadata_parse_error": True}
            parser_error = True
        offer_data = {
            **extracted,
            "raw_partner_response": raw_response,
            "telegram_metadata": parsed_metadata,
            "parser_error": parser_error,
        }
        decision = validate_partner_offer(
            offer_data, case, partner, handoff_mode, request, extracted
        )
        status = "ready_to_send" if decision["decision"] == "auto_send" else "needs_review"
        timestamp = _now()
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO partner_offers
                    (partner_request_id, case_id, partner_id, status,
                     handoff_decision, raw_partner_response, offer_title,
                     offer_description, price_text, currency, url, conditions,
                     validation_reasons, validation_score, telegram_metadata,
                     validated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, request["case_id"], request["partner_id"], status,
                    decision["decision"], offer_data["raw_partner_response"],
                    extracted["offer_title"], extracted["offer_description"],
                    extracted["price_text"], extracted["currency"], extracted["url"],
                    extracted["conditions"], json.dumps(decision["reasons"]),
                    decision["score"], metadata, timestamp, timestamp,
                ),
            )
        return get_offer(cursor.lastrowid, db_path)
    finally:
        connection.close()


def format_client_offer(offer, case):
    data = case.get("data") or case.get("case_data") or {}
    category = case.get("category")
    icon = "🏠" if category == "housing" else "🤝"
    lines = [f"{icon} Получили вариант от нашего партнёра"]
    if data.get("location"):
        lines.extend(("", "Район:", str(data["location"])))
    if offer.get("price_text"):
        lines.extend(("", "Стоимость:", str(offer["price_text"])))
    if offer.get("offer_description"):
        safe_description = _safe_client_text(offer["offer_description"])
        if safe_description:
            lines.extend(("", "Описание:", safe_description))
    if offer.get("url") and urlparse(str(offer["url"])).netloc.casefold() not in {
        "t.me", "telegram.me"
    }:
        lines.extend(("", "Ссылка:", str(offer["url"])))
    if offer.get("conditions"):
        lines.extend(("", "Условия:", str(offer["conditions"])))
    lines.extend((
        "",
        "Предложение получено от партнёра. Актуальность и доступность нужно "
        "уточнить перед бронированием.",
    ))
    return "\n".join(lines)


async def send_offer_to_client(
    offer_id, telegram_sender, manual_approval=False, db_path=None,
):
    offer = get_offer(offer_id, db_path)
    if not offer:
        raise OfferHandoffError("Предложение не найдено")
    if offer["status"] == "sent_to_client":
        raise DuplicateOfferSendError("Предложение уже отправлено клиенту")
    allowed = (
        offer["status"] == "ready_to_send" and offer["handoff_decision"] == "auto_send"
    ) or (manual_approval and offer["status"] == "needs_review")
    if not allowed:
        raise OfferHandoffError("Предложение не разрешено к отправке")
    context = get_offer_context(offer_id, db_path)
    message_text = format_client_offer(offer, context)
    try:
        message = await telegram_sender(
            chat_id=context["client_telegram_id"], text=message_text
        )
        message_id = (
            message.get("message_id") if isinstance(message, dict)
            else getattr(message, "message_id", None)
        )
        if message_id is None:
            raise RuntimeError("Telegram response has no message_id")
    except Exception as error:
        connection = get_connection(db_path)
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE partner_offers SET error_code = 'telegram_failure',
                        error_message = ?, updated_at = ? WHERE id = ?
                    """,
                    (type(error).__name__, _now(), offer_id),
                )
        finally:
            connection.close()
        raise OfferTelegramError("Telegram не подтвердил отправку клиенту") from error
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE partner_offers SET status = 'sent_to_client', sent_at = ?,
                    client_telegram_message_id = ?, error_code = NULL,
                    error_message = NULL, updated_at = ? WHERE id = ?
                """,
                (_now(), message_id, _now(), offer_id),
            )
    finally:
        connection.close()
    return get_offer(offer_id, db_path)


def reject_offer(offer_id, db_path=None):
    offer = get_offer(offer_id, db_path)
    if not offer or offer["status"] == "sent_to_client":
        raise OfferHandoffError("Предложение нельзя отклонить")
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                "UPDATE partner_offers SET status = 'rejected', updated_at = ? WHERE id = ?",
                (_now(), offer_id),
            )
    finally:
        connection.close()
    return get_offer(offer_id, db_path)
