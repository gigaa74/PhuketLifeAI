import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from geo_relevance import (
    canonicalize_known_property_url,
    result_has_phuket_geo_evidence,
)
from search_presentation import CONCRETE_PROPERTY, LISTING_PAGE


SEARCH_WITH_RESULTS = "with_results"
SEARCH_NO_RESULTS = "no_results"
SEARCH_PROVIDER_ERROR = "provider_error"

# =========================================================
# SEARCH ENGINE
# Phuket Life
# Универсальный движок поиска жилья
# =========================================================


# =========================================================
# PARSERS
# =========================================================

def parse_budget_rub(budget_text):
    """
    Извлекает бюджет в рублях.

    Примеры:
    "до 20 тыс. рублей" -> 20000
    "до 20 тысяч рублей" -> 20000
    "до 50 000 рублей" -> 50000
    "30000 рублей" -> 30000
    "50 000" -> 50000
    """

    if not budget_text:
        return None

    text = str(budget_text).lower().strip()

    # 20 тыс / 20 тысяч
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*"
        r"(?:тыс\.?|тысяч(?:а|и)?|тысячи)",
        text,
    )

    if match:
        value = float(
            match.group(1).replace(",", ".")
        )

        return int(value * 1000)

    # 50 000 / 100 000 / 1 000 000
    match = re.search(
        r"\d{1,3}(?:[\s\u00A0]\d{3})+",
        text,
    )

    if match:
        number_text = re.sub(
            r"[\s\u00A0]",
            "",
            match.group(0),
        )

        return int(number_text)

    # 50000 / 30000
    match = re.search(
        r"\d+",
        text,
    )

    if match:
        return int(
            match.group(0)
        )

    return None


def parse_people(value):
    """
    Преобразует количество гостей в число.
    """

    if value is None:
        return None

    text = str(value).lower()

    numbers = re.findall(
        r"\d+",
        text,
    )

    if not numbers:
        return None

    return int(
        numbers[0]
    )


def has_pet(case_data):
    """
    Проверяет наличие питомца.
    """

    pet = case_data.get(
        "pet",
        "",
    )

    if not pet:
        return False

    text = str(
        pet
    ).lower().strip()

    return text not in (
        "нет",
        "нету",
        "без животных",
        "без питомца",
        "без питомцев",
        "no",
        "none",
    )


# =========================================================
# DATE / STAY LENGTH
# =========================================================

MONTHS = {
    "января": 1,
    "январь": 1,
    "февраля": 2,
    "февраль": 2,
    "марта": 3,
    "март": 3,
    "апреля": 4,
    "апрель": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июля": 7,
    "июль": 7,
    "августа": 8,
    "август": 8,
    "сентября": 9,
    "сентябрь": 9,
    "октября": 10,
    "октябрь": 10,
    "ноября": 11,
    "ноябрь": 11,
    "декабря": 12,
    "декабрь": 12,
}


def parse_simple_date(value, default_year=None):
    """
    Понимает простые даты:
    "1 сентября"
    "01.09.2026"
    "2026-09-01"
    """

    if not value:
        return None

    if default_year is None:
        default_year = datetime.now().year

    text = str(
        value
    ).lower().strip()

    # YYYY-MM-DD
    match = re.search(
        r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b",
        text,
    )

    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None

    # DD.MM.YYYY
    match = re.search(
        r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b",
        text,
    )

    if match:
        try:
            return datetime(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
            )
        except ValueError:
            return None

    # "1 сентября 2026"
    match = re.search(
        r"\b(\d{1,2})\s+([а-яё]+)"
        r"(?:\s+(\d{4}))?\b",
        text,
    )

    if match:
        day = int(
            match.group(1)
        )

        month_name = (
            match.group(2)
        )

        month = MONTHS.get(
            month_name
        )

        if not month:
            return None

        year = (
            int(match.group(3))
            if match.group(3)
            else default_year
        )

        try:
            return datetime(
                year,
                month,
                day,
            )
        except ValueError:
            return None

    return None


def get_stay_days(search_request):
    """
    Возвращает примерную длительность проживания.
    """

    arrival = parse_simple_date(
        search_request.get(
            "arrival_date"
        )
    )

    departure = parse_simple_date(
        search_request.get(
            "departure_date"
        )
    )

    if not arrival or not departure:
        return None

    # Если даты без года и выезд оказался раньше,
    # считаем, что выезд уже в следующем году.
    if departure <= arrival:
        try:
            departure = departure.replace(
                year=departure.year + 1
            )
        except ValueError:
            return None

    return (
        departure - arrival
    ).days


def get_rental_type(search_request):
    """
    Определяет тип аренды по длительности.

    short  -> до 21 дня
    medium -> 22-89 дней
    long   -> 90+ дней
    """

    stay_days = get_stay_days(
        search_request
    )

    if stay_days is None:
        return "unknown"

    if stay_days <= 21:
        return "short"

    if stay_days < 90:
        return "medium"

    return "long"


# =========================================================
# CASE VALIDATION
# =========================================================

def validate_case(case):
    """
    Проверяет, достаточно ли данных для поиска жилья.
    """

    if not case:
        return {
            "ready": False,
            "reason": "Кейс не найден.",
        }

    if case.get(
        "category"
    ) != "housing":
        return {
            "ready": False,
            "reason": (
                "Поиск жилья поддерживается "
                "только для категории housing."
            ),
        }

    data = case.get(
        "data",
        {},
    )

    required_fields = [
        "arrival_date",
        "departure_date",
        "people",
        "budget",
    ]

    missing = []

    for field in required_fields:
        value = data.get(
            field
        )

        if value in (
            None,
            "",
            [],
            {},
        ):
            missing.append(
                field
            )

    if missing:
        return {
            "ready": False,
            "reason": (
                "Не хватает обязательных "
                "параметров."
            ),
            "missing_data": missing,
        }

    return {
        "ready": True,
        "reason": (
            "Кейс готов к поиску."
        ),
    }


# =========================================================
# BUILD SEARCH REQUEST
# =========================================================

def build_search_request(case):
    """
    Преобразует кейс Phuket Life
    в единый поисковый запрос.
    """

    data = case.get(
        "data",
        {},
    )

    budget_rub = parse_budget_rub(
        data.get(
            "budget"
        )
    )

    people = parse_people(
        data.get(
            "people"
        )
    )

    return {
        "category": case.get(
            "category"
        ),

        "arrival_date": data.get(
            "arrival_date"
        ),

        "departure_date": data.get(
            "departure_date"
        ),

        "people": people,

        "budget_rub": budget_rub,

        "budget_original": data.get(
            "budget"
        ),

        "location": data.get(
            "location"
        ),

        "pet": data.get(
            "pet"
        ),

        "has_pet": has_pet(
            data
        ),

        "housing_type": data.get(
            "housing_type"
        ),
    }


def build_concrete_search_queries(search_request):
    """Build focused discovery queries without inventing offer facts."""
    location = str(search_request.get("location") or "Phuket").strip()
    housing_type = str(
        search_request.get("housing_type") or "apartment condo hotel"
    ).strip()
    dates = " ".join(
        str(search_request.get(field) or "").strip()
        for field in ("arrival_date", "departure_date")
    ).strip()
    pet = " pet friendly" if search_request.get("has_pet") else ""
    place = location if "phuket" in location.lower() else f"{location} Phuket"
    context = " ".join(part for part in (place, dates) if part)

    queries = [
        f"{context} {housing_type} rent{pet}",
        f"{place} condo rent specific property{pet}",
        f"{place} hotel Booking{pet}",
        f"site:booking.com/hotel {context}{pet}",
        f"site:airbnb.com/rooms {context}{pet}",
        f"site:fazwaz.com property rent {place}{pet}",
        f"site:thailand-property.com property {place} rent{pet}",
        f"{place} аренда конкретные апартаменты{pet}",
    ]
    return list(dict.fromkeys(" ".join(query.split()) for query in queries))


# =========================================================
# NORMALIZED RESULT
# =========================================================

def normalize_result(
    result,
    source_name,
):
    """
    Приводит результат любого провайдера
    к единому формату Phuket Life.
    """

    if not isinstance(
        result,
        dict,
    ):
        return None

    url = canonicalize_known_property_url(result.get(
        "url",
        "",
    ))

    domain = result.get(
        "domain",
        "",
    )

    if not domain and url:
        try:
            domain = (
                urlparse(url)
                .netloc
                .lower()
            )
        except Exception:
            domain = ""

    title = str(result.get("title") or result.get("name") or "")
    snippet = str(result.get("snippet") or result.get("description") or "")
    location_text = str(
        result.get("location_text") or result.get("location") or ""
    )

    return {
        "source": source_name,

        "domain": domain,

        "title": title,

        "name": title,

        "property_type": result.get(
            "property_type",
            "",
        ),

        "location_text": location_text,

        "location": location_text,

        "address": result.get(
            "address",
            "",
        ),

        "price": result.get(
            "price"
        ),

        "price_text": str(result.get("price_text") or ""),

        "currency": result.get(
            "currency",
            "",
        ),

        "price_rub": result.get(
            "price_rub"
        ),

        "rating": result.get(
            "rating"
        ),

        "reviews": result.get(
            "reviews"
        ),

        "pet_friendly": result.get(
            "pet_friendly"
        ),

        "url": url,

        "image": result.get(
            "image",
            "",
        ),

        "snippet": snippet,

        "description": snippet,

        "result_type": result.get("result_type") or classify_result_type(
            result
        ),

        "search_score": 0,

        "retrieved_at": result.get("retrieved_at") or datetime.now(
            timezone.utc
        ).isoformat(),
    }


def classify_result_type(result):
    """Conservatively distinguish object pages from catalogs/search pages."""
    url = str(result.get("url", "")).lower()
    title = str(result.get("name", "")).lower()
    text = f"{title} {url}"

    listing_markers = (
        "/search",
        "/stays/",
        "/properties",
        "/property-for-rent",
        "/apartments/city/",
        "/condos",
        "/apartments",
        "лучших",
        "объявлен",
        "предложен",
        "недвижимость в",
        "аренда жилья в",
    )
    if any(marker in text for marker in listing_markers):
        return LISTING_PAGE

    concrete_patterns = (
        r"booking\.[^/]+/hotel/[^/]+/[^/?#]+",
        r"airbnb\.[^/]+/rooms/\d+",
        r"fazwaz\.[^/]+/(?:property-rent|property-sale)/[^/?#]+",
        r"thailand-property\.[^/]+/property/[^/?#]+",
        r"/property/[^/?#]+",
        r"/listing/[^/?#]+",
    )
    if any(re.search(pattern, url) for pattern in concrete_patterns):
        return CONCRETE_PROPERTY

    return LISTING_PAGE


def normalize_result_identifier(url):
    return str(url or "").strip().lower().rstrip("/")


# =========================================================
# SEARCH PROVIDER BASE
# =========================================================

class SearchProvider:
    """
    Базовый интерфейс поискового провайдера.
    """

    name = "base"

    def search(
        self,
        search_request,
    ):
        raise NotImplementedError


# =========================================================
# EMPTY PROVIDER
# =========================================================

class EmptyProvider(
    SearchProvider
):
    """
    Пустой провайдер для тестов.
    """

    name = "none"

    def search(
        self,
        search_request,
    ):
        return []


# =========================================================
# SOURCE RANKING
# =========================================================

def get_source_score(
    result,
    search_request,
):
    """
    Вычисляет полезность источника
    для конкретного кейса клиента.
    """

    url = str(
        result.get(
            "url",
            "",
        )
    ).lower()

    domain = str(
        result.get(
            "domain",
            "",
        )
    ).lower()

    text = (
        f"{result.get('name', '')} "
        f"{result.get('description', '')}"
    ).lower()

    rental_type = get_rental_type(
        search_request
    )

    score = 0

    # -----------------------------------------------------
    # КОРОТКАЯ АРЕНДА
    # -----------------------------------------------------

    if rental_type == "short":
        priorities = {
            "booking.com": 50,
            "airbnb.": 48,
            "ostrovok.ru": 45,
            "tripadvisor.": 28,
            "avito.ru": 24,
            "fazwaz.": 22,
            "thailand-property.com": 20,
            "propertyscout.co.th": 20,
        }

    # -----------------------------------------------------
    # АРЕНДА 1-3 МЕСЯЦА
    # -----------------------------------------------------

    elif rental_type == "medium":
        priorities = {
            "fazwaz.": 50,
            "thailand-property.com": 47,
            "propertyscout.co.th": 46,
            "avito.ru": 42,
            "airbnb.": 38,
            "booking.com": 35,
            "ostrovok.ru": 32,
            "livephuket.com": 40,
            "holycowphuket.ru": 38,
        }

    # -----------------------------------------------------
    # ДОЛГОСРОЧНАЯ АРЕНДА
    # -----------------------------------------------------

    elif rental_type == "long":
        priorities = {
            "fazwaz.": 55,
            "thailand-property.com": 52,
            "propertyscout.co.th": 52,
            "livephuket.com": 48,
            "avito.ru": 46,
            "holycowphuket.ru": 45,
            "airbnb.": 30,
            "booking.com": 24,
            "ostrovok.ru": 22,
        }

    # -----------------------------------------------------
    # ЕСЛИ ДЛИТЕЛЬНОСТЬ НЕ УДАЛОСЬ ОПРЕДЕЛИТЬ
    # -----------------------------------------------------

    else:
        priorities = {
            "booking.com": 40,
            "airbnb.": 40,
            "ostrovok.ru": 38,
            "fazwaz.": 40,
            "thailand-property.com": 38,
            "propertyscout.co.th": 38,
            "avito.ru": 35,
            "livephuket.com": 35,
        }

    source_text = (
        domain
        + " "
        + url
    )

    for source_name, value in (
        priorities.items()
    ):
        if source_name in source_text:
            score += value
            break

    # -----------------------------------------------------
    # PET FRIENDLY
    # -----------------------------------------------------

    if search_request.get(
        "has_pet"
    ):
        pet_words = (
            "pet friendly",
            "pets allowed",
            "pet-friendly",
            "с животными",
            "животными",
            "питомец",
            "питомцами",
            "pets",
        )

        if any(
            word in text
            for word in pet_words
        ):
            score += 20

    # -----------------------------------------------------
    # ТИП ЖИЛЬЯ
    # -----------------------------------------------------

    housing_type = search_request.get(
        "housing_type"
    )

    if housing_type:
        housing_text = str(
            housing_type
        ).lower()

        if housing_text in text:
            score += 8

    # -----------------------------------------------------
    # НАЛИЧИЕ ЦЕНЫ
    # -----------------------------------------------------

    if result.get(
        "price"
    ) is not None:
        score += 4

    # -----------------------------------------------------
    # НАЛИЧИЕ ОПИСАНИЯ
    # -----------------------------------------------------

    if result.get(
        "description"
    ):
        score += 2

    # -----------------------------------------------------
    # НАЛИЧИЕ ПРЯМОЙ ССЫЛКИ
    # -----------------------------------------------------

    if result.get(
        "url"
    ):
        score += 2

    return score


# =========================================================
# SEARCH ENGINE
# =========================================================

class HousingSearchEngine:
    """
    Главный движок поиска жилья.

    1. Получает поисковый запрос.
    2. Передаёт его провайдерам.
    3. Нормализует результаты.
    4. Убирает дубли.
    5. Ранжирует под конкретного клиента.
    """

    def __init__(
        self,
        providers=None,
    ):
        if providers is None:
            providers = [
                EmptyProvider()
            ]

        self.providers = providers

    def search(
        self,
        search_request,
    ):
        return self.search_with_status(search_request)["results"]

    def search_with_status(
        self,
        search_request,
    ):
        all_results = []
        provider_errors = []
        successful_providers = 0
        excluded_urls = {
            normalize_result_identifier(canonicalize_known_property_url(url))
            for url in search_request.get("excluded_urls", [])
            if url
        }

        for provider in self.providers:
            try:
                results = provider.search(
                    search_request
                )
                successful_providers += 1

                if not results:
                    continue

                for result in results:
                    normalized = normalize_result(
                        result,
                        provider.name,
                    )

                    if normalized:
                        if not result_has_phuket_geo_evidence(
                            normalized,
                            search_request.get("location"),
                        ):
                            continue
                        identifier = normalize_result_identifier(
                            normalized.get("url")
                        )
                        if identifier and identifier in excluded_urls:
                            continue
                        all_results.append(
                            normalized
                        )

            except Exception as e:
                provider_errors.append(provider.name)
                print(
                    "[SEARCH] Ошибка источника "
                    f"{provider.name}: {e}"
                )

        all_results = self.remove_duplicates(
            all_results
        )

        all_results = self.sort_results(
            all_results,
            search_request,
        )

        if all_results:
            status = SEARCH_WITH_RESULTS
        elif successful_providers:
            status = SEARCH_NO_RESULTS
        else:
            status = SEARCH_PROVIDER_ERROR

        return {
            "status": status,
            "results": all_results,
            "provider_errors": provider_errors,
        }

    @staticmethod
    def remove_duplicates(
        results,
    ):
        """
        Убирает дубли в первую очередь по URL.
        """

        unique = []
        seen = set()

        for result in results:
            url = str(
                result.get(
                    "url",
                    "",
                )
            ).strip().lower()

            if url:
                key = (
                    "url",
                    url,
                )
            else:
                key = (
                    "fallback",
                    str(
                        result.get(
                            "name",
                            "",
                        )
                    ).strip().lower(),
                    str(
                        result.get(
                            "address",
                            "",
                        )
                    ).strip().lower(),
                )

            if key in seen:
                continue

            seen.add(
                key
            )

            unique.append(
                result
            )

        return unique

    @staticmethod
    def sort_results(
        results,
        search_request,
    ):
        """
        Ранжирует результаты под конкретный кейс.
        """

        for item in results:
            item["search_score"] = (
                get_source_score(
                    item,
                    search_request,
                )
            )

        def sort_key(item):
            score = item.get(
                "search_score",
                0,
            )

            rating = item.get(
                "rating"
            )

            try:
                rating = float(
                    rating
                )
            except (
                TypeError,
                ValueError,
            ):
                rating = 0

            return (
                1 if item.get("result_type") == CONCRETE_PROPERTY else 0,
                score,
                rating,
            )

        return sorted(
            results,
            key=sort_key,
            reverse=True,
        )


# =========================================================
# PUBLIC SEARCH FUNCTION
# =========================================================

def search_housing(
    case,
    providers=None,
    excluded_urls=None,
    page=0,
    repeat_search=False,
    result_limit=5,
):
    """
    Главная публичная функция поиска жилья.
    Именно её вызывает bot.py.
    """

    validation = validate_case(
        case
    )

    if not validation[
        "ready"
    ]:
        return {
            "success": False,
            "status": "invalid_request",

            "message": validation[
                "reason"
            ],

            "missing_data": validation.get(
                "missing_data",
                [],
            ),

            "request": None,

            "results": [],
        }

    search_request = build_search_request(
        case
    )
    search_request.update(
        {
            "excluded_urls": list(excluded_urls or []),
            "page": int(page),
            "repeat_search": bool(repeat_search),
            "result_limit": max(1, min(int(result_limit), 10)),
        }
    )

    print(
        "\n===== HOUSING SEARCH REQUEST ====="
    )

    print(
        search_request
    )

    print(
        "===================================\n"
    )

    if providers is None:
        from yandex_provider import YandexSearchProvider

        try:
            providers = [YandexSearchProvider()]
        except Exception as e:
            print(f"[SEARCH] Ошибка инициализации источника: {e}")
            return {
                "success": False,
                "status": SEARCH_PROVIDER_ERROR,
                "message": "Поиск временно недоступен из-за ошибки источника.",
                "request": search_request,
                "results": [],
            }

    engine = HousingSearchEngine(providers=providers)

    execution = engine.search_with_status(
        search_request
    )
    results = execution["results"]
    results = results[:search_request["result_limit"]]
    status = execution["status"]

    if status == SEARCH_WITH_RESULTS:
        message = (
            "Найдено вариантов: "
            f"{len(results)}"
        )

    elif status == SEARCH_NO_RESULTS:
        message = (
            "Поисковый запрос сформирован. "
            "Подходящих предложений пока нет."
        )
    else:
        message = (
            "Поиск временно недоступен из-за ошибки источника."
        )

    return {
        "success": status != SEARCH_PROVIDER_ERROR,

        "status": status,

        "message": message,

        "request": search_request,

        "results": results,
    }
