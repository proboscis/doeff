# Proposal: doeff-flow — Node-Based WebUI for doeff

**Status**: Draft  
**Author**: Design discussion (2025-01-29)  
**Package**: `doeff-flow` (new subpackage)

## Summary

Create a ComfyUI-like node-based visual workflow system built on doeff's principled foundations. Unlike ComfyUI's ad-hoc architecture, this design leverages doeff's effect system and Hy macros to achieve a clean separation of concerns with a single source of truth.

## Motivation

ComfyUI demonstrates the value of node-based workflow editors for AI pipelines, but its architecture has fundamental limitations:

| Issue | ComfyUI Problem |
|-------|-----------------|
| Source of truth | Split between Python classes and JSON workflows |
| UI schema | Mixed into class attributes (`INPUT_TYPES` dict) |
| Type safety | Strings everywhere (`"MODEL"`, `"LATENT"`) |
| Graph format | Numeric ID JSON — not diffable, not human-readable |
| Composability | Nodes are isolated, can't compose functionally |
| Testing | Requires full runtime, hard to unit test |
| Version control | Painful — ID changes break diffs |

doeff already has the primitives for a cleaner solution:
- `Program[T]` — lazy, composable computations
- `Effect` — data describing operations
- `WGraph`, `WStep`, `WNode` — DAG representation
- `@cache` — memoization
- `Gather` — parallel execution
- Effect interception — event emission

## Design

### Core Insight

In doeff:
- Each `@do` function naturally forms a **node**
- `yield Effect(...)` represents **operations requiring input/output**
- Function parameters are **input ports**
- Return value is the **output port**
- Composition via `yield` creates **edges**

The missing piece is **metadata** for the UI to know what widgets to render.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            Hy Source                                │
│   (defnode ...) (defgraph ...)  ──── single source of truth         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
          ┌─────────────────┐     ┌─────────────────┐
          │   UI Schema     │     │  doeff Program  │
          │     (JSON)      │     │   (runtime)     │
          └────────┬────────┘     └────────┬────────┘
                   │                       │
                   ▼                       ▼
          ┌─────────────────┐     ┌─────────────────┐
          │    Frontend     │◄────│    Runtime      │
          │   (litegraph)   │ SSE │  (async exec)   │
          └─────────────────┘     └─────────────────┘
```

### Hy Macros

#### Node Definition (`defnode`)

```hy
(defnode ksampler 
  [^{:type "MODEL"} model
   ^{:widget "seed"} seed
   ^{:widget "slider" :min 1 :max 10000 :default 20} steps
   ^{:widget "slider" :min 0.0 :max 100.0 :default 8.0} cfg]
  
  "Sample latent using model"
  :category "sampling"
  :returns "LATENT"
  
  (yield (KSample model seed steps cfg)))
```

The macro expands to:
1. A `@do` decorated Python function (runtime)
2. A registration call with UI schema (metadata)
3. Type information for the node registry

#### Effect Definition (`defeffect`)

```hy
(defeffect ImageLoad []
  "Load image from user"
  :widget "dropzone"
  :accepts ["image/png" "image/jpeg"]
  :output-type Image)

(defeffect TextInput [^{:default ""} initial]
  "Text input field"
  :widget "textarea"
  :multiline True
  :output-type str)

(defeffect IntSlider [min max ^{:default None} default]
  "Integer slider"
  :widget "slider"
  :output-type int)
```

#### Graph Definition (`defgraph`)

```hy
(defgraph txt2img-workflow
  :nodes {
    ckpt    (load-checkpoint "sd_xl_base")
    pos     (clip-encode :model ckpt.clip :text "a photo of a cat")
    neg     (clip-encode :model ckpt.clip :text "blurry, bad")
    latent  (empty-latent :width 1024 :height 1024)
    sampled (ksampler :model ckpt.model
                      :positive pos
                      :negative neg
                      :latent latent
                      :steps 20
                      :cfg 7.0)
    image   (vae-decode :vae ckpt.vae :latent sampled)
  }
  :output image)
```

This compiles to:
1. A schema with nodes and edges (for UI)
2. A runnable `@do` function with topologically sorted execution

### Event System

The runtime emits events during execution:

```hy
{:type :node-start   :node "pos"     :timestamp 1706123456}
{:type :node-progress :node "sampled" :step 5 :total 20}
{:type :node-cached  :node "ckpt"    :cache-key "abc123"}
{:type :node-done    :node "pos"     :duration-ms 142}
{:type :node-error   :node "sampled" :error "OOM"}
{:type :graph-done   :output-node "image" :total-ms 3420}
```

Using doeff's effect interception:

```hy
(defn with-events [program event-sink]
  (.intercept program
    (fn [effect]
      (put! event-sink {:type :effect 
                        :effect-type (type effect)
                        :node (current-node)})
      effect)))
```

### Round-Trip: UI ↔ Code

The system supports bidirectional conversion:

```
Hy Code ──compile──▶ JSON Schema ──render──▶ Web UI
   ▲                                            │
   └────────────serialize────────────────────────┘
```

UI edits can be serialized back to Hy code for version control.

## Comparison with ComfyUI

| Aspect | ComfyUI | doeff-flow |
|--------|---------|------------|
| **Source of truth** | Split (class + JSON) | Single (Hy code) |
| **UI schema** | `INPUT_TYPES` dict in class | Derived from `^{...}` metadata |
| **Type safety** | Strings `"MODEL"` | Actual types possible |
| **Graph format** | Numeric ID JSON | Readable Hy s-expressions |
| **Diffable** | No (ID changes) | Yes (named symbols) |
| **Version control** | Painful | Natural |
| **Composability** | None | Full (just functions) |
| **Testing** | Requires GPU + models | **Mock handlers, instant** |
| **Caching** | Custom hash logic | doeff `@cache` decorator |
| **Events** | Bespoke WebSocket | Effect interception |
| **Error handling** | Try/catch scattered | `Try` effect, structured |
| **Extensibility** | Monkey-patch nodes | Compose programs |
| **Round-trip** | UI → JSON only | UI ↔ Hy ↔ JSON |

## Key Architectural Advantage: Complete Mockability

**ComfyUI's testing reality** (from their repo):
- `tests-unit/` — Tests utilities like `validate_node_input()`, not workflows
- `tests/inference/` — Requires running server, GPU, and models
- Tests connect to `--listen 127.0.0.1 --port 8188`
- Compare actual generated images pixel-by-pixel
- **No way to test workflow logic without full execution**

**doeff-flow's approach** — Effects are data, handlers are swappable:

```python
# Test workflow with mock handlers - NO GPU, NO models, milliseconds
mock_handlers = {
    ImageLoad: lambda e, ts, s: ContinueValue(FakeImage(512, 512), ...),
    Encode: lambda e, ts, s: ContinueValue(FakeEmbedding(), ...),
    Sample: lambda e, ts, s: ContinueValue(FakeLatent(), ...),
    Decode: lambda e, ts, s: ContinueValue(FakeImage(1024, 1024), ...),
}

runtime = AsyncRuntime(handlers=mock_handlers)

@pytest.mark.asyncio
async def test_workflow_executes_correct_order():
    """Test graph topology - NO GPU needed."""
    workflow = load_workflow("txt2img.hy")
    executed = []
    
    # Track which effects run
    tracking_runtime = AsyncRuntime(handlers=make_tracking_handlers(executed))
    await tracking_runtime.run(workflow)
    
    assert executed == ["ImageLoad", "Encode", "Sample", "Decode"]

@pytest.mark.asyncio  
async def test_caching_skips_unchanged_nodes():
    """Test cache behavior - NO GPU needed."""
    workflow = load_workflow("txt2img.hy")
    call_counts = Counter()
    
    counting_runtime = AsyncRuntime(handlers=make_counting_handlers(call_counts))
    await counting_runtime.run(workflow)
    await counting_runtime.run(workflow)  # Second run
    
    assert call_counts["Encode"] == 1  # Cached on second run

@pytest.mark.asyncio
async def test_error_propagation():
    """Test error handling - NO GPU needed."""
    def failing_sample(e, ts, s):
        raise RuntimeError("Out of memory")
    
    runtime = AsyncRuntime(handlers={**mock_handlers, Sample: failing_sample})
    result = await runtime.run(workflow)
    
    assert result.is_err()
    assert "Out of memory" in str(result.error)
```

### Testing Capability Comparison

| Capability | ComfyUI | doeff-flow |
|------------|---------|------------|
| Test workflow logic | ❌ Requires models | ✅ Mock handlers |
| Test node ordering | ❌ Requires execution | ✅ Inspect Program |
| Test caching | ❌ Run twice with GPU | ✅ Mock + count |
| Test error handling | ❌ Force real errors | ✅ Inject failures |
| Run in CI | ❌ Need GPU runner | ✅ Any CI |
| Test speed | ❌ Minutes | ✅ Milliseconds |
| Test parallelism | ❌ Real Gather | ✅ Mock Gather |

This is a **fundamental architectural advantage** — workflows created in the WebUI can be tested via CLI with mock handlers, enabling TDD for AI pipelines.

## Additional Architectural Strengths

### 2. Composability

ComfyUI nodes are **isolated units**. You can't easily nest workflows, create higher-order nodes, or abstract patterns.

doeff-flow workflows compose like functions:

```hy
;; Nest workflows inside other workflows
(defgraph preprocess [image]
  (-> image (resize 512 512) (normalize)))

(defgraph generate [image prompt]
  (let [preprocessed (yield (preprocess image))  ; nested workflow!
        result (yield (sample preprocessed prompt))]
    result))

;; Higher-order: node that wraps another with retry logic
(defn with-retry [node-fn retries]
  (defnode retry-wrapper [& args]
    (loop [attempts retries]
      (let [result (yield (Try (apply node-fn args)))]
        (if (or (.is-ok result) (zero? attempts))
          (.unwrap result)
          (recur (dec attempts)))))))
```

### 3. Control Flow

ComfyUI has no native loops, conditionals, or recursion — requires hacky "loop" nodes.

doeff-flow has full language control flow:

```hy
(defgraph iterative-refinement [image prompt iterations]
  (loop [current image
         i 0]
    (if (>= i iterations)
      current
      (let [refined (yield (refine current prompt))]
        (recur refined (inc i))))))

(defgraph conditional-workflow [image]
  (let [analysis (yield (analyze image))]
    (if (> (:quality analysis) 0.8)
      image                        ; good enough
      (yield (enhance image)))))   ; needs enhancement
```

### 4. Debugging & Observability

doeff provides rich introspection out of the box:

| Feature | What It Does |
|---------|--------------|
| `EffectCallTree` | Hierarchical view of which `@do` functions produced which effects |
| `EffectObservation` | Record of every effect executed |
| `EffectCreationContext` | File, line, function where effect was created |
| `RuntimeResult` | Full execution trace, logs, state, graph |
| `WGraph` visualization | vis.js HTML output of computation graph |

```python
result = await runtime.run(workflow)

# See exactly what happened
print(result.call_tree.visualize_ascii())
# └─ txt2img()
#    ├─ load_model('sdxl')
#    │  └─ LoadModel('sdxl')
#    ├─ encode('a cat')
#    │  └─ Encode(...)
#    └─ sample(...)
#       └─ Sample(...) x20

# Where did the error originate?
print(result.error.creation_context)
# File "workflow.hy", line 42, in sample
```

ComfyUI: Console logs. Good luck tracing errors.

### 5. Deterministic Replay

Since effects are data, executions can be recorded and replayed:

```python
# Record execution
result = await runtime.run(workflow)
recorded_effects = result.effect_observations

# Replay with same effect results — NO GPU!
replay_handlers = make_replay_handlers(recorded_effects)
replayed = await AsyncRuntime(handlers=replay_handlers).run(workflow)

assert replayed.value == result.value  # Deterministic!
```

Use cases:
- **Bug reproduction** — replay exact sequence that caused crash
- **Time-travel debugging** — step through effect by effect
- **Golden master testing** — record once, replay forever

### 6. Multiple Runtimes

| Runtime | Use Case |
|---------|----------|
| `AsyncRuntime` | Production with real GPU |
| `SyncRuntime` | Simple scripts, blocking |
| `SimulationRuntime` | Test with fake/controlled time |

```python
# Test timeout behavior without actually waiting
sim_runtime = SimulationRuntime()

async def test_timeout():
    workflow = with_timeout(long_workflow, seconds=30)
    sim_runtime.advance_time(31)  # Instant!
    
    result = await sim_runtime.run(workflow)
    assert result.is_err()
    assert "timeout" in str(result.error)
```

ComfyUI: One runtime. Real time. Real waiting.

### 7. Effect Interception (Middleware)

Add cross-cutting concerns without modifying nodes:

```hy
;; Add logging to every effect
(defn with-logging [program]
  (.intercept program
    (fn [effect]
      (print f"Executing: {effect}")
      effect)))

;; Add metrics collection
(defn with-metrics [program metrics-client]
  (.intercept program
    (fn [effect]
      (.increment metrics-client (type effect))
      effect)))

;; Add retry logic to all IO effects
(defn with-retry [program]
  (.intercept program
    (fn [effect]
      (if (io-effect? effect)
        (retry-effect effect :attempts 3)
        effect))))

;; Compose middleware
(-> workflow
    (with-logging)
    (with-metrics prometheus)
    (with-retry))
```

ComfyUI: No middleware. Every node handles its own concerns.

### 8. Dependency Injection

Clean configuration via the `Ask` effect:

```hy
(defgraph my-workflow []
  (let [model-path (yield (Ask "model_path"))
        api-key (yield (Ask "api_key"))
        cache-dir (yield (Ask "cache_dir"))]
    ...))

;; Inject different configs per environment
(runtime.run workflow :env {"model_path" "/prod/models" 
                            "api_key" (get-secret "PROD_KEY")})

(runtime.run workflow :env {"model_path" "/test/models"
                            "api_key" "test-key"})
```

ComfyUI: Hardcoded paths or global configuration.

### 9. Incremental Execution

Like a build system (Make, Bazel) — only re-run what changed:

```python
result1 = await runtime.run(workflow, inputs={"prompt": "a cat"})
# Executes: load_model → encode → sample → decode

result2 = await runtime.run(workflow, inputs={"prompt": "a dog"})  
# Executes: encode → sample → decode  (model already cached!)

result3 = await runtime.run(workflow, inputs={"prompt": "a dog"})
# Executes: nothing (fully cached!)
```

doeff caching advantages over ComfyUI:
- **Content-addressed** — hash of actual inputs
- **Explicit** — `@cache` decorator with configurable policy
- **Inspectable** — can query what's cached and why

### 10. IDE Support

Since workflows are just Python/Hy functions:

| Feature | doeff-flow | ComfyUI |
|---------|------------|---------|
| Autocomplete | ✅ | ❌ JSON blob |
| Type checking | ✅ | ❌ String types |
| Go to definition | ✅ | ❌ |
| Find references | ✅ | ❌ |
| Refactoring | ✅ | ❌ |
| Linting | ✅ | ❌ |

### 11. Version Control

```diff
# doeff-flow: Meaningful, reviewable diffs
(defgraph txt2img
  :nodes {
    model (load-model "sdxl")
-   steps (slider :default 20)
+   steps (slider :default 30)  ; Increased for quality
  })
```

```diff
# ComfyUI: Opaque ID-based diffs
- "3": {"inputs": {"steps": 20, ...}}
+ "3": {"inputs": {"steps": 30, ...}}
# What is node "3"? Context lost.
```

### 12. Serialization & Distribution

Programs are values — can be serialized and distributed:

```python
# Serialize workflow
serialized = cloudpickle.dumps(workflow)

# Send to remote worker pool
redis.publish("gpu-jobs", serialized)

# Worker receives and executes
workflow = cloudpickle.loads(message)
result = await runtime.run(workflow)
```

ComfyUI: Tightly coupled to its server architecture.

## Full Strength Comparison

| Strength | ComfyUI | doeff-flow |
|----------|---------|------------|
| **Testability** | ❌ GPU required | ✅ Mock handlers |
| **Composability** | ❌ Flat nodes | ✅ Nested workflows |
| **Control flow** | ❌ No loops/conditionals | ✅ Full Hy/Python |
| **Debugging** | ❌ Console logs | ✅ Call tree, traces |
| **Replay** | ❌ Not possible | ✅ Record/playback |
| **Multiple runtimes** | ❌ One runtime | ✅ Sync/Async/Sim |
| **Middleware** | ❌ None | ✅ Effect interception |
| **Dependency injection** | ❌ Global config | ✅ Ask effect |
| **Incremental execution** | ⚠️ Basic cache | ✅ Content-addressed |
| **IDE support** | ❌ JSON blobs | ✅ Full support |
| **Version control** | ❌ ID soup | ✅ Named symbols |
| **Distribution** | ❌ Server-coupled | ✅ Serializable |

## Implementation Plan

### Phase 1: Core Macros (Hy Layer)

- [ ] `defeffect` macro — effect with UI widget metadata
- [ ] `defnode` macro — node definition with schema extraction
- [ ] `defgraph` macro — declarative graph definition
- [ ] Node registry — collect all defined nodes
- [ ] Schema extraction — generate JSON from macros

### Phase 2: Runtime Integration

- [ ] Graph-aware runtime — execute with node-level granularity
- [ ] Event emission — hook into effect execution
- [ ] Progress reporting — for long-running operations
- [ ] Node-level caching — skip unchanged nodes

### Phase 3: Server Layer

- [ ] `/api/nodes` — GET available node definitions
- [ ] `/api/graphs` — GET/POST workflow definitions  
- [ ] `/api/run` — POST workflow, returns execution ID
- [ ] `/ws/events` — WebSocket stream of execution events

### Phase 4: Frontend

- [ ] Evaluate options: litegraph.js, rete.js, react-flow
- [ ] Node rendering from schema
- [ ] Real-time execution feedback
- [ ] Workflow save/load
- [ ] Code export (Hy/Python)

## Package Structure

```
packages/doeff-flow/
├── src/doeff_flow/
│   ├── __init__.py
│   ├── hy/
│   │   ├── macros.hy        # defnode, defeffect, defgraph
│   │   ├── widgets.hy       # widget type definitions
│   │   └── prelude.hy       # common imports
│   ├── registry.py          # node/effect registry
│   ├── schema.py            # JSON schema generation
│   ├── runtime.py           # graph-aware executor
│   ├── events.py            # event emission
│   └── server/
│       ├── __init__.py
│       ├── api.py           # REST endpoints
│       └── ws.py            # WebSocket handler
├── frontend/                # or separate package
│   ├── package.json
│   └── src/
└── examples/
    ├── nodes/
    │   ├── image.hy
    │   ├── text.hy
    │   └── sampling.hy
    └── workflows/
        └── txt2img.hy
```

## What doeff Already Provides

| Need | doeff Has |
|------|-----------|
| Lazy execution | `Program` is lazy |
| Effect interception | `.intercept()` |
| Caching | `@cache` decorator |
| Parallel execution | `Gather` effect |
| Graph tracking | `WGraph`, `WStep`, `WNode` |
| Async runtime | `AsyncRuntime` |
| Effect observation | `EffectObservation` |
| Call tree | `EffectCallTree` |
| Visualization | `graph_snapshot.py` → vis.js |

## Open Questions

1. **Frontend choice**: litegraph.js (ComfyUI's choice), rete.js, or react-flow?
2. **Hy integration**: Require Hy runtime or compile to pure Python?
3. **Type system**: How strict should node type checking be?
4. **Persistence**: How to store workflows (files, database)?
5. **Collaboration**: Real-time multi-user editing?

## Future Directions

The current doeff + Hy design is a pragmatic sweet spot, but there are more advanced architectures worth considering for future evolution:

### 1. Content-Addressed Everything (Merkle DAG)

Make every node, edge, and value content-addressed by its hash, like Git/IPFS/Unison:

```
Current (name-based):
  load_model("sdxl") → cache key = ("load_model", "sdxl")
  Problem: If load_model code changes, cache is stale

Better (content-addressed):
  Node hash = sha256(node_code + dependencies + inputs)
  
  Benefits:
  - Code change → hash change → auto-invalidate
  - Identical computations across workflows → deduplicated
  - Shareable cache across users (like Nix store)
  - Immutable history (like Git)
  - Distributed storage (like IPFS)
```

**References**: [Unison](https://www.unison-lang.org/), [Nix](https://nixos.org/), [IPFS](https://ipfs.io/)

### 2. Incremental/Reactive Computation

Instead of "run workflow, skip cached nodes," adopt a **spreadsheet model** where cells automatically update when dependencies change:

```hy
;; Current: Pull-based (run workflow to get result)
(def result (run workflow))

;; Future: Push-based (result updates when inputs change)
(def model (cell (load-model "sdxl")))
(def prompt (cell "a cat"))
(def image (derived [model prompt] 
              (generate model prompt)))

;; Change prompt → image automatically recomputes
(reset! prompt "a dog")
;; Only encode + sample + decode run
```

**Benefit**: Real-time preview as you tweak parameters in the UI.

**References**: [Incremental (OCaml)](https://github.com/janestreet/incremental), [Adapton](http://adapton.org/), [Salsa (Rust)](https://github.com/salsa-rs/salsa)

### 3. Projectional Editing

Instead of bidirectional sync between text and UI (which can be lossy), make the **AST the single source** with multiple views:

```
Current: Bidirectional sync (lossy)
  Hy Code ←→ Visual UI
  Comments lost. Formatting lost. Sync bugs.

Future: Projectional editing
                  ┌─────────────┐
                  │    AST      │  ← Single source
                  └─────────────┘
                   /           \
                  ↓             ↓
             ┌────────┐    ┌────────┐
             │Text    │    │Visual  │  ← Views (projections)
             │View    │    │View    │
             └────────┘    └────────┘

  Edit in either view → updates AST → other view updates
  No sync issues. Perfect round-trip.
```

**References**: [JetBrains MPS](https://www.jetbrains.com/mps/), [Hazel](https://hazel.org/), [Dark](https://darklang.com/)

### 4. Multi-Shot Continuations

doeff uses algebraic effects with one-shot continuations. Multi-shot continuations would enable even more composable handling:

```hy
;; Current: Global handler for effect type
(runtime.run workflow :handlers {Sample: my_handler})

;; Future: Scoped, composable handlers
(with-handler [Sample my_handler]
  (with-handler [Log silent_handler]  ; nested scope!
    (run inner-workflow))
  (run outer-workflow))  ; different handler
```

Multi-shot continuations would enable:
- **Multi-shot handlers** — run continuation multiple times (doeff currently supports one-shot only)
- **Backtracking** — explore multiple branches from a single effect point
- **Non-determinism** — natively model choice

**References**: [Eff](https://www.eff-lang.org/), [Koka](https://koka-lang.github.io/), [OCaml 5 effects](https://v2.ocaml.org/manual/effects.html)

### 5. Typed Holes

Allow incomplete workflows with "holes" that show what's needed:

```hy
(defgraph my-workflow []
  (let [model (yield (load-model "sdxl"))
        image (yield (generate model ???))]  ; ← typed hole
    image))

;; System infers: ??? must be type Prompt
;; UI shows: "Need Prompt input here"
;; Can run partial workflow up to the hole
```

**References**: [Hazel](https://hazel.org/), [Idris](https://www.idris-lang.org/)

### 6. Provenance Tracking

Track where every value came from for debugging and explainability:

```python
result = await runtime.run(workflow)

# Query provenance of any output
result.provenance(output_image.pixel[100, 100])
# → "This pixel came from:
#    → decode(latent[50,50])
#    → sample(step=17, noise=0.3)  
#    → encode('detailed fur texture')
#    → Original prompt word 'fur' at position 3"
```

### Implementation Roadmap

| Phase | Enhancement | Effort | Impact |
|-------|-------------|--------|--------|
| **Phase 1** | Ship current doeff + Hy design | Done | ★★★★★ |
| **Phase 2** | Content-addressed cache layer | Medium | ★★★★☆ |
| **Phase 3** | Incremental computation for preview | High | ★★★★☆ |
| **Phase 4** | Typed holes for partial workflows | Medium | ★★★☆☆ |
| **Research** | Multi-shot continuations | Very High | ★★★☆☆ |
| **Research** | Projectional editing | Very High | ★★★☆☆ |

### Design Principle

> The enemy of shipped is perfect.

The current design is **principled enough** to be extensible toward these ideals without requiring them upfront. Each enhancement can be added incrementally without rewriting the foundation.

## References

### Core Technologies
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — practical but ad-hoc node editor
- [Hy](https://github.com/hylang/hy) — Lisp dialect embedded in Python
- [litegraph.js](https://github.com/jagenjo/litegraph.js) — graph editor library

### doeff Primitives
- `doeff/graph_snapshot.py` — existing vis.js integration
- `doeff/types.py` — `WGraph`/`WStep`/`WNode` DAG primitives
- `doeff/effects/` — effect system foundation

### Future Directions Research
- [Unison](https://www.unison-lang.org/) — content-addressed code
- [Nix](https://nixos.org/) — content-addressed builds
- [Adapton](http://adapton.org/) — incremental computation
- [Salsa](https://github.com/salsa-rs/salsa) — incremental compilation (Rust)
- [Hazel](https://hazel.org/) — typed holes, live programming
- [Koka](https://koka-lang.github.io/) — algebraic effects
- [JetBrains MPS](https://www.jetbrains.com/mps/) — projectional editing

## Conclusion

This design achieves what ComfyUI does but with principled foundations:

- **Single source of truth** — Hy macros generate both runtime and UI
- **Clean separation** — effects are data, UI is derived
- **Composable** — it's just doeff programs
- **Testable** — pure functions, no runtime required
- **Version-controllable** — readable, diffable code

The elegance comes from doeff's existing architecture. We're not fighting the abstraction — we're working with it.
