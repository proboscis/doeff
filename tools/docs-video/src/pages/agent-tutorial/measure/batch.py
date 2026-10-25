# The batch scene: Step 4 of docs/19-agent-tutorial.md (review_batch.py). Four files, asyncio.Semaphore(2) as the
# page's max_concurrent=2, asyncio.gather over all of them; each review is async_session_scope plus
# async_monitor_session (poll every 1 s), on the scripted terminal of stand_ins.py where the scripted agent takes
# 3.0 / 1.0 / 2.5 / 1.5 s. Runs the batch with max_concurrent=2, then with 1 to compare. Prints when each review
# starts and finishes, how many run at once at most, the total, and the order gather returns the results in.
import asyncio
import time
from pathlib import Path

from stand_ins import ScriptedTerminal

from doeff_agents import AgentType, LaunchConfig, async_monitor_session, async_session_scope

WORK = {"utils.py": 3.0, "api.py": 1.0, "models.py": 2.5, "cli.py": 1.5}


def at() -> str:
    return f"[{time.monotonic() - start:4.1f}s]"


async def review_file_async(file_path: str) -> dict:
    config = LaunchConfig(agent_type=AgentType.CLAUDE, work_dir=Path.cwd(), prompt=f"Briefly review {file_path}.")
    async with async_session_scope(f"review-{file_path.replace('.', '-')}", config, backend=terminal) as session:
        final = await asyncio.wait_for(async_monitor_session(session), timeout=120)
        return {"file": file_path, "ended": final.value}


async def review_batch(files: list[str], max_concurrent: int = 2) -> list[dict]:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited_review(file_path: str) -> dict:
        global running, most
        async with semaphore:
            running += 1
            most = max(most, running)
            print(at(), "Starting:", file_path)
            result = await review_file_async(file_path)
            print(at(), "Finished:", file_path, result["ended"])
            running -= 1
            return result

    return await asyncio.gather(*[limited_review(name) for name in files])


for limit in (2, 1):
    terminal = ScriptedTerminal(work=WORK, review={name: ["1. ok."] for name in WORK})
    start = time.monotonic()
    running = 0
    most = 0
    print(f"review_batch(files, max_concurrent={limit}):")
    results = asyncio.run(review_batch(list(WORK), max_concurrent=limit))
    print(f"Completed {len(results)} reviews in {time.monotonic() - start:.1f}s (the scripted work adds up to {sum(WORK.values()):.1f}s)")
    print("most at once:", most)
    print("gather order:", [r["file"] for r in results])
    print("sessions killed, in order:", terminal.killed)
