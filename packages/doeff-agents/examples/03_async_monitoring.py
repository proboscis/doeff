#!/usr/bin/env python3
"""
Async Monitoring Example - Using doeff Effects API

This example demonstrates using doeff async_run for monitoring
agent sessions with async/await patterns.

Features shown:
- async_run for non-blocking execution
- Parallel session execution with Gather-like patterns
- Fine-grained effects for monitoring multiple sessions
- Integration with preset_handlers for logging
"""

import asyncio
import time
from pathlib import Path

from doeff import do
from doeff.effects.writer import slog
from doeff_preset import preset_handlers

from _runtime import run_program
from doeff_agents import (
    AgentType,
    Capture,
    MockSessionScript,
    Launch,
    LaunchConfig,
    Monitor,
    Observation,
    SessionHandle,
    SessionStatus,
    Sleep,
    Stop,
    agent_effectful_handlers,
    mock_agent_handlers,
    configure_mock_session,
)


@do
def single_session_workflow(session_name: str, config: LaunchConfig):
    """Run a single session using effects.
    
    Yields effects interpreted by doeff_vm handlers.
    """
    yield slog(step="start", session_name=session_name)
    
    handle: SessionHandle = yield Launch(session_name, config)
    yield slog(step="launched", pane_id=handle.pane_id)
    
    final_status = SessionStatus.PENDING
    iteration = 0
    
    try:
        while iteration < 120:
            observation: Observation = yield Monitor(handle)
            final_status = observation.status
            
            if observation.output_changed:
                yield slog(
                    step="status_change",
                    status=observation.status.value,
                )
            
            if observation.pr_url:
                yield slog(step="pr_detected", url=observation.pr_url)
            
            if observation.is_terminal:
                break
            
            iteration += 1
            yield Sleep(0.5)
        
        output = yield Capture(handle, lines=20)
        yield slog(step="complete", status=final_status.value)
        
        return {
            "session_name": session_name,
            "status": final_status.value,
            "output": output,
            "iterations": iteration,
        }
    
    finally:
        yield Stop(handle)


@do
def parallel_tasks_workflow(tasks: list[tuple[str, str]]):
    """Run multiple agent tasks, demonstrating parallel-like patterns.
    
    Note: True parallelism requires launching separate runtimes or
    using asyncio.gather at the Python level. This example shows
    how to structure the effects for sequential execution.
    
    For true parallel execution, see run_truly_parallel() below.
    """
    yield slog(step="parallel_start", task_count=len(tasks))
    
    results = []
    
    for prompt, name_suffix in tasks:
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt=prompt,
        )
        
        session_name = f"parallel-{name_suffix}-{int(time.time())}"
        yield slog(step="task_start", name=name_suffix)
        
        # Run each task (sequentially in this case)
        result = yield from single_session_workflow(session_name, config)
        results.append((name_suffix, result))
        
        yield slog(step="task_complete", name=name_suffix, status=result["status"])
    
    yield slog(step="parallel_complete", completed=len(results))
    return results


@do
def interleaved_monitoring_workflow(configs: list[tuple[str, LaunchConfig]]):
    """Monitor multiple sessions with interleaved checks.
    
    This pattern launches all sessions first, then monitors them
    in a round-robin fashion until all are terminal.
    """
    yield slog(step="interleaved_start", session_count=len(configs))
    
    # Launch all sessions
    handles: list[tuple[str, SessionHandle]] = []
    for suffix, config in configs:
        session_name = f"interleaved-{suffix}-{int(time.time())}"
        handle = yield Launch(session_name, config)
        handles.append((suffix, handle))
        yield slog(step="launched", name=suffix, pane_id=handle.pane_id)
    
    # Track status for each session
    statuses = {suffix: SessionStatus.PENDING for suffix, _ in handles}
    
    try:
        timeout = 120
        start_time = time.time()
        
        while not all(s in (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.EXITED) 
                      for s in statuses.values()):
            if time.time() - start_time > timeout:
                yield slog(step="timeout", msg="Timeout reached")
                break
            
            # Check each session
            for suffix, handle in handles:
                if statuses[suffix] not in (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.EXITED):
                    observation = yield Monitor(handle)
                    
                    if observation.status != statuses[suffix]:
                        statuses[suffix] = observation.status
                        yield slog(
                            step="status_change",
                            name=suffix,
                            status=observation.status.value,
                        )
            
            yield Sleep(0.5)
        
        # Capture final outputs
        results = []
        for suffix, handle in handles:
            output = yield Capture(handle, lines=30)
            results.append({
                "name": suffix,
                "status": statuses[suffix].value,
                "output": output,
            })
        
        yield slog(step="interleaved_complete", results_count=len(results))
        return results
    
    finally:
        # Stop all sessions
        for suffix, handle in handles:
            yield Stop(handle)
            yield slog(step="stopped", name=suffix)


async def run_with_mock_runtime() -> None:
    """Run the example with mock handlers."""
    print("=" * 60)
    print("Running single session with mock handlers")
    print("=" * 60)

    configure_mock_session(
        "async-demo",  # Will be overwritten by actual session name
        MockSessionScript([
            (SessionStatus.RUNNING, "Writing haiku..."),
            (SessionStatus.DONE, "Code flows like water\nBugs emerge then disappear\nTests pass, peace returns"),
        ]),
    )
    
    # For mock, we need to pre-configure the session name
    session_name = f"async-demo-{int(time.time())}"
    configure_mock_session(
        session_name,
        MockSessionScript([
            (SessionStatus.RUNNING, "Writing haiku..."),
            (SessionStatus.DONE, "Code flows like water\nBugs emerge then disappear\nTests pass, peace returns"),
        ]),
    )
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a haiku about programming.",
    )

    result = await run_program(
        single_session_workflow(session_name, config),
        handler_maps=(preset_handlers(),),
        custom_handlers=mock_agent_handlers(),
    )
    print(f"\nResult: {result}")


async def run_truly_parallel() -> None:
    """Run multiple sessions truly in parallel using asyncio.gather.
    
    This demonstrates how to achieve true parallelism by running
    multiple async_run invocations concurrently.
    """
    print("\n" + "=" * 60)
    print("Running truly parallel sessions")
    print("=" * 60)
    
    tasks_config = [
        ("Write a function to reverse a string", "reverse-string"),
        ("Write a function to check if a number is prime", "is-prime"),
        ("Write a function to calculate fibonacci numbers", "fibonacci"),
    ]
    
    async def run_single_task(prompt: str, suffix: str) -> dict:
        """Run a single task with its own runtime."""
        session_name = f"parallel-{suffix}-{int(time.time())}"
        
        # Each task gets its own mock configuration
        configure_mock_session(
            session_name,
            MockSessionScript([
                (SessionStatus.RUNNING, f"Working on {suffix}..."),
                (SessionStatus.DONE, f"Completed {suffix}!"),
            ]),
        )
        
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path.cwd(),
            prompt=prompt,
        )

        return await run_program(
            single_session_workflow(session_name, config),
            handler_maps=(preset_handlers(),),
            custom_handlers=mock_agent_handlers(),
        )
    
    # Run all tasks truly in parallel
    start_time = time.time()
    results = await asyncio.gather(*[
        run_single_task(prompt, suffix)
        for prompt, suffix in tasks_config
    ])
    elapsed = time.time() - start_time
    
    print(f"\nAll sessions completed in {elapsed:.1f}s")
    for result in results:
        print(f"  {result['session_name']}: {result['status']}")


async def run_with_real_tmux() -> None:
    """Run with real tmux (requires tmux + claude CLI)."""
    import shutil
    
    if not shutil.which("tmux") or not shutil.which("claude"):
        print("tmux or Claude CLI not available, skipping")
        return
    
    print("\n" + "=" * 60)
    print("Running with real tmux")
    print("=" * 60)
    
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt="Write a haiku about programming.",
    )
    
    session_name = f"async-real-{int(time.time())}"
    
    result = await run_program(
        single_session_workflow(session_name, config),
        handler_maps=(preset_handlers(),),
        custom_handlers=agent_effectful_handlers(),
    )
    print(f"\nResult: {result}")


async def main() -> None:
    """Run all async examples."""
    await run_with_mock_runtime()
    await run_truly_parallel()
    
    # Uncomment to run with real tmux
    # await run_with_real_tmux()


if __name__ == "__main__":
    asyncio.run(main())
