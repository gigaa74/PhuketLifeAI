from async_utils import run_blocking
from case_engine import get_repeat_search_options, record_search_results
from search_engine import SEARCH_PROVIDER_ERROR, search_housing


MISSING_FIELD_LABELS = {
    "arrival_date": "дату заезда",
    "departure_date": "дату выезда",
    "people": "количество гостей",
    "budget": "бюджет",
}


def build_housing_missing_question(missing_fields):
    labels = [
        MISSING_FIELD_LABELS[field]
        for field in missing_fields
        if field in MISSING_FIELD_LABELS
    ]
    if not labels:
        return "Уточните, пожалуйста, недостающие параметры поиска жилья."
    if len(labels) == 1:
        fields_text = labels[0]
    else:
        fields_text = ", ".join(labels[:-1]) + " и " + labels[-1]
    return f"Уточните, пожалуйста, {fields_text}."


async def execute_housing_search(
    case_data,
    repeat_search=False,
    search_callable=None,
    requested_result_limit=5,
):
    """Execute exactly one verified search and persistable search state."""
    search_callable = search_callable or search_housing
    requested_result_limit = max(1, min(int(requested_result_limit), 10))
    options = get_repeat_search_options(
        case_data,
        repeat_search=repeat_search,
    )
    result = await run_blocking(
        search_callable,
        {"category": "housing", "data": case_data},
        None,
        options["excluded_urls"],
        options["page"],
        repeat_search,
        requested_result_limit,
    )

    if result.get("status") == SEARCH_PROVIDER_ERROR:
        return result, case_data, "ready_for_search"

    shown_results = result.get("results", [])[:requested_result_limit]
    updated_data = record_search_results(
        case_data,
        shown_results,
        result.get("request", {}).get("page", options["page"]),
        options["fingerprint"],
    )
    return result, updated_data, "results_presented"
