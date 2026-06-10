from urllib import request


def fetch(url: str) -> bytes:
    return request.urlopen(url).read()
