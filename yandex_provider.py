import re
import base64
import requests
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
from config import load_settings
from reliability import retry_call


load_dotenv()


class YandexSearchProvider:
    name = "yandex"

    def __init__(self):
        settings = load_settings()
        self.api_key = settings.yandex_search_api_key
        self.folder_id = settings.yandex_folder_id
        self.retry_attempts = settings.external_retry_attempts
        self.retry_base_delay_seconds = settings.external_retry_base_delay_seconds

        self.url = "https://searchapi.api.cloud.yandex.net/v2/web/search"

    def search(self, search_request):
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }

        result_limit = max(1, min(int(search_request.get("result_limit", 5)), 10))
        discovery_limit = max(result_limit, 5)
        results = []
        seen_urls = set()
        successful_queries = 0
        first_error = None

        for query_text in self._build_queries(search_request):
            payload = {
                "query": {
                    "searchType": "SEARCH_TYPE_COM",
                    "queryText": query_text,
                    "page": str(search_request.get("page", 0)),
                },
                "folderId": self.folder_id,
                "responseFormat": "FORMAT_XML",
                "groupSpec": {
                    "groupMode": "GROUP_MODE_FLAT",
                    "groupsOnPage": str(discovery_limit),
                    "docsInGroup": "1",
                },
            }
            try:
                def request_search():
                    response = requests.post(
                        self.url, headers=headers, json=payload, timeout=30
                    )
                    response.raise_for_status()
                    return response

                response = retry_call(
                    request_search,
                    attempts=self.retry_attempts,
                    base_delay_seconds=self.retry_base_delay_seconds,
                )
                raw_data = response.json().get("rawData")
                successful_queries += 1
            except requests.RequestException as error:
                if first_error is None:
                    first_error = error
                continue
            if not raw_data:
                continue
            xml_text = base64.b64decode(raw_data).decode(
                "utf-8", errors="replace"
            )
            for item in self._parse_xml(xml_text, limit=discovery_limit):
                identifier = item.get("url", "").strip().lower().rstrip("/")
                if identifier and identifier not in seen_urls:
                    seen_urls.add(identifier)
                    results.append(item)

        if not successful_queries and first_error is not None:
            raise first_error
        return results

    def _build_queries(self, search_request):
        from search_engine import build_concrete_search_queries

        return build_concrete_search_queries(search_request)

    def _build_query(self, search_request):
        return self._build_queries(search_request)[0]

    @classmethod
    def _extract_price_data(cls, text):
        if not text:
            return None, "", ""

        for pattern, currency in cls._price_patterns():
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            number_text = re.sub(r"[^\d]", "", match.group(1))
            if not number_text:
                continue
            price = int(number_text)
            if price > 0:
                return price, currency, match.group(0).strip()
        return None, "", ""

    @staticmethod
    def _price_patterns():
        return [
            (r"฿\s*([\d\s,]+)", "THB"),
            (r"([\d\s,]+)\s*(?:บาท|THB)", "THB"),
            (r"₽\s*([\d\s,]+)", "RUB"),
            (r"([\d\s,]+)\s*(?:руб(?:лей|ля|ль)?|RUB)", "RUB"),
            (r"\$\s*([\d\s,]+)", "USD"),
            (r"([\d\s,]+)\s*USD", "USD"),
        ]

    @staticmethod
    def _extract_price(text):
        price, currency, _ = YandexSearchProvider._extract_price_data(text)
        return price, currency

    def _parse_xml(
        self,
        xml_text,
        limit=10,
    ):
        root = ET.fromstring(
            xml_text
        )

        results = []

        for doc in root.findall(
            ".//doc"
        ):
            title_node = doc.find(
                "title"
            )

            url_node = doc.find(
                "url"
            )

            domain_node = doc.find(
                "domain"
            )

            if (
                url_node is None
                or not url_node.text
            ):
                continue

            title = self._node_text(
                title_node
            )

            url = (
                url_node.text.strip()
            )

            domain = ""

            if (
                domain_node is not None
                and domain_node.text
            ):
                domain = (
                    domain_node.text.strip()
                )

            passages = []

            for passage in doc.findall(
                "./passages/passage"
            ):
                passage_text = (
                    self._node_text(
                        passage
                    )
                )

                if passage_text:
                    passages.append(
                        passage_text
                    )

            snippet = " ".join(
                passages
            ).strip()

            price, currency, price_text = (
                self._extract_price_data(
                    f"{title} {snippet}"
                )
            )

            results.append(
                {
                    "name": title,
                    "property_type": "",
                    "location": "",
                    "address": "",
                    "price": price,
                    "price_text": price_text,
                    "currency": currency,
                    "price_rub": None,
                    "rating": None,
                    "reviews": None,
                    "pet_friendly": None,
                    "url": url,
                    "image": "",
                    "description": snippet,
                    "domain": domain,
                }
            )

            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _node_text(node):
        if node is None:
            return ""

        return "".join(
            node.itertext()
        ).strip()


if __name__ == "__main__":
    provider = YandexSearchProvider()

    test_request = {
        "category": "housing",
        "arrival_date": "1 сентября",
        "departure_date": "1 октября",
        "people": 2,
        "budget_rub": 50000,
        "budget_original": "до 50 000 рублей",
        "location": "Rawai",
        "pet": "",
        "has_pet": False,
        "housing_type": "апартаменты",
    }

    results = provider.search(
        test_request
    )

    print(
        f"Найдено результатов: "
        f"{len(results)}"
    )

    print()

    for index, item in enumerate(
        results,
        start=1,
    ):
        print(
            f"{index}. {item['name']}"
        )

        print(
            f"   URL: {item['url']}"
        )

        print(
            f"   Price: "
            f"{item['price']} "
            f"{item['currency']}"
        )

        print(
            f"   Description: "
            f"{item['description'][:300]}"
        )

        print()
