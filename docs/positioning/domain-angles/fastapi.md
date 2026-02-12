# Domain Angle: FastAPI / Web Development

The largest Python web community. FastAPI developers already think in dependency injection. doeff generalizes what they already know.

## The Bridge: Depends() -> Ask Effect

FastAPI's `Depends()` is already a limited Reader effect:

```python
# FastAPI: dependency injection via Depends()
from fastapi import Depends

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/users/{user_id}")
async def get_user(user_id: int, db: Session = Depends(get_db)):
    return db.query(User).filter(User.id == user_id).first()
```

```python
# doeff: dependency injection via Ask effect
from doeff import do, Ask

@do
def get_user(user_id: int) -> Program[User]:
    db = yield Ask("db")
    return db.query(User).filter(User.id == user_id).first()
```

The structural similarity is intentional. But doeff's Ask does more:

| Capability | FastAPI Depends() | doeff Ask |
|-----------|------------------|-----------|
| Inject a dependency | Yes | Yes |
| Swap for testing | Override in TestClient | Handler swap |
| Apply to ALL endpoints at once | Middleware (limited) | Handler stacking |
| Compose with retry, caching | Manual wiring | Orthogonal handlers |
| Record/replay | No | Yes |

## Where doeff Adds Value Over FastAPI

### 1. Cross-Cutting Concerns Without Middleware Spaghetti

```python
# FastAPI: each concern is a separate middleware or dependency
@app.middleware("http")
async def add_logging(request, call_next):
    logger.info(f"Request: {request.url}")
    response = await call_next(request)
    return response

@app.middleware("http")
async def add_cost_tracking(request, call_next):
    # Can't easily track LLM costs here — middleware doesn't see function internals
    response = await call_next(request)
    return response
```

```python
# doeff: handlers compose orthogonally
@do
def handle_request(user_id: int) -> Program[Response]:
    user = yield get_user(user_id)
    analysis = yield LLMChat(messages=[...], model="gpt-4o")
    return Response(user=user, analysis=analysis)

# Logging, cost tracking, retry — all as stacked handlers
result = run(
    handle_request(42),
    handlers=[
        logging_handler,      # logs ALL effects (DB, LLM, cache)
        cost_cap_handler,     # caps LLM spend per request
        retry_handler,        # retries transient failures
        db_handler,           # provides real DB
        openai_handler,       # provides real LLM
    ]
)
```

FastAPI middleware sees HTTP requests. doeff handlers see every effect — including LLM calls, DB queries, and cache operations inside your endpoint logic.

### 2. Testing Without TestClient Gymnastics

```python
# FastAPI testing: override dependencies, create TestClient
def override_get_db():
    return fake_db

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)
response = client.get("/users/42")

# Problem: how do you mock the LLM call inside the endpoint?
# Answer: more @patch decorators
@patch("myapp.endpoints.openai.chat.completions.create")
def test_endpoint(mock_llm):
    mock_llm.return_value = Mock(...)
    response = client.get("/users/42")
```

```python
# doeff testing: swap all handlers at once
def test_handle_request():
    result = run(handle_request(42), handlers=[
        StubDB(users={42: fake_user}),
        StubLLM(responses={"Analyze": "cached analysis"}),
    ])
    assert result.value.analysis == "cached analysis"
```

No TestClient. No dependency_overrides. No @patch. One line per fake service.

### 3. The Gradual Adoption Path

doeff doesn't require rewriting your FastAPI app. You can adopt it incrementally:

```python
# Step 1: Use doeff inside a single endpoint
@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    result = run(
        analyze_pipeline(request.text),
        handlers=[openai_handler, cache_handler, db_handler],
    )
    return result.value

# Step 2: Add recording for debugging
@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    result = run(
        analyze_pipeline(request.text),
        handlers=[
            RecordingHandler(f"traces/{request.id}.json"),
            openai_handler, cache_handler, db_handler,
        ],
    )
    return result.value

# Step 3: Replay production issues
# When a user reports a bug, replay their trace locally
result = run(
    analyze_pipeline("the problematic input"),
    handlers=[ReplayHandler("traces/bug_report_123.json")],
)
```

## The Pitch

> "You already use FastAPI's Depends() for dependency injection. doeff generalizes that pattern to every side effect — LLM calls, caching, retries, logging. Same idea, but composable. And because everything is a yield, you can record and replay any request without a TestClient."

## Who This Targets

- FastAPI developers who are adding LLM features to their web apps
- Teams with `@patch` fatigue in their test suites
- Backend engineers who want the Depends() pattern to work for more than just DB sessions
