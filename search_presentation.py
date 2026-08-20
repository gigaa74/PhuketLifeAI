CONCRETE_PROPERTY = "concrete_property_result"
LISTING_PAGE = "listing_search_category_page"


def build_pre_search_message(case_data, repeat_search, confirmation_builder):
    if repeat_search:
        return None
    return confirmation_builder(case_data)


def build_results_message(results, repeat_search=False):
    concrete = [
        item for item in results
        if item.get("result_type") == CONCRETE_PROPERTY
    ]
    listing_pages = [
        item for item in results
        if item.get("result_type") != CONCRETE_PROPERTY
    ]

    lines = []
    if concrete:
        header = "Вот ещё новые варианты:" if repeat_search else "Нашёл первые варианты жилья:"
        lines.append(header + "\n")
        lines.extend(_format_results(concrete))
        lines.append(
            "Найдено в поисковом источнике — актуальность цены и "
            "доступность нужно проверить перед бронированием.\n"
        )
    if listing_pages:
        if concrete:
            header = (
                "Вот ещё страницы с дополнительными предложениями:"
                if repeat_search
                else "Источники с дополнительными предложениями жилья:"
            )
        else:
            header = (
                "Вот ещё страницы с предложениями:"
                if repeat_search
                else "Нашли источники с предложениями жилья по вашим параметрам:"
            )
        lines.append(header + "\n")
        lines.extend(_format_results(listing_pages))
    return "\n".join(lines)


def _format_results(results):
    lines = []
    for index, item in enumerate(results, start=1):
        name = item.get("title") or item.get("name", "Результат поиска")
        description = item.get("snippet") or item.get("description", "")
        if len(description) > 250:
            description = description[:250] + "..."
        url = item.get("url", "")
        price_text = item.get("price_text", "")
        price_line = f"\nЦена в источнике: {price_text}" if price_text else ""
        lines.append(f"{index}. {name}\n{description}{price_line}\n{url}\n")
    return lines
