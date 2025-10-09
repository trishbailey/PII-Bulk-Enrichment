# pdl_client.py
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

# CHANGE: Switched to the Sandbox API URL for free testing.
# This URL does not use production credits and returns synthetic data.
PDL_BULK_ENDPOINT = "https://sandbox.api.peopledatalabs.com/v5/bulk/person" 

class PDLClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def bulk_enrich(self, requests_payload: list[dict]) -> dict:
        """
        requests_payload: [{"params": {...}}, ...] up to 100 per call.
        Returns parsed JSON.
        """
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        resp = self._client.post(PDL_BULK_ENDPOINT, headers=headers, json={"requests": requests_payload})
        # PDL returns JSON with per-item status codes inside; HTTP may still be 200 overall.
        resp.raise_for_status()
        return resp.json()

# REMINDER: To revert to production (paid) mode, change the endpoint back to:
# PDL_BULK_ENDPOINT = "https://api.peopledatalabs.com/v5/person/bulk"
