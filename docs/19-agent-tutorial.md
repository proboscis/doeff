# Tutorial: Building an Automated Code Review System

This tutorial walks through building a practical application using `doeff-agents`:
an automated code review system that uses Claude to review pull requests.

## What We'll Build

A script that:
1. Launches Claude in a tmux session
2. Asks it to review code changes
3. Collects the review output
4. Handles errors gracefully

## Prerequisites

Before starting, ensure you have:

```bash
# Install tmux
brew install tmux  # macOS
# or: apt install tmux  # Ubuntu

# Install Claude Code CLI
npm install -g @anthropic/claude-code

# Install doeff-agents
pip install doeff-agents
```

Verify installation:

```bash
tmux -V       # Should show version
claude --help # Should show help text
```

## Step 1: Basic Session Launch

Let's start with a simple script that launches Claude and captures its output:

```python
# review_basic.py
import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    capture_output,
    launch_session,
    monitor_session,
    stop_session,
)


def review_file(file_path: str) -> str:
    """Get Claude to review a file."""
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"Review the code in {file_path} and provide feedback on code quality, potential bugs, and improvements.",
    )

    session_name = f"review-{int(time.time())}"
    session = launch_session(session_name, config)

    try:
        # Wait for completion (max 2 minutes)
        for _ in range(120):
            monitor_session(session)
            if session.is_terminal:
                break
            time.sleep(1)

        return capture_output(session, lines=100)
    finally:
        stop_session(session)


if __name__ == "__main__":
    import sys
    file_to_review = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    review = review_file(file_to_review)
    print(review)
```

Run it:

```bash
python review_basic.py src/utils.py
```

## Step 2: Add Context Manager for Safety

Let's improve the code using `session_scope` for automatic cleanup:

```python
# review_safe.py
import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    capture_output,
    monitor_session,
    session_scope,
)


def review_file(file_path: str, timeout: int = 120) -> dict:
    """
    Review a file with Claude.

    Returns a dict with:
    - success: bool
    - review: str (if success)
    - error: str (if failure)
    """
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"Please review {file_path}. Focus on:\n"
               f"1. Code quality and readability\n"
               f"2. Potential bugs or edge cases\n"
               f"3. Performance considerations\n"
               f"4. Security issues\n"
               f"Be concise and actionable.",
    )

    session_name = f"review-{int(time.time())}"

    with session_scope(session_name, config) as session:
        start = time.time()

        while not session.is_terminal:
            if time.time() - start > timeout:
                return {"success": False, "error": "Timeout"}

            monitor_session(session)
            time.sleep(1)

        output = capture_output(session, lines=200)

        if session.status == SessionStatus.DONE:
            return {"success": True, "review": output}
        elif session.status == SessionStatus.FAILED:
            return {"success": False, "error": f"Agent failed:\n{output[-500:]}"}
        else:
            return {"success": False, "error": f"Ended with status: {session.status.value}"}


if __name__ == "__main__":
    import sys
    import json

    file_to_review = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    result = review_file(file_to_review)

    if result["success"]:
        print("=== Code Review ===")
        print(result["review"])
    else:
        print(f"Review failed: {result['error']}")
```

## Step 3: Add Status Callbacks

Let's add real-time progress reporting:

```python
# review_progress.py
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    capture_output,
    monitor_session,
    session_scope,
)


@dataclass
class ReviewProgress:
    """Track review progress."""
    file_path: str
    started_at: datetime = field(default_factory=datetime.now)
    status_history: list[tuple[float, SessionStatus]] = field(default_factory=list)
    pr_url: str | None = None

    def on_status_change(
        self,
        old: SessionStatus,
        new: SessionStatus,
        output: str | None,
    ) -> None:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        self.status_history.append((elapsed, new))

        # Print progress
        icon = {
            SessionStatus.BOOTING: "🚀",
            SessionStatus.RUNNING: "⚙️",
            SessionStatus.BLOCKED: "⏸️",
            SessionStatus.DONE: "✅",
            SessionStatus.FAILED: "❌",
        }.get(new, "•")

        print(f"  {icon} [{elapsed:.1f}s] {new.value}")

    def on_pr_detected(self, url: str) -> None:
        self.pr_url = url
        print(f"  📝 PR: {url}")


def review_file_with_progress(file_path: str, timeout: int = 120) -> dict:
    """Review a file with progress tracking."""
    print(f"\n📋 Reviewing: {file_path}")
    print("-" * 40)

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"Review the code in {file_path}. Be thorough but concise.",
    )

    session_name = f"review-{int(time.time())}"
    progress = ReviewProgress(file_path)

    with session_scope(session_name, config) as session:
        start = time.time()

        while not session.is_terminal:
            if time.time() - start > timeout:
                return {
                    "success": False,
                    "error": "Timeout",
                    "progress": progress,
                }

            monitor_session(
                session,
                on_status_change=progress.on_status_change,
                on_pr_detected=progress.on_pr_detected,
            )
            time.sleep(0.5)

        output = capture_output(session, lines=200)

        return {
            "success": session.status == SessionStatus.DONE,
            "review": output if session.status == SessionStatus.DONE else None,
            "error": None if session.status == SessionStatus.DONE else session.status.value,
            "progress": progress,
        }


if __name__ == "__main__":
    import sys

    files = sys.argv[1:] if len(sys.argv) > 1 else ["main.py"]

    for file_path in files:
        result = review_file_with_progress(file_path)

        print("-" * 40)
        if result["success"]:
            print("Review completed successfully!")
            # print(result["review"])  # Uncomment to see full review
        else:
            print(f"Review failed: {result['error']}")
```

## Step 4: Async Batch Processing

For reviewing multiple files concurrently:

```python
# review_batch.py
import asyncio
import time
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    async_monitor_session,
    async_session_scope,
    capture_output,
)


async def review_file_async(file_path: str, timeout: float = 120) -> dict:
    """Asynchronously review a single file."""
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"Briefly review {file_path}. List top 3 issues if any.",
    )

    session_name = f"review-{file_path.replace('/', '-')}-{int(time.time())}"

    def on_change(old, new, output):
        print(f"  [{file_path}] {new.value}")

    try:
        async with async_session_scope(session_name, config) as session:
            # Create timeout task
            async def monitor_with_timeout():
                return await asyncio.wait_for(
                    async_monitor_session(session, on_status_change=on_change),
                    timeout=timeout,
                )

            try:
                final_status = await monitor_with_timeout()
            except asyncio.TimeoutError:
                return {"file": file_path, "success": False, "error": "Timeout"}

            output = capture_output(session, lines=100)

            return {
                "file": file_path,
                "success": final_status == SessionStatus.DONE,
                "review": output if final_status == SessionStatus.DONE else None,
                "error": None if final_status == SessionStatus.DONE else final_status.value,
            }

    except Exception as e:
        return {"file": file_path, "success": False, "error": str(e)}


async def review_batch(files: list[str], max_concurrent: int = 3) -> list[dict]:
    """
    Review multiple files with limited concurrency.

    Args:
        files: List of file paths to review
        max_concurrent: Maximum number of concurrent reviews
    """
    print(f"Starting batch review of {len(files)} files (max {max_concurrent} concurrent)")
    print("=" * 60)

    # Use semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited_review(file_path: str) -> dict:
        async with semaphore:
            print(f"Starting: {file_path}")
            result = await review_file_async(file_path)
            status = "✅" if result["success"] else "❌"
            print(f"{status} Finished: {file_path}")
            return result

    # Run all reviews
    start = time.time()
    results = await asyncio.gather(*[limited_review(f) for f in files])
    elapsed = time.time() - start

    # Summary
    print("=" * 60)
    print(f"Completed {len(results)} reviews in {elapsed:.1f}s")
    successful = sum(1 for r in results if r["success"])
    print(f"Success: {successful}/{len(results)}")

    return results


async def main():
    import sys

    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        # Default: find Python files in current directory
        files = [str(p) for p in Path.cwd().glob("*.py")][:5]  # Limit to 5

    if not files:
        print("No files to review!")
        return

    results = await review_batch(files, max_concurrent=2)

    # Print detailed results
    print("\n" + "=" * 60)
    print("Detailed Results:")
    print("=" * 60)

    for result in results:
        print(f"\n📄 {result['file']}:")
        if result["success"]:
            print("  Status: Success")
            # Uncomment to see reviews:
            # print(result["review"][:500] + "..." if len(result["review"]) > 500 else result["review"])
        else:
            print(f"  Status: Failed ({result['error']})")


if __name__ == "__main__":
    asyncio.run(main())
```

## Step 5: Full Application

Here's the complete code review system:

```python
# code_reviewer.py
"""
Automated Code Review System

Usage:
    python code_reviewer.py [files...]
    python code_reviewer.py --git-diff  # Review changed files
    python code_reviewer.py --watch     # Watch and review on change
"""

import argparse
import asyncio
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from doeff_agents import (
    AgentType,
    LaunchConfig,
    SessionStatus,
    async_monitor_session,
    async_session_scope,
    capture_output,
)


@dataclass
class ReviewResult:
    file: str
    success: bool
    review: str | None
    error: str | None
    duration: float


async def review_file(file_path: str, timeout: float = 180) -> ReviewResult:
    """Review a single file."""
    start = time.time()

    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"""Review the code in {file_path}. Provide:

1. **Summary** (1-2 sentences)
2. **Issues** (if any, ordered by severity)
3. **Suggestions** (optional improvements)

Be concise and specific. Use file:line references.""",
    )

    session_name = f"review-{Path(file_path).stem}-{int(time.time())}"

    try:
        async with async_session_scope(session_name, config) as session:
            try:
                final = await asyncio.wait_for(
                    async_monitor_session(session, poll_interval=0.5),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return ReviewResult(
                    file=file_path,
                    success=False,
                    review=None,
                    error="Timeout",
                    duration=time.time() - start,
                )

            output = capture_output(session, lines=200)

            return ReviewResult(
                file=file_path,
                success=final == SessionStatus.DONE,
                review=output if final == SessionStatus.DONE else None,
                error=None if final == SessionStatus.DONE else final.value,
                duration=time.time() - start,
            )

    except Exception as e:
        return ReviewResult(
            file=file_path,
            success=False,
            review=None,
            error=str(e),
            duration=time.time() - start,
        )


async def review_files(files: list[str], max_concurrent: int = 2) -> list[ReviewResult]:
    """Review multiple files with concurrency limit."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited(path: str) -> ReviewResult:
        async with semaphore:
            print(f"  ⏳ Reviewing: {path}")
            result = await review_file(path)
            icon = "✅" if result.success else "❌"
            print(f"  {icon} Done: {path} ({result.duration:.1f}s)")
            return result

    return await asyncio.gather(*[limited(f) for f in files])


def get_git_changed_files() -> list[str]:
    """Get list of changed files from git."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        # Filter to Python files
        return [f for f in files if f.endswith(".py")]
    except subprocess.CalledProcessError:
        return []


def print_results(results: list[ReviewResult]) -> None:
    """Print formatted results."""
    print("\n" + "=" * 70)
    print("CODE REVIEW RESULTS")
    print("=" * 70)

    for result in results:
        print(f"\n📄 {result.file}")
        print("-" * 50)

        if result.success:
            print(result.review)
        else:
            print(f"❌ Review failed: {result.error}")

    # Summary
    print("\n" + "=" * 70)
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    total_time = sum(r.duration for r in results)

    print(f"Total: {len(results)} files reviewed in {total_time:.1f}s")
    print(f"  ✅ Successful: {len(successful)}")
    print(f"  ❌ Failed: {len(failed)}")

    if failed:
        print("\nFailed files:")
        for r in failed:
            print(f"  - {r.file}: {r.error}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Automated Code Review")
    parser.add_argument("files", nargs="*", help="Files to review")
    parser.add_argument("--git-diff", action="store_true", help="Review git changed files")
    parser.add_argument("--concurrent", "-c", type=int, default=2, help="Max concurrent reviews")
    args = parser.parse_args()

    # Determine files to review
    if args.git_diff:
        files = get_git_changed_files()
        if not files:
            print("No changed Python files found.")
            return
        print(f"Found {len(files)} changed files")
    elif args.files:
        files = args.files
    else:
        files = [str(p) for p in Path.cwd().glob("*.py")][:5]

    if not files:
        print("No files to review!")
        return

    print(f"\n🔍 Starting review of {len(files)} file(s)...\n")

    results = await review_files(files, max_concurrent=args.concurrent)
    print_results(results)


if __name__ == "__main__":
    import shutil

    if not shutil.which("claude"):
        print("Error: Claude CLI not found.")
        print("Install with: npm install -g @anthropic/claude-code")
        exit(1)

    asyncio.run(main())
```

## Running the Application

```bash
# Review specific files
python code_reviewer.py src/main.py src/utils.py

# Review git changes
python code_reviewer.py --git-diff

# Increase concurrency
python code_reviewer.py --concurrent 4 *.py
```

## Key Takeaways

1. **Use context managers** (`session_scope`, `async_session_scope`) for automatic cleanup
2. **Monitor for terminal states** to know when the agent is done
3. **Handle BLOCKED status** when the agent needs input
4. **Use callbacks** for progress reporting and event handling
5. **Limit concurrency** when running multiple sessions
6. **Set timeouts** to prevent hanging

## Next Steps

- Add support for different output formats (JSON, Markdown)
- Integrate with CI/CD pipelines
- Add GitHub PR commenting
- Implement caching to avoid re-reviewing unchanged files

See the [API Reference](18-agent-session-management.md) for more details.
