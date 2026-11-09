"""The runs behind the article2-opening video: every result it shows, from the examples of the article "Yield and async"
(examples/yield_and_async: greet.py, drive.py, stream.py).

Usage (from the repository root): uv run python tools/docs-video/src/pages/article2-opening/measure/opening.py > tools/docs-video/src/pages/article2-opening/measure/opening.log
"""

import asyncio
import sys
from pathlib import Path

# The article's examples (drive, greet, stream) are imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "yield_and_async"))

from doeff_core_effects import scheduled
from drive import drive, twice_async
from greet import ada, closed, greet, greet_async, titled, together, twice
from stream import bridged, collect, collect_in_one, counted, tokens

from doeff import run

print("o1 greet_async under asyncio.run:", asyncio.run(greet_async()))
print("o1 greet under ada:", run(ada(greet())))
print("o4 twice_async under drive:", drive(twice_async(), {"name": "Ada"}))
try:
    asyncio.run(twice_async())
except RuntimeError as error:
    print("o4 twice_async under asyncio.run: RuntimeError:", error)
print("o5 ada(twice()):", run(ada(twice())))
print("o5 ada(titled(twice())):", run(ada(titled(twice()))))
print("o6 closed(twice()):", run(closed(twice())))
print("o6 scheduled(ada(together())):", run(scheduled(ada(together()))))
text, waits = run(bridged(counted(collect(tokens()))))
print("o7 collect, one Await per chunk:", repr(text), len(waits))
text, waits = run(bridged(counted(collect_in_one(tokens()))))
print("o7 collect_in_one:", repr(text), len(waits))
