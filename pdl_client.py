# pdl_client.py
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

PDL_PERSON_ENDPOINT = "https://api.peopledatalabs.com/v5/person/enrich"

class PDLClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def person_enrich(self, params: dict) -> dict:
        """
        params: { ... } for a single person enrichment.
        Returns parsed JSON.
        """
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        resp = self._client.get(PDL_PERSON_ENDPOINT, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


