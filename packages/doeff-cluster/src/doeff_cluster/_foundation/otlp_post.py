"""OTLP/HTTP の本文 1 つを送り先へ POST する生の送り(生の http に触ってよいのは foundation の層 — DOEFF106)。

使い手は job の log の送り手(worker/entry/line_stamp の JobLogShipper — 書き手の thread とは別の送りの thread)。shim と、待ちの子から
分かれた子 A の中で動くので、標準ライブラリだけを import する。送り先 1 つに接続を 1 本張って使い回し、失敗したら閉じて、次の送りで
張り直す。送りは待ちの上限(秒)の内で終わり、失敗は例外でなく答えで返す(送り手は数えて捨てる — job の write を止めない)。
"""

import http.client
from urllib.parse import urlsplit

# 層 foundation の文脈と役の名乗り(DOEFF104)— 隣の process_environ.py と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "foundation"}


def endpoint_usable(endpoint: str) -> bool:
    """送り先の URL が、この送りで POST できる形(http か https・host の在る絶対の URL)か — 送り手を作る前に、使えない値を断るため。"""
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and (port is None or port > 0)


class OtlpPoster:
    """送り先 1 つへ JSON の本文を POST する口(接続を使い回す)。使うのは送りの thread 1 本だけ。"""

    def __init__(self, endpoint: str) -> None:
        """送り先の URL(endpoint_usable の答えが真の物)を、接続の作り方と POST の path に分けて持つため(接続はまだ張らない)。"""
        parts = urlsplit(endpoint)
        self.secure = parts.scheme == "https"
        self.host = parts.hostname or ""
        self.port = parts.port
        self.path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self._mut_connection: http.client.HTTPConnection | None = None

    def connection(self, timeout: float) -> http.client.HTTPConnection:
        """今の接続(無ければ張る前の新しい接続)を、この送りの待ちの上限で使える形にして返すため。"""
        current = self._mut_connection
        if current is None:
            kind = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
            current = kind(self.host, self.port, timeout=timeout)
            self._mut_connection = current
        current.timeout = timeout
        if current.sock is not None:
            current.sock.settimeout(timeout)
        return current

    def posted(self, body: bytes, timeout: float) -> bool:
        """本文 1 つを POST するため。答え = 送り先が 2xx で受けたか(つながらない・待ちの上限を越えた・2xx でない時は偽 — 接続の
        失敗なら接続を閉じ、次の送りで張り直す)。"""
        try:
            current = self.connection(timeout)
            current.request("POST", self.path, body=body, headers={"Content-Type": "application/json"})
            response = current.getresponse()
            response.read()
        except (OSError, http.client.HTTPException):
            self.close()
            return False
        return 200 <= response.status < 300

    def close(self) -> None:
        """接続を閉じるため(送りの終わりと、失敗した送りの後)。"""
        current = self._mut_connection
        self._mut_connection = None
        if current is not None:
            current.close()
