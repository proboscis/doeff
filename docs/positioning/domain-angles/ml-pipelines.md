# Domain Angle: ML Pipelines

doeff's home turf. The archpainter codebase (100k+ LOC, pinjected-based, multi-year ML research) is the canonical migration story.

## The Pain

ML pipelines have the most side effects of any Python domain:
- GPU compute (expensive, slow)
- API calls (LLM, image generation, embeddings)
- WandB / MLflow logging
- Model checkpointing (disk, cloud storage)
- Dataset loading (HDF5, LMDB, S3)
- Distributed execution (Ray, Dask, Celery)

Testing these pipelines is effectively impossible. Most ML repos have near-zero test coverage because mocking GPU compute + API calls + distributed execution is too painful.

## The archpainter Story

archpainter uses pinjected (doeff's predecessor) for DI across 328 files. The side effect catalog:

```
Current (pinjected/imperative)          ->  doeff (yields)
---------------------------------------------------------------
Injected.bind(load_model)              ->  model = yield Ask("model")
wandb.log(metrics)                     ->  yield Tell(metrics)
ray.remote(fn).remote(args)            ->  result = yield Spawn(fn, args)
cache.get(key) / cache.set(key, val)   ->  val = yield CacheGet(key)
torch.load(path)                       ->  ckpt = yield Await(load_ckpt(path))
Injected.mzip(a, b, c)                ->  a, b, c = yield Gather(prog_a, prog_b, prog_c)
```

## The Killer Demo: Experiment Replay

### Record a training experiment:

```python
result = run(train_experiment(), handlers=[
    RecordingHandler("runs/exp_2026_02_12.json"),
    RealGPUHandler(),
    WandBHandler(),
    OpenAIHandler(),
])
# Cost: $50 in API calls, 4 hours of GPU time
```

### Replay to re-analyze with different metrics ($0, instant):

```python
result = run(evaluate_model(), handlers=[
    ReplayHandler("runs/exp_2026_02_12.json"),  # replays all IO
    NewMetricsHandler(),                         # computes new metrics
])
# Cost: $0. No GPU. No API calls. Instant.
```

### Replay 100 variations for hyperparameter analysis:

```python
for lr in [0.001, 0.01, 0.1]:
    result = run(train_experiment(), handlers=[
        ReplayHandler("runs/exp_2026_02_12.json"),
        OverrideHandler({"learning_rate": lr}),
    ])
# "This saved us $4,950 in API costs"
```

## The Conference Talk

**Title:** "Replay Any ML Experiment Without GPU: Algebraic Effects for Research Pipelines"

**Abstract:** Training runs cost hours and dollars. Debugging requires re-running. We recorded all side effects of a 100k-LOC image generation pipeline as algebraic effects, enabling instant replay, metric re-analysis, and hyperparameter sweeps — all without GPU or API access. We'll show the migration from pinjected to doeff and the 50x cost reduction in experiment iteration.

## Migration Path from pinjected

| Phase | What changes | Effort | Impact |
|-------|-------------|--------|--------|
| 1. Core DI | `Injected.bind()` -> `Ask` effects | High | Foundation |
| 2. Compute | `ray.remote` -> `Spawn`/`Gather` | Medium | Distributed story |
| 3. IO | WandB, LMDB, HDF5, image IO -> effects | Medium | Testability |
| 4. Recording | Add `RecordingHandler` | Low | The killer demo |
| 5. Replay demos | Show re-analysis without GPU | Low | The conference talk |

## Competing Solutions in ML

| Tool | What it does | What it doesn't do |
|------|-------------|-------------------|
| **MLflow** | Tracks metrics, artifacts, models | Doesn't capture effect-level IO for replay |
| **WandB** | Experiment logging, comparison | No replay of actual computation |
| **DVC** | Data versioning, pipeline DAG | DAG-level, not effect-level |
| **Hydra** | Config management | No effect system, no replay |
| **doeff** | All effects as yields, recording, replay, composition | Requires paradigm shift |

doeff doesn't replace these tools — it composes with them. WandB becomes a handler. MLflow becomes a handler. The recording layer sits underneath everything.
