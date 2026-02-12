# The Three-Stage Pitch

doeff's compound value proposition. Each stage builds on the previous one. The sequentiality is the hook; what it enables is the sale.

## Why a Compound Pitch?

No single doeff feature is a "must-have" on its own:
- Flat yields = 2x cleaner code (nice, not urgent)
- Zero-mock testing = 5x cleaner tests (close, but mock is tolerable)
- Replay = impossible without effects (powerful, but unfamiliar)

Combined, they're irresistible: because your side effects are yields, testing becomes trivial, and because testing is trivial, replay is free.

## Stage 1: Flat Yields (Gets Attention)

**The hook.** "Your side effects become sequential yield statements."

### Before (typical Python with interleaved concerns):

```python
def process_order(order_id):
    logger.info(f"Processing {order_id}")

    try:
        cached = cache.get(f"order:{order_id}")
        if cached:
            return cached
    except CacheMiss:
        pass

    for attempt in range(3):
        try:
            order = db.query("SELECT ...", order_id)
            break
        except DBError as e:
            logger.warning(f"DB attempt {attempt}: {e}")
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

    try:
        price = requests.get(f"/api/price/{order.product_id}").json()["price"]
    except RequestException:
        logger.warning("Price API failed, using fallback")
        price = get_fallback_price(order.product_id)

    total = order.qty * price

    try:
        cache.set(f"order:{order_id}", total, ttl=300)
    except CacheError:
        logger.warning("Cache write failed, continuing")

    logger.info(f"Done: {total}")
    return total
```

### After (doeff):

```python
@do
def process_order(order_id) -> Program[float]:
    yield Log(f"Processing {order_id}")
    cached = yield Safe(CacheGet(f"order:{order_id}"))
    if cached.is_ok():
        return cached.value

    order = yield Retry(3, backoff=exp)(db_query("SELECT ...", order_id))
    price = yield recover(
        http_get(f"/api/price/{order.product_id}"),
        fallback=get_fallback_price(order.product_id)
    )

    total = order.qty * price
    yield Safe(CachePut(f"order:{order_id}", total, ttl=300))
    yield Log(f"Done: {total}")
    return total
```

**What the audience sees:** Retry logic, fallback patterns, cache handling — all collapsed into single yields. Business logic (`order.qty * price`) is no longer buried in 6 levels of try/except.

**The async/await parallel:** Python adopted async/await because callback hell was unreadable. doeff generalizes that same transformation to ALL side effects. Algebraic effects are a superset of async/await.

**Honest caveat:** This is a 2x improvement, not 10x. Developers can read both versions. The "before" code is ugly but familiar. The flat yields alone don't force adoption.

## Stage 2: Zero-Mock Testing (Gets Interest)

**The sale.** "Because all effects are yields, you can swap them all at once."

### Testing the imperative version:

```python
@patch('myapp.cache.get')
@patch('myapp.cache.set')
@patch('myapp.db.query')
@patch('myapp.requests.get')
@patch('myapp.time.sleep')
def test_process_order(mock_sleep, mock_http, mock_db, mock_cache_set, mock_cache_get):
    mock_cache_get.side_effect = CacheMiss()
    mock_db.return_value = Mock(qty=5, product_id="X")
    mock_http.return_value = Mock(json=lambda: {"price": 100})

    result = process_order("order-123")

    assert result == 500
    mock_cache_set.assert_called_once_with("order:order-123", 500, ttl=300)
    mock_db.assert_called_once()
    assert mock_sleep.call_count == 0
```

### Testing the doeff version:

```python
def test_process_order():
    result = run(process_order("order-123"), handlers=[
        InMemoryCache(),
        StubDB({"order-123": Order(qty=5, product_id="X")}),
        StubHTTP({"/api/price/X": {"price": 100}}),
    ])
    assert result.value == 500
```

**What the audience sees:**
- No `@patch`. No `Mock()`. No `side_effect`.
- No knowledge of implementation internals (which module imports what).
- The test reads as: "run this program with these fake services."
- Adding a new effect to the program doesn't break existing tests (no new patches needed).

**The deeper point:** Mock-based testing tests implementation details (which functions are called). Handler-based testing tests behavior (what effects are produced and what results come back). The latter is strictly better for refactoring safety.

## Stage 3: Replay (Gets Adoption)

**The lock-in.** "Because effects are data, you can record and replay them."

### Record a production run:

```python
result = run(process_order("order-123"), handlers=[
    RecordingHandler("runs/order-123-20260212.json"),
    RealDB(),
    RealHTTP(),
    RealCache(),
])
```

### Replay without any external services:

```python
result = run(process_order("order-123"), handlers=[
    ReplayHandler("runs/order-123-20260212.json"),
])
# Zero network calls. Zero DB queries. Instant. Deterministic.
```

### Replay with modifications:

```python
# Same recorded data, but with a new pricing algorithm
result = run(process_order("order-123"), handlers=[
    ReplayHandler("runs/order-123-20260212.json"),
    NewPricingHandler(),  # Override just the pricing effect
])
```

**What the audience sees:**
- Debug production bugs by replaying the exact sequence of effects
- Re-analyze data without re-running expensive computations
- Test new logic against recorded production data
- CI regression tests that replay real-world scenarios

**Why this is impossible without effects:**
In imperative code, side effects happen inside function calls. There's no seam to intercept. You'd have to mock every dependency at every call site. With effects, every side effect is a yield — a natural interception point.

## The Compound Effect

Each stage's value depends on the previous:

```
Stage 1: yields    -> Stage 2: testing  -> Stage 3: replay
(flat code)           (swap handlers)       (record/replay handlers)

Without Stage 1, you can't have Stage 2 (no yields = no handlers to swap)
Without Stage 2, you can't have Stage 3 (no handler swap = no replay handler)
```

This is why "effects" can't be sold as a single feature. It's a paradigm that unlocks a cascade of capabilities. The pitch must walk through all three stages.

## Per-Audience Emphasis

| Audience | Lead with | Close with |
|----------|-----------|------------|
| **Web developers** | Stage 2 (testing pain is universal) | Stage 1 (cleaner code) |
| **ML engineers** | Stage 3 (replay saves $$$ in GPU/API costs) | Stage 2 (testable pipelines) |
| **DevOps / batch** | Stage 3 (replay failed jobs, resume from checkpoint) | Stage 1 (flat retry/error handling) |
| **AI agent builders** | Stage 3 (replay agent sessions at $0) | Stage 2 (test without API calls) |
| **PL enthusiasts** | Stage 1 (algebraic effects in Python!) | Academic validation (Wang 2025) |

## The One-Liner

> "Your side effects become yields. Because of that, testing and replay become trivial."

## The Conference Talk Title

> "Never Mock Again: How Algebraic Effects Eliminate @patch from Python Testing"

or

> "$0 Debugging: Replay Any LLM Agent Session Without API Calls"
