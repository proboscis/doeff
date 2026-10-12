"""本物の watchfiles の見張り N 本が待っている間に、loop.getaddrinfo('localhost') が何秒で答えるかを測る(card ki-fe848da94d24)。"""
import asyncio, sys, tempfile, time
from pathlib import Path
from doeff import do, run
from doeff_core_effects.effects import Await
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_events.effects.files import CloseFileWatch, NextFileChanges, WatchFiles
from doeff_events.handlers.os_files import os_file_watch_handler

N = int(sys.argv[1])

async def lookup():
    await asyncio.sleep(0.5)
    loop = asyncio.get_running_loop()
    start = time.monotonic()
    try:
        await asyncio.wait_for(loop.getaddrinfo("localhost", 80), 3.0)
        return round(time.monotonic() - start, 3)
    except TimeoutError:
        return "timeout>3s"

@do
def waiter(w):
    return (yield NextFileChanges(w))

@do
def program(root):
    ws = []
    for i in range(N):
        d = Path(root) / str(i); d.mkdir()
        ws.append((yield WatchFiles(str(d))))
    ts = []
    for w in ws:
        ts.append((yield Spawn(waiter(w))))
    took = yield Await(lookup())
    for w in ws:
        yield CloseFileWatch(w)
    for t in ts:
        yield Wait(t)
    return took

with tempfile.TemporaryDirectory() as root:
    print(N, "watches → getaddrinfo:", run(scheduled(await_handler()(os_file_watch_handler()(program(root))))))
