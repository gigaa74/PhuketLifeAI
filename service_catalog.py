"""Canonical, extensible Phuket Life service catalogue.

Internal keys stay stable; Russian labels and deterministic patterns are presentation
and intake concerns. Unknown services remain unknown and must never fall back to housing.
"""

import re

SERVICE_DEFINITIONS = {
    "housing": ("жильё", r"\b(?:квартир\w*|апартамент\w*|вилл\w*|жиль[её]|дом(?!работ|охозяй)\w*|таунхаус\w*|кондо\w*)\b"),
    "property_purchase": ("покупка недвижимости", r"\b(?:купить|покупк\w*|продаж\w*)\s+(?:кондо|квартир\w*|вилл\w*|дом\w*|недвижимост\w*)\b"),
    "property_management": ("управление недвижимостью", r"\b(?:управлени\w+ (?:недвижимост\w*|объект\w*)|property management|управляющ\w+ компани\w*)\b"),
    "car_rental": ("аренда автомобилей", r"\b(?:автомобил\w*|машин\w*|авто\b|автопрокат\w*|rent(?:al)? car)\b"),
    "bike_rental": ("аренда байков", r"\b(?:байк\w*|мотоцикл\w*|скутер\w*|мотопрокат\w*|bike rental)\b"),
    "transfer": ("трансфер", r"\b(?:трансфер\w*|такси\b|встреч\w+ (?:в|из) аэропорт\w*|доставить (?:из|в) аэропорт\w*)\b"),
    "personal_driver": ("личный водитель", r"\b(?:личн\w+|персональн\w+|семейн\w+) водител\w*|\bводител\w+ (?:на день|на неделю|на месяц|с машин\w*|в семью)\b"),
    "excursions": ("экскурсии", r"\b(?:экскурси\w*|экскурсовод\w*|тур\w+ по|обзорн\w+ тур\w*)\b"),
    "boats": ("лодки и яхты", r"\b(?:лодк\w*|яхт\w*|катер\w*|судн\w*|корабл\w*|спидбот\w*|speed\s*boats?)\b"),
    "fishing": ("рыбалка", r"\b(?:рыбалк\w*|рыболов\w*|фишинг\w*)\b"),
    "diving": ("дайвинг", r"\b(?:дайвинг\w*|дайв\w*|погружени\w+ с акваланг\w*|scuba)\b"),
    "water_sports": ("водные развлечения", r"\b(?:серфинг\w*|сап\w*|sup\b|вейкборд\w*|кайтсерфинг\w*|парасейлинг\w*|гидроцикл\w*)\b"),
    "activities": ("активности и развлечения", r"\b(?:активност\w*|развлечени\w*|аквапарк\w*|зоопарк\w*|шоу\b|аттракцион\w*)\b"),
    "guide": ("услуги гида", r"\b(?:гид\w*|сопровождающ\w+|экскурсовод\w*)\b"),
    "cleaning": ("разовая уборка", r"\b(?:уборк\w*|клининг\w*|убрать (?:дом|квартир\w*|вилл\w*)|уборщиц\w*)\b"),
    "housekeeping": ("домработница / помощница по дому", r"\b(?:домработниц\w*|домохозяйк\w*|горничн\w*|помощниц\w+ по дом\w*|housekeep\w*)\b"),
    "nanny": ("няня", r"\b(?:нян\w*|бебиситтер\w*|babysitter\w*|присмотр\w+ за (?:реб[её]нк\w*|детьми))\b"),
    "personal_assistant": ("личный помощник", r"\b(?:личн\w+|персональн\w+) (?:помощник\w*|ассистент\w*)\b"),
    "private_chef": ("личный повар", r"\b(?:личн\w+|частн\w+|домашн\w+) повар\w*|\bповар\w+ (?:на дом|в семью)\b"),
    "repair": ("ремонт и мастер на дом", r"\b(?:ремонт\w*|мастер\w+ на (?:дом|час)|муж\w+ на час|починить|отремонтировать)\b"),
    "electrician": ("электрик", r"\b(?:электрик\w*|электромонтаж\w*|проводк\w*|розетк\w*|коротк\w+ замыкани\w*)\b"),
    "plumber": ("сантехник", r"\b(?:сантехник\w*|сантехник\w+ работ\w*|прорвало труб\w*|теч[её]т кран\w*|засор\w*)\b"),
    "aircon_service": ("ремонт и обслуживание кондиционеров", r"\b(?:кондиционер\w+ (?:не работает|сломал|ремонт|чистк|обслужив)|ремонт\w+ кондиционер\w*|чистк\w+ кондиционер\w*)\b"),
    "pool_garden": ("обслуживание бассейна и сада", r"\b(?:чистк\w+ бассейн\w*|обслуживани\w+ бассейн\w*|садовник\w*|уход\w+ за сад\w*|ландшафт\w*)\b"),
    "fitness_trainer": ("персональный тренер", r"\b(?:персональн\w+ тренер\w*|фитнес[- ]?тренер\w*|тренер\w*(?:\s+(?:по|для|в зал))?|тренировк\w+ (?:по|с тренер)|коуч\w+ по (?:фитнес|плаван|бокс))\b"),
    "wellness": ("wellness и йога", r"\b(?:wellness|велнес\w*|спа\b|йог\w*|медитаци\w*|ретрит\w*)\b"),
    "massage": ("массаж", r"\b(?:массаж\w*|массажист\w*|массажистк\w*)\b"),
    "beauty": ("красота и уход", r"\b(?:салон\w+ красот\w*|парикмахер\w*|барбер\w*|маникюр\w*|педикюр\w*|визажист\w*|косметолог\w*)\b"),
    "medical": ("медицина", r"\b(?:врач\w*|клиник\w*|медицин\w*|доктор\w*|госпитал\w*|больниц\w*)\b"),
    "dental": ("стоматология", r"\b(?:стоматолог\w*|дантист\w*|зубн\w+ врач\w*|лечени\w+ зуб\w*)\b"),
    "insurance": ("страхование", r"\b(?:страховк\w*|страховани\w*|insurance)\b"),
    "pets": ("услуги для животных", r"\b(?:ветеринар\w*|ветклиник\w*|груминг\w*|грумер\w*|передержк\w*|зоогостиниц\w*|догситтер\w*|petsitter\w*)\b"),
    "food": ("рестораны и питание", r"\b(?:ресторан\w*|кафе\b|доставк\w+ еды|еда на заказ)\b"),
    "catering": ("кейтеринг", r"\b(?:кейтеринг\w*|catering|банкет\w*|фуршет\w*)\b"),
    "delivery": ("доставка и курьер", r"\b(?:курьер\w*|доставк\w*|привезти|забрать посылк\w*)\b"),
    "shopping": ("покупки и поиск товаров", r"\b(?:найти|купить|достать) (?:товар\w*|лекарств\w*|продукт\w*|техник\w*)|\bшопинг\w*\b"),
    "tutoring": ("репетитор и обучение", r"\b(?:репетитор\w*|учител\w*|урок\w+ (?:английск|русск|тайск|математик|музык)|обучени\w+|языков\w+ школ\w*)\b"),
    "translation": ("переводчик", r"\b(?:переводчик\w*|устн\w+ перевод\w*|письменн\w+ перевод\w*|перевести документ\w*)\b"),
    "photo_video": ("фото и видео", r"\b(?:фотограф\w*|видеограф\w*|фотосесси\w*|видеосъ[её]мк\w*|съ[её]мк\w+ свадьб\w*)\b"),
    "events": ("мероприятия и праздники", r"\b(?:организаци\w+ (?:свадьб\w*|праздник\w*|мероприяти\w*)|ведущ\w+ на|дидже\w*|аниматор\w*|декоратор\w*)\b"),
    "visa": ("визы и иммиграционные услуги", r"\b(?:виз\w*|иммиграц\w*|продлени\w+ пребыван\w*|визаран\w*)\b"),
    "legal": ("юридические услуги", r"\b(?:юрист\w*|адвокат\w*|нотариус\w*|юридическ\w+ (?:помощ|консультац|услуг))\b"),
    "accounting_business": ("бухгалтерия и бизнес-услуги", r"\b(?:бухгалтер\w*|бухуч[её]т\w*|регистраци\w+ компани\w*|открыть компани\w*|налогов\w+ консультац\w*)\b"),
    "relocation": ("переезд и релокация", r"\b(?:relocation|релокаци\w*|переезд\w*|адаптаци\w+ на пхукет\w*)\b"),
    "sim": ("SIM-карты и связь", r"\b(?:sim[- ]?карт\w*|сим[- ]?карт\w*|мобильн\w+ интернет\w*|подключить интернет\w*)\b"),
    "security": ("охрана и безопасность", r"\b(?:охранник\w*|телохранител\w*|личн\w+ охран\w*|охрана\w+ (?:дом|вилл|мероприяти)|систем\w+ безопасност\w*)\b"),
}

CATEGORY_PATTERNS = {key: value[1] for key, value in SERVICE_DEFINITIONS.items()}
CATEGORY_LABELS_RU = {key: value[0] for key, value in SERVICE_DEFINITIONS.items()}

# Legacy keys remain readable/valid for existing production data.
CATEGORY_LABELS_RU.update({
    "legal_visa": "юридические и визовые услуги",
    "other": "другое",
})
SERVICE_CATEGORIES = set(CATEGORY_LABELS_RU)
SERVICE_CATEGORY_ADMIN_RU = {
    key: label[:1].upper() + label[1:] for key, label in CATEGORY_LABELS_RU.items()
}


def detect_service_categories(text):
    """Return ordered categories and suppress obvious contextual false positives."""
    value = " ".join(str(text or "").split())
    categories = [
        key for key, pattern in CATEGORY_PATTERNS.items()
        if re.search(pattern, value, re.I)
    ]
    specific_property = {"property_purchase", "property_management"}
    non_housing_service = set(categories) - {"housing"} - specific_property
    housing_is_location_context = bool(re.search(
        r"\b(?:на|в)\s+(?:вилл\w*|дом\w*|квартир\w*|апартамент\w*)\b",
        value, re.I,
    ))
    if "housing" in categories and (
        specific_property.intersection(categories)
        or (non_housing_service and housing_is_location_context)
    ):
        categories.remove("housing")
    if "medical" in categories and "dental" in categories:
        categories.remove("medical")
    if "car_rental" in categories:
        appliance_only = bool(re.search(
            r"\b(?:стиральн\w*|посудомоечн\w*)\s+машин\w*\b", value, re.I
        )) and not re.search(
            r"\b(?:автомобил\w*|авто\b|автопрокат\w*|аренд\w+ машин\w*|"
            r"ищ\w+ машин\w*|нужн\w+ машин\w*)\b", value, re.I
        )
        driver_context = "personal_driver" in categories and not re.search(
            r"\b(?:аренд\w*|прокат\w*)\b", value, re.I
        )
        if appliance_only or driver_context:
            categories.remove("car_rental")
    return categories
