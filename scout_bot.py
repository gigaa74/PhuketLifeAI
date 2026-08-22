from telegram.ext import Application, MessageHandler, filters
from dotenv import load_dotenv

from database import init_db
from scout_candidates import save_scout_candidate
from scout_config import load_scout_settings
from scout_detector import classify_scout_message


SCOUT_MESSAGE_FILTER = (filters.TEXT | filters.CAPTION) & ~filters.COMMAND


def _format_owner_notification(candidate):
    reasons = "; ".join(candidate["detection_reasons"])
    identity = (
        f"Telegram user ID: {candidate['source_user_id']}"
        if candidate.get("source_user_id") is not None
        else "Telegram user ID: не указан"
    )
    username = candidate.get("source_username")
    return (
        "🔎 Scout обнаружил кандидата\n\n"
        f"Тип: {candidate['scout_type']}\n"
        f"Категория: {candidate['detected_category']}\n"
        f"Источник: {candidate.get('source_chat_title') or candidate['source_chat_id']}\n"
        f"{identity}\n"
        f"Username: {'@' + username if username else 'не указан'}\n"
        f"Причины: {reasons}\n"
        f"Уверенность: {candidate['confidence']:.2f}\n\n"
        f"Исходное сообщение:\n{candidate['original_text']}\n\n"
        "Статус: требуется решение владельца"
    )


async def process_scout_observation(scout_type, allowed_chat_ids, observation,
                                    owner_user_id=None, owner_notifier=None,
                                    db_path=None):
    if observation["source_chat_id"] not in allowed_chat_ids:
        return {"processed": False, "reason": "chat_not_allowed"}
    detection = classify_scout_message(scout_type, observation.get("original_text"))
    if not detection:
        return {"processed": False, "reason": "no_strong_signal"}
    candidate, created = save_scout_candidate(
        scout_type, observation, detection, db_path
    )
    notified = False
    notification_failed = False
    if created and owner_user_id is not None and owner_notifier is not None:
        try:
            await owner_notifier(
                chat_id=owner_user_id, text=_format_owner_notification(candidate)
            )
            notified = True
        except Exception:
            notification_failed = True
    return {
        "processed": True, "candidate": candidate, "created": created,
        "owner_notified": notified, "outreach_performed": False,
        "owner_notification_failed": notification_failed,
    }


def _observation_from_update(update):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    return {
        "source_chat_id": chat.id,
        "source_chat_title": getattr(chat, "title", None),
        "source_message_id": message.message_id,
        "source_user_id": user.id if user else None,
        "source_username": user.username if user else None,
        "original_text": message.text or message.caption or "",
    }


def build_scout_application(settings, db_path=None):
    init_db(db_path)
    application = Application.builder().token(settings.bot_token).build()

    async def observe(update, context):
        await process_scout_observation(
            settings.scout_type, settings.allowed_chat_ids,
            _observation_from_update(update), settings.owner_user_id,
            context.bot.send_message, db_path,
        )

    application.add_handler(MessageHandler(SCOUT_MESSAGE_FILTER, observe))
    return application


def run_scout_bot(scout_type, dotenv_path=None):
    load_dotenv(dotenv_path=dotenv_path)
    settings = load_scout_settings(scout_type)
    if not settings.allowed_chat_ids:
        print(
            f"[{scout_type.upper()} SCOUT] Allowlist пуст: "
            "групповые сообщения обрабатываться не будут."
        )
    if settings.outreach_enabled:
        print(
            f"[{scout_type.upper()} SCOUT] Outreach остаётся отключён: "
            "Sprint #8 работает только в observe-only режиме."
        )
    build_scout_application(settings).run_polling()
