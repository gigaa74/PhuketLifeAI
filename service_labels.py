from service_catalog import CATEGORY_LABELS_RU


def category_label_ru(value):
    return CATEGORY_LABELS_RU.get(value, str(value))
