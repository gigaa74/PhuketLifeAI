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

    if concrete and not listing_pages:
        header = (
            "Вот ещё новые варианты:"
            if repeat_search
            else "Нашёл первые варианты жилья:"
        )
    elif listing_pages and not concrete:
        header = (
            "Вот ещё страницы с предложениями:"
            if repeat_search
            else "Нашли источники с предложениями жилья по вашим параметрам:"
        )
    else:
        header = (
            "Вот ещё новые результаты поиска:"
            if repeat_search
            else "Нашли варианты и страницы с предложениями жилья:"
        )

    lines = [header + "\n"]
    for index, item in enumerate(results, start=1):
        name = item.get("name", "Результат поиска")
        description = item.get("description", "")
        if len(description) > 250:
            description = description[:250] + "..."
        url = item.get("url", "")
        lines.append(f"{index}. {name}\n{description}\n{url}\n")
    return "\n".join(lines)
