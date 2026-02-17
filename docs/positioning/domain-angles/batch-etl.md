# Domain Angle: Batch Jobs & ETL Pipelines

Underserved community with massive pain. Batch jobs are where doeff's retry, checkpointing, and replay capabilities shine brightest.

## The Pain

Batch jobs processing millions of records face:
- **Partial failures**: Job processes 7,432 of 10,000 records, then crashes. Restart from zero?
- **Retry complexity**: Exponential backoff + circuit breaker + fallback + logging = nested try/except hell
- **State threading**: Tracking progress, accumulating results, managing counters across 10k iterations
- **Testing impossibility**: Can't unit test a job that talks to 5 external services
- **Debugging**: "It failed at 3am. Which record? What state? What was the error?"

## The doeff Approach

```python
@do
def process_batch(items: list[str]) -> Program[BatchResult]:
    yield Tell(f"Starting batch of {len(items)} items")
    results = []
    for i, item in enumerate(items):
        yield Put("progress", i)
        result = yield Try(process_single_item(item))
        if result.is_ok():
            results.append(result.value)
            yield Tell({"processed": item, "status": "ok"})
        else:
            yield Tell({"processed": item, "status": "error", "error": str(result.error)})
    return BatchResult(total=len(items), succeeded=len(results), results=results)

@do
def process_single_item(item: str) -> Program[ItemResult]:
    data = yield Retry(3, backoff=exp)(fetch_data(item))
    enriched = yield recover(
        enrich_from_api(data),
        fallback=enrich_from_cache(data),
    )
    yield store_result(enriched)
    return ItemResult(item=item, data=enriched)
```

Retry, fallback, error handling, progress tracking, logging — all flat yields. Business logic (`enrich`, `store`) is visible, not buried.

## The Killer Feature: Resume from Checkpoint

```python
# Run with recording — captures all effects
try:
    result = run(process_batch(items), handlers=[
        RecordingHandler("runs/batch_2026_02_12.json"),
        RealDBHandler(),
        RealAPIHandler(),
    ])
except BatchError:
    pass  # Recording captured everything up to the failure point

# Resume from where it failed — replay completed effects, continue with real ones
result = run(process_batch(items), handlers=[
    ResumeHandler("runs/batch_2026_02_12.json"),  # replays completed, then switches to real
    RealDBHandler(),
    RealAPIHandler(),
])
# Skips the 7,432 already-processed records. Continues from 7,433.
```

**Your 6-hour job doesn't restart from zero.**

## The Testing Story

```python
def test_batch_with_partial_failure():
    items = ["good1", "bad1", "good2"]
    result = run(process_batch(items), handlers=[
        StubDB({"good1": data1, "good2": data2}),
        StubAPI({"good1": enriched1, "bad1": APIError("timeout"), "good2": enriched2}),
    ])
    assert result.value.total == 3
    assert result.value.succeeded == 2
    # Verify the error was logged
    assert any("bad1" in log["processed"] for log in result.writer_output)

def test_retry_behavior():
    call_count = 0
    def flaky_api(item):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise APIError("transient")
        return {"data": item}

    result = run(process_single_item("test"), handlers=[
        FunctionHandler(fetch_data=lambda i: {"raw": i}),
        FunctionHandler(enrich_from_api=flaky_api),
    ])
    assert call_count == 3  # retried twice, succeeded on third
    assert result.value.data == {"data": "test"}
```

No `@patch`. No `unittest.mock`. No knowledge of which module imports what.

## Competing Solutions

| Tool | Strengths | Doesn't Do |
|------|-----------|-----------|
| **Prefect** | Orchestration, retries, observability | No effect-level replay, no handler composition |
| **Airflow** | Scheduling, DAG management | Task-level granularity, no effect system |
| **Dagster** | Software-defined assets, type safety | Asset-level, not effect-level |
| **Celery** | Distributed task queue | No composition, no replay |
| **doeff** | Effect-level composition, recording, replay, handler swap | No scheduling (compose with Prefect/Airflow) |

doeff doesn't replace orchestrators. It makes the code inside each task composable, testable, and replayable. Use Prefect to schedule the job. Use doeff to write it.

## The Pitch

> "Your batch job processes 10,000 records. It fails at record 7,432 after 6 hours. With doeff, you resume from 7,432 — because every side effect was recorded as a yield. No restart. No lost progress. No re-processing."
