CASE_STATUS_RU = {
    "new": "Новый",
    "active": "Собираются данные",
    "ready_for_search": "Готов к поиску",
    "searching": "Идёт поиск",
    "results_presented": "Найдены варианты",
    "completed": "Завершён",
    "closed": "Закрыт",
    "cancelled": "Отменён",
}

OFFER_STATUS_RU = {
    "needs_review": "Требуется проверка",
    "ready_to_send": "Готово к отправке",
    "sent_to_client": "Отправлено клиенту",
    "rejected": "Отклонено",
    "expired": "Истекло",
}

HANDOFF_DECISION_RU = {
    "auto_send": "Автоматическая отправка",
    "review_required": "Ручная проверка",
    "declined": "Отказ",
}

PARTNER_STATUS_RU = {
    "candidate": "Кандидат",
    "active": "Активен",
    "paused": "Приостановлен",
    "blocked": "Заблокирован",
}

PARTNER_REQUEST_STATUS_RU = {
    "created": "Создан",
    "sent": "Отправлен партнёру",
    "responded": "Получен ответ",
    "declined": "Отказ",
    "failed": "Ошибка отправки",
    "cancelled": "Отменён",
}

SERVICE_CATEGORY_RU = {
    "housing": "Жильё", "transfer": "Трансфер",
    "car_rental": "Аренда автомобиля", "bike_rental": "Аренда байка",
    "excursions": "Экскурсии", "boats": "Катера", "fishing": "Рыбалка",
    "visa": "Визы", "sim": "SIM-карты", "activities": "Активности",
    "medical": "Медицина", "beauty": "Красота", "food": "Питание",
    "delivery": "Доставка", "guide": "Гид",
    "photo_video": "Фото и видео", "other": "Другое",
}

VALIDATION_REASON_RU = {
    "global_review_mode": "Включён режим обязательной ручной проверки",
    "partner_not_active": "Партнёр не активен",
    "partner_auto_handoff_disabled": "Автоотправка для партнёра выключена",
    "partner_request_not_responded": "Ответ партнёра не подтверждён",
    "case_or_client_missing": "Не найден кейс или клиент",
    "case_terminal": "Кейс уже закрыт",
    "insufficient_description": "Недостаточно описания",
    "insufficient_offer_data": "Недостаточно данных предложения",
    "multiple_options_require_review": "В ответе несколько вариантов",
    "invalid_url": "Ссылка некорректна",
    "multiple_urls": "В ответе несколько ссылок",
    "currency_unclear": "Валюта не определена",
    "internal_data_detected": "Обнаружены внутренние данные",
    "unsafe_action_claim": "Есть утверждение, требующее проверки",
    "partner_contact_detected": "Обнаружен прямой контакт партнёра",
    "location_conflict": "Район отличается от параметров кейса",
    "media_without_description": "Медиа получено без понятного описания",
    "parser_error": "Ответ не удалось безопасно структурировать",
}


def _translated(mapping, value):
    return mapping.get(value, "Неизвестно")


def format_case_status_ru(value):
    return _translated(CASE_STATUS_RU, value)


def format_offer_status_ru(value):
    return _translated(OFFER_STATUS_RU, value)


def format_handoff_decision_ru(value):
    return _translated(HANDOFF_DECISION_RU, value)


def format_partner_status_ru(value):
    return _translated(PARTNER_STATUS_RU, value)


def format_partner_request_status_ru(value):
    return _translated(PARTNER_REQUEST_STATUS_RU, value)


def format_service_category_ru(value):
    return _translated(SERVICE_CATEGORY_RU, value)


def format_validation_reason_ru(value):
    return _translated(VALIDATION_REASON_RU, value)
