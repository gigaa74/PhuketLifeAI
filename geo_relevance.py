import re
from urllib.parse import unquote, urlparse, urlunparse


PHUKET_LOCATION_ALIASES = {
    "phuket": ("phuket", "пхукет", "mueang phuket", "muang phuket"),
    "rawai": ("rawai", "равай", "ราไวย์"),
    "kata": ("kata", "ката", "กะตะ"),
    "karon": ("karon", "карон", "กะรน"),
    "patong": ("patong", "патонг", "ป่าตอง"),
    "kamala": ("kamala", "камала", "กมลา"),
    "bang_tao": ("bang tao", "bangtao", "банг тао", "บางเทา"),
    "nai_harn": ("nai harn", "naiharn", "най харн", "ในหาน"),
    "chalong": ("chalong", "чалонг", "ฉลอง"),
}

CONFLICTING_GEO_MARKERS = (
    "atlanta", "атланта", "bangkok", "бангкок", "pattaya", "паттайя",
    "chiang mai", "чиангмай", "krabi", "краби", "koh samui", "самуи",
)


def _normalized_geo_text(value):
    text = unquote(str(value or "")).casefold()
    text = re.sub(r"[_/|,+-]+", " ", text)
    return " ".join(text.split())


def _contains_alias(text, alias):
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) is not None


def normalize_phuket_location(value):
    text = _normalized_geo_text(value)
    for canonical, aliases in PHUKET_LOCATION_ALIASES.items():
        if any(_contains_alias(text, alias) for alias in aliases):
            return canonical
    return None


def canonicalize_known_property_url(url):
    raw = str(url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    host = (parsed.hostname or "").casefold()
    if (
        (host == "airbnb.com" or host.endswith(".airbnb.com"))
        and re.fullmatch(r"/rooms/\d+/?", parsed.path or "")
    ):
        return urlunparse((
            parsed.scheme or "https", "www.airbnb.com", parsed.path.rstrip("/"),
            parsed.params, parsed.query, "",
        ))
    return raw


def result_has_phuket_geo_evidence(result, target_location=None):
    evidence = " ".join(
        _normalized_geo_text(result.get(field))
        for field in (
            "title", "name", "snippet", "description", "location_text",
            "location", "address", "url",
        )
    )
    if any(_contains_alias(evidence, marker) for marker in CONFLICTING_GEO_MARKERS):
        return False
    target = normalize_phuket_location(target_location)
    accepted = {
        alias
        for aliases in PHUKET_LOCATION_ALIASES.values()
        for alias in aliases
    }
    if target and target != "phuket":
        accepted.update(PHUKET_LOCATION_ALIASES[target])
    return any(_contains_alias(evidence, alias) for alias in accepted)
