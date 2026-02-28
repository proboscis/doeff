# Domain Angle: Gradio / Streamlit / ML Prototyping

The Gradio/Streamlit community builds quick ML demos. doeff's value here is NOT dependency injection (any DI framework can swap implementations per environment). The value is that **effects make the pipeline inspectable, pausable, and forkable** — turning Gradio into an interactive debugger for ML workflows.

## What Effects Uniquely Enable (DI Cannot)

| Capability | With DI | With Effects |
|-----------|---------|-------------|
| Swap services per environment | Yes (trivially) | Yes (trivially) |
| **Stream intermediate results to UI** | Callbacks/observers threaded through every service | **Orthogonal handler — program doesn't know about UI** |
| **Pause execution between steps** | Impossible — function calls are opaque | **Handler controls when to resume** |
| **Fork from any intermediate step** | Impossible — no way to "replay up to here" | **Replay handler + override from any yield** |
| **Share full execution trace** | Logging (text only) | **Effect trace with all inputs/outputs as artifact** |

The first row is DI. Rows 2-5 are effects. This doc focuses exclusively on rows 2-5.

## 1. Effect Trace as Live UI

Every `yield` is a capturable event. A stacked handler can push each effect to the Gradio UI in real-time without the program knowing:

```python
@do
def style_transfer(content_img, style_img):
    yield Tell("Encoding content image...")
    content_features = yield ExtractFeatures(content_img, model="vgg19", layers=["conv4_2"])

    yield Tell("Encoding style image...")
    style_features = yield ExtractFeatures(style_img, model="vgg19", layers=["conv1_1", "conv2_1", "conv3_1"])

    yield Tell("Optimizing...")
    for step in range(100):
        loss = yield OptimizationStep(content_features, style_features, step)
        yield Tell({"step": step, "loss": loss.value})
        if step % 10 == 0:
            yield Snapshot(f"step_{step}", loss.current_image)

    return loss.current_image
```

```python
from doeff import Delegate, Effect, do

# Handler that streams effects to Gradio — orthogonal to the pipeline
@do
def gradio_streaming_handler(effect: Effect, k):
    if isinstance(effect, Tell):
        if isinstance(effect.value, str):
            gradio_log_queue.put(effect.value)        # -> live log panel
        elif isinstance(effect.value, dict):
            gradio_metrics_queue.put(effect.value)    # -> live loss chart
    elif isinstance(effect, Snapshot):
        gradio_image_queue.put(effect.image)         # -> live preview gallery
    yield Delegate()  # let the real handler execute the effect too
```

The streaming handler stacks on top of any program. The program has zero Gradio imports. Adding live UI to an existing pipeline = one handler, zero code changes to the pipeline.

With DI, you'd pass a `ProgressCallback` or `UIObserver` into every service, threading it through function signatures. That's invasive. With effects, it's orthogonal.

## 2. Interactive Step-Through Execution

A handler can **pause** at any yield and let the user inspect, modify, and continue:

```python
@do
def image_pipeline(image):
    denoised = yield Denoise(image, strength=0.5)
    upscaled = yield Upscale(denoised, factor=4)
    colorized = yield Colorize(upscaled, palette="warm")
    return colorized
```

```python
from doeff import Delegate, Effect, do

@do
def interactive_step_handler(effect: Effect, k):
    """Pause at each effect. Show preview. Let user adjust parameters."""
    gradio_step_display.update(f"Next: {type(effect).__name__}")
    gradio_preview.update(getattr(effect, 'input_image', None))

    # Block until user clicks "Continue" or adjusts parameters
    user_params = gradio_param_queue.get()

    if user_params:
        modified = effect.with_params(**user_params)
        yield Delegate(modified)
    else:
        yield Delegate()
```

The Gradio UI becomes a pipeline debugger:

```
+--------------------------------------------------+
| Pipeline Step: Upscale                            |
| [Preview: denoised image]                         |
|                                                   |
| Factor: [====O=====] 4                            |
|                                                   |
| [<< Back]  [Run Step]  [Skip >>]  [Run All >>]   |
+--------------------------------------------------+
| Effect Trace:                                     |
|  1. Denoise(strength=0.5)  -> [thumb]  DONE       |
|  2. Upscale(factor=4)      -> [thumb]  CURRENT    |
|  3. Colorize(palette=warm) ->          PENDING     |
+--------------------------------------------------+
```

**DI cannot do this.** With DI, `denoise()` calls `upscale()` inside a function body — there's no seam to pause between them. With effects, each `yield` IS a seam.

## 3. Fork-and-Explore from Any Step

The user runs the pipeline, sees the result, and thinks "what if I used factor=2 instead of 4?" With effects, you replay UP TO that step and diverge — without re-running expensive earlier steps:

```python
# Replay Denoise from recording, override Upscale params, continue live
result = run(image_pipeline(image), handlers=[
    ReplayHandler("trace.json", replay_until="Upscale"),  # instant
    OverrideHandler(Upscale, {"factor": 2}),              # modify
    RealHandler(),                                         # continue
])
```

In the Gradio UI:

```
Effect Trace (interactive):
  1. Denoise(0.5)  -> [result]  [locked - from recording, instant]
  2. Upscale(4)    -> [result]  [click to fork]
     |                            |
     |                            v
     +-- Fork A: Upscale(2) -> [new result]
     |       3. Colorize(warm) -> [new result]
     |
     +-- Fork B: Upscale(8) -> [new result]
             3. Colorize(cool) -> [new result]
```

The user explores a parameter tree. Each branch replays the cheap steps and only re-runs from the fork point. For a pipeline where step 1 takes 5 minutes on a GPU, this saves 5 minutes per exploration branch.

**DI cannot do this.** There's no concept of "replay the first N function calls from a recording and then switch to live execution."

## 4. Effect Trace as Shareable Artifact

The recorded effect trace isn't just a log — it's the full execution with all inputs and outputs:

```python
# Researcher A runs the pipeline, records everything
result = run(style_transfer(content, style), handlers=[
    RecordingHandler("experiments/run_42.json"),
    *gpu_handlers,
])
# run_42.json contains: every effect, every intermediate tensor, every parameter

# Researcher B opens the trace in their own Gradio app — no GPU needed
result = run(style_transfer(content, style), handlers=[
    InteractiveReplayHandler("experiments/run_42.json"),
])
# They can browse every intermediate result, fork, modify parameters
```

The trace file IS the experiment. Not just metrics — the full sequence of operations. Other researchers explore it interactively without access to the original GPU/model/data.

## 5. Conference Demo Insurance

This one is simple but high-value for the Gradio community:

```python
# Before the conference: record your demo session
run(demo_pipeline(inputs), handlers=[
    RecordingHandler("demo_session.json"),
    *real_handlers,
])

# At the conference: replay mode
run(demo_pipeline(inputs), handlers=[
    ReplayHandler("demo_session.json"),
])
# Works on airplane WiFi. No API key. No GPU. Same outputs.
```

Every ML researcher has been burned by a live demo failing. With effects, the demo is deterministic.

(Note: this specific capability IS achievable with DI by pre-computing and caching results. But the effect-based version is automatic — record once, replay forever, for ANY program, without modifying the program.)

## The Pitch for Gradio/Streamlit Users

> "Your Gradio app calls a pipeline. Right now that pipeline is a black box — you click a button and wait. With doeff, every step of the pipeline is a yield. That means Gradio can show you each intermediate result as it happens, let you pause and adjust parameters mid-pipeline, fork from any step to explore alternatives, and share the full execution trace with collaborators. Your demo becomes a debugger."

## What This Is NOT

This is NOT about:
- Swapping services per environment (DI does this fine)
- Testing with mocks (covered in [three-stage-pitch.md](../three-stage-pitch.md))
- Cleaner code (covered in [three-stage-pitch.md](../three-stage-pitch.md))

This IS about the **interactive inspection and exploration** that only effects enable — because every yield is a pausable, recordable, replayable seam in the execution.
