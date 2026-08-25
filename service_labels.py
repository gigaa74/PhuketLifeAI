CATEGORY_LABELS_RU = {
    "housing": "жильё",
    "car_rental": "аренда автомобилей",
    "bike_rental": "аренда байков",
    "transfer": "трансфер",
    "excursions": "экскурсии",
    "boats": "лодки и яхты",
    "fishing": "рыбалка",
    "food": "рестораны и питание",
    "wellness": "wellness и массаж",
    "medical": "медицина",
    "legal_visa": "юридические и визовые услуги",
    "relocation": "переезд и релокация",
}


def category_label_ru(value):
    return CATEGORY_LABELS_RU.get(value, str(value))
