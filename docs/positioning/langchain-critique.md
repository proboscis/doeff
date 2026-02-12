# LangChain: What Went Wrong, and How Effects Fix It

LangChain (126k stars) is the most popular Python LLM framework. It's also the most criticized. The criticisms map precisely to problems that algebraic effects solve.

## The Community Verdict

### Production teardowns (all independently reaching the same conclusion):

**Octomind** (Jun 2024, [480 points on HN](https://news.ycombinator.com/item?id=40739982)):
> "When our team began spending as much time understanding and debugging LangChain as it did building features, it wasn't a good sign."

**Stackademic** (Jan 2026): Profiled a RAG chatbot.
- 12s average response with LangChain -> 3.2s without (73% faster)
- 47 transitive dependencies -> ~5
- Memory leaks: ~2MB per request via callback system
- Onboarding: 2 weeks -> 2 days

**Max Woolf / minimaxir** (Jul 2023, [268 points on HN](https://news.ycombinator.com/item?id=36725982)):
> "LangChain is one of the few pieces of software that *increases* overhead in most of its popular use cases."

**Harrison Chase** (LangChain CEO) acknowledged on HN:
> "The initial version was pretty high level and absolutely abstracted away too much."

### The recurring pattern:

```
1. New AI project starts
2. Someone suggests LangChain ("industry standard!")
3. Build prototype -> works (barely)
4. Go to production
5. Performance sucks / debugging is hell
6. Team rewrites without LangChain
7. Everything gets faster and simpler
8. Write blog post about it
```

## Problem-by-Problem Mapping

### 1. Over-Abstraction

**LangChain problem:**
```python
# 3 new abstractions to make one API call
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

chain = ChatPromptTemplate.from_template("tell me about {topic}") | ChatOpenAI() | StrOutputParser()
result = chain.invoke({"topic": "bears"})
```

**Raw Python equivalent:**
```python
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": f"tell me about {topic}"}]
)
result = response.choices[0].message.content
```

**PL theory diagnosis:** Premature abstraction without algebraic foundation. `ChatPromptTemplate` is an f-string. `StrOutputParser` is `.content`. The `|` pipe overloads Python's bitwise OR in non-obvious ways.

**doeff approach:**
```python
@do
def tell_me_about(topic: str) -> Program[str]:
    response = yield LLMChat(
        messages=[{"role": "user", "content": f"tell me about {topic}"}],
        model="gpt-4o",
    )
    return response.content
```

No wrapper classes. The function IS the chain. The `yield` IS the effect dispatch. No new abstractions beyond what Python generators already provide.

### 2. Debugging Opacity (50-Layer Stack Traces)

**LangChain problem:**
```
# Actual error output
ValidationError: 1 validation error for LLMChain
  prompt field required (type=value_error.missing)
  in BaseChain.validate -> RetrievalQA.__init__ -> ConversationalRetrievalChain.from_llm
  [... 35 more lines through framework internals]
```

**PL theory diagnosis:** Side effects are embedded in method calls through 10+ abstraction layers. There's no clean boundary between user code and framework internals.

**doeff approach:** Effects are data. They're yielded, not called through a chain of wrapper objects. The handler processes the effect data directly. Stack traces stay shallow because the interpreter loop is the only framework code in the path.

### 3. Testing Nightmare

**LangChain problem:**
```python
# Must mock through multiple abstraction layers
@patch('myapp.chains.retriever.get_relevant_documents')
@patch('myapp.chains.llm.predict')
@patch('myapp.chains.parser.parse')
def test_rag_chain(mock_parse, mock_predict, mock_retrieve):
    mock_retrieve.return_value = [Document(page_content="...")]
    mock_predict.return_value = "answer"
    # 15 more lines of mock setup...
```

**PL theory diagnosis:** Side effects are baked into business logic. Testing requires surgical patching of internal implementation details.

**doeff approach:**
```python
def test_rag_pipeline():
    result = run(
        rag_pipeline("query"),
        handlers=[
            StubLLM({"Analyze": "cached analysis"}),
            InMemoryRetriever({"query": [doc1, doc2]}),
        ]
    )
    assert result.value == expected
```

No patching. No mock objects. No knowledge of implementation internals. Swap the handler, the program doesn't change.

### 4. No Effect Separation

**LangChain problem:** The `Runnable.invoke()` method executes side effects (LLM calls, retrieval, parsing) inside the method call. Callbacks provide observability hooks but don't separate effects from logic.

**PL theory diagnosis:** Lack of parametric polymorphism over effects. The program IS the execution. You can't run the same program with different effect handlers.

**doeff approach:** Effects are first-class values that the program yields. The handler decides what to do with them. Same program, different handlers = different behavior. This is the core algebraic insight.

### 5. Vendor Lock-in

**LangChain problem:**
- LangSmith (observability) is closed-source SaaS
- Switching away requires rewriting all chains, prompts, parsers
- Community integrations vary wildly in quality

**doeff approach:** Handler stacking IS the integration layer. Adding a new provider = one handler function. No framework-specific wrappers. No marketplace. Just Python functions that match on effect types.

### 6. Performance Overhead

**LangChain documented overhead (Stackademic teardown):**

| Source | Time |
|--------|------|
| Chain initialization & validation | 1.2s |
| Prompt template rendering | 0.8s |
| Response parsing & callbacks | 2.9s |
| Serialization & logging | 1.1s |
| Miscellaneous overhead | 2.0s |
| **Total framework overhead** | **8.0s** |
| Actual OpenAI API call | 3.1s |

73% of response time was LangChain overhead, not LLM latency.

**doeff approach:** Effects are dataclasses. Yielding an effect is a generator send/receive. The Rust VM handles continuation management. The overhead is microseconds per effect dispatch, not seconds.

## The Fundamental Architectural Difference

```
LangChain:
  User Code -> PromptTemplate -> ChatModel -> OutputParser -> Callback -> ...
                    |                |             |              |
                    v                v             v              v
               (validation)    (API call)    (parsing)      (logging)
               = tightly coupled, every layer adds latency and complexity

doeff:
  User Code:  yield LLMChat(...)  yield Parse(...)  yield Log(...)
                    |                    |                  |
                    v                    v                  v
  Handler Stack:  [openai_handler]  [parse_handler]  [log_handler]
               = decoupled, each handler is independent, composable, swappable
```

LangChain's problems aren't implementation bugs. They're architectural. The chain/callback pattern fundamentally couples execution with side effects. Algebraic effects fundamentally separate them.

## Sources

- [The Problem With LangChain](https://minimaxir.com/2023/07/langchain-problem/) — Max Woolf, Jul 2023
- [Why we no longer use LangChain](https://www.octomind.dev/blog/why-we-no-longer-use-langchain-for-building-our-ai-agents) — Octomind, Jun 2024
- [LangChain Made Our AI App Slow](https://blog.stackademic.com/langchain-made-our-ai-app-slow-we-rewrote-without-it-6386b78880d3) — Stackademic, Jan 2026
- [Challenges & Criticisms of LangChain](https://shashankguda.medium.com/challenges-criticisms-of-langchain-b26afcef94e7) — Mar 2025
- [HN discussion (480 pts)](https://news.ycombinator.com/item?id=40739982) — Octomind post
- [HN discussion (268 pts)](https://news.ycombinator.com/item?id=36725982) — minimaxir post
