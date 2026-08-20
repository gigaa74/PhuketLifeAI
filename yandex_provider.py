import os
import re
import base64
import requests
import xml.etree.ElementTree as ET

from dotenv import load_dotenv


load_dotenv()


class YandexSearchProvider:
    name = "yandex"

    def __init__(self):
        self.api_key = os.getenv("YANDEX_SEARCH_API_KEY")
        self.folder_id = os.getenv("YANDEX_FOLDER_ID")

        if not self.api_key:
            raise ValueError("YANDEX_SEARCH_API_KEY не найден в .env")

        if not self.folder_id:
            raise ValueError("YANDEX_FOLDER_ID не найден в .env")

        self.url = "https://searchapi.api.cloud.yandex.net/v2/web/search"

    def search(self, search_request):
        query_text = self._build_query(search_request)

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "query": {
                "searchType": "SEARCH_TYPE_COM",
                "queryText": query_text,
            },
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
        }

        response = requests.post(
            self.url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        raw_data = data.get("rawData")

        if not raw_data:
            return []

        xml_text = base64.b64decode(raw_data).decode(
            "utf-8",
            errors="replace",
        )

        return self._parse_xml(
            xml_text,
            limit=10,
        )

    def _build_query(self, search_request):
        parts = [
            "Phuket Thailand",
            "apartment condo house rental",
        ]

        location = search_request.get("location")

        if location:
            parts.append(
                str(location)
            )

        housing_type = search_request.get(
            "housing_type"
        )

        if housing_type:
            parts.append(
                str(housing_type)
            )

        budget = search_request.get(
            "budget_original"
        )

        if budget:
            parts.append(
                str(budget)
            )

        if search_request.get(
            "has_pet"
        ):
            parts.append(
                "pet friendly"
            )

        return " ".join(parts)

    @staticmethod
    def _extract_price(text):
        if not text:
            return None, ""

        patterns = [
            (
                r"฿\s*([\d\s,]+)",
                "THB",
            ),
            (
                r"([\d\s,]+)\s*(?:บาท|THB)",
                "THB",
            ),
            (
                r"₽\s*([\d\s,]+)",
                "RUB",
            ),
            (
                r"([\d\s,]+)\s*(?:руб(?:лей|ля|ль)?|RUB)",
                "RUB",
            ),
            (
                r"\$\s*([\d\s,]+)",
                "USD",
            ),
            (
                r"([\d\s,]+)\s*USD",
                "USD",
            ),
        ]

        for pattern, currency in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            number_text = (
                match.group(1)
            )

            number_text = re.sub(
                r"[^\d]",
                "",
                number_text,
            )

            if not number_text:
                continue

            try:
                price = int(
                    number_text
                )
            except ValueError:
                continue

            if price <= 0:
                continue

            return price, currency

        return None, ""

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

            price, currency = (
                self._extract_price(
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