# The page's first effect and handler: shrink_all yields Progress, and two handlers give it two meanings.
# show resumes k each time; stop_after(2) drops k at the second report. Prints what each handler saw, which
# images were shrunk, and each run's result. Backs the handler scenes.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Progress(EffectBase):
    done: int
    total: int


SHRUNK = []


@do
def shrink_all(images):
    sizes = []
    for width, height in images:
        sizes.append((width // 2, height // 2))
        SHRUNK.append((width, height))
        yield Progress(len(sizes), len(images))
    return sizes


shown = []


@handler
def show(effect: Progress, k):
    shown.append(f"{effect.done}/{effect.total}")
    return (yield Resume(k, None))


def stop_after(limit):
    seen = []

    @handler
    def on_progress(effect: Progress, k):
        seen.append(f"{effect.done}/{effect.total}")
        if effect.done >= limit:
            return f"stopped after {effect.done} of {effect.total}"
        return (yield Resume(k, None))

    return on_progress, seen


images = [(640, 480), (1024, 768), (800, 600)]

print("show:", run(show(shrink_all(images))))
print("  seen:", shown)
print("  shrunk:", SHRUNK)

SHRUNK.clear()
on_progress, seen = stop_after(2)
print("stop_after(2):", run(on_progress(shrink_all(images))))
print("  seen:", seen)
print("  shrunk:", SHRUNK)
