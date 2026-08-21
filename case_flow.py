import case_engine
from message_router import CASE_UPDATE, NEW_CASE


def persist_case_analysis(client_id, case_analysis, routing, existing_case=None):
    """Persist one extracted intent without mixing data across categories."""
    category = case_analysis.get("category", "other")
    if routing["intent"] == NEW_CASE and routing.get("category"):
        category = routing["category"]
    if routing["intent"] == CASE_UPDATE and existing_case:
        category = existing_case["category"]

    title = case_analysis.get("title", "Новый запрос")
    new_data = case_analysis.get("data", {})
    new_missing = case_analysis.get("missing_data", [])
    if not isinstance(new_data, dict):
        new_data = {}
    if not isinstance(new_missing, list):
        new_missing = []

    if (
        existing_case
        and routing["intent"] == NEW_CASE
        and (
            category != existing_case.get("category")
            or routing.get("force_new_case")
        )
    ):
        case_engine.set_case_status(existing_case["id"], "cancelled")
        existing_case = None

    old_data = existing_case.get("data", {}) if existing_case else {}
    case_data = case_engine.merge_case_data(old_data, new_data)
    missing_data = (
        case_engine.get_housing_missing_fields(case_data)
        if category == "housing"
        else new_missing
    )
    status = case_engine.get_case_status(missing_data)
    case_id = case_engine.get_or_create_case(client_id, category, title)
    case_engine.update_case(
        case_id,
        case_data,
        missing_data,
        status,
    )
    return {
        "id": case_id,
        "category": category,
        "title": title,
        "data": case_data,
        "missing_data": missing_data,
        "status": status,
    }
