# live.py
import asyncio
import json
import tempfile
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from doeff import Await, Resume, handler

from on_disk import offline, recorded
from releases import REPOS, TAGS, HttpRequest, latest_releases

served: Counter[str] = Counter()  # how many times each path was served


class Releases(BaseHTTPRequestHandler):  # a local server that answers like the GitHub API
    def do_GET(self):
        served[self.path] += 1
        repo = self.path.removeprefix("/repos/").removesuffix("/releases/latest")
        body = json.dumps({"tag_name": TAGS[repo]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the access log off stdout
        pass


def fetch(request: Request) -> str:  # the blocking part: one request over HTTP, its body as text
    with urlopen(request, timeout=10) as response:
        return response.read().decode()


@handler
def send(effect: HttpRequest, k):  # sends with urllib on a worker thread, awaited through Await
    request = Request(effect.url, method=effect.method, headers=effect.headers)
    body = yield Await(asyncio.to_thread(fetch, request))
    return (yield Resume(k, body))


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Releases)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{server.server_port}"
    with tempfile.TemporaryDirectory() as directory:
        tape = Path(directory) / "releases.json"
        print("-- live: urllib sends each request, and the file keeps each answer")
        print(recorded(latest_releases(REPOS, "secret", api), tape, send))
        print(f"served: {served.total()}")
        server.shutdown()
        server.server_close()
        print("-- stopped: the server is gone, and the same file answers")
        print(recorded(latest_releases(REPOS, "secret", api), tape, offline))
