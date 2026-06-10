# doeff: workflow
import httpx


def fetch_status(url: str) -> int:
    return httpx.get(url).status_code
