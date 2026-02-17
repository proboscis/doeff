# Domain Angle: ML Infrastructure — From Constructor Injection Hell to Zero-Parameter Effects

ML infrastructure is the "get my code running on that GPU machine" problem. You have a Python project with dependencies. You need it running in a Docker container on a remote host with GPUs. You need file sync, environment detection, image building, kubectl commands, rsync to pods, GCS transfers, NFS mounts.

This is the domain where dependency injection shows both its power and its ceiling.

## Real-World Evidence: ml-nexus

**ml-nexus** is an ML environment orchestration library built entirely on **pinjected** (a DI framework). It:
- Auto-detects project structure (UV, Rye, pip, setup.py)
- Builds Docker images with correct Python version and dependencies
- Deploys to local/remote Docker hosts, GCE/Vertex AI, Kubernetes
- Manages file sync (rsync, krsync, GCS)
- Handles NFS mount planning and GPU-as-a-Service backends

Every function uses `@injected` with dependencies before `/` and runtime args after. The codebase demonstrates both the strengths of DI and the pain points that algebraic effects solve.

## Why This Is NOT Just DI

ml-nexus already uses sophisticated dependency injection. What DI **cannot** do:

1. **Eliminate constructor injection explosion** — 15-parameter constructors (`_a_system`, `_logger`, `_a_kubectl`, `_a_docker_push`, ...) thread through every layer
2. **Make retry a cross-cutting handler** — 5 nearly-identical retry loops copy-pasted across kubectl, ai-platform, krsync, SSH
3. **Eliminate "silent" variant duplication** — `a_execute_kubectl` / `a_execute_kubectl_silent` duplicate entire functions just to toggle logging
4. **Test without infrastructure** — tests require real K8s, real Docker, real GCS (the platform adapter's CLAUDE.md says "testing without MLPlatform is nonsense")
5. **One setup function for every environment** — 6 job runner classes each reimplement the same 6-phase pipeline (build → push → sync → deploy → run → collect) with 10-20 constructor parameters each
6. **Avoid the `returns` library escape hatch** — `Future`, `FutureResultE`, `IOResultE` constantly escape with `unsafe_perform_io` (algebraic effects trying to happen through a library)
7. **Make event bus automatic** — `ml_nexus_system_call_event_bus` manually called at every system call site (`SystemCallStart`, `SystemCallEnd`, `SystemCallStdOut`, `SystemCallStdErr`) — this is what effect handlers do automatically

This document shows the evolution from pinjected to doeff — not as a replacement, but as the next step.

## Pain Point 1: Constructor Injection Explosion

### The Problem (pinjected)

```python
@injected
def a_persistent_ml_platform_job_core(
    _a_system: Injected,
    _logger: Injected,
    _a_kubectl: Injected,
    _a_docker_push: Injected,
    _a_build_image: Injected,
    _a_rsync_to_pod: Injected,
    _a_gcs_upload: Injected,
    _a_nfs_mount_planner: Injected,
    _a_pod_status_checker: Injected,
    _a_log_streamer: Injected,
    _a_cleanup_resources: Injected,
    _a_retry_with_backoff: Injected,
    _a_event_bus: Injected,
    _a_config_validator: Injected,
    _a_secret_manager: Injected,
    /,
    job_spec: JobSpec,
    mount_plan: MountPlan,
) -> FutureResultE[JobResult]:
    """
    15 injected dependencies before the slash.
    Every layer that calls this needs all 15 injected.
    Every test needs to mock all 15.
    """
    # Emit event
    _a_event_bus(SystemCallStart(cmd="kubectl apply", context=job_spec))
    
    # Validate config
    validation_result = _a_config_validator(job_spec)
    if validation_result.is_err():
        return FutureResultE.from_failure(validation_result.error)
    
    # Build image
    image_result = await _a_build_image(job_spec.dockerfile_path)
    if image_result.is_err():
        _logger.error(f"Image build failed: {image_result.error}")
        return FutureResultE.from_failure(image_result.error)
    
    # Push to registry
    push_result = await _a_docker_push(image_result.value, job_spec.registry)
    # ... 200 more lines using all 15 dependencies
```

Every function that calls this needs all 15 dependencies injected. Every test needs to provide all 15 mocks. The dependency list grows with every new capability.

### The Solution (doeff)

```python
@dataclass(frozen=True)
class SystemCall(Effect):
    cmd: str
    context: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ValidateConfig(Effect):
    spec: JobSpec

@dataclass(frozen=True)
class BuildImage(Effect):
    dockerfile_path: Path

@dataclass(frozen=True)
class PushImage(Effect):
    image: str
    registry: str

@do
def persistent_ml_platform_job_core(
    job_spec: JobSpec,
    mount_plan: MountPlan,
) -> Program[JobResult]:
    """
    Zero injected dependencies.
    Effects are yielded at the call site.
    Handlers provide implementations.
    """
    # Validation happens through effect
    validation = yield ValidateConfig(job_spec)
    
    # Build image
    image = yield BuildImage(job_spec.dockerfile_path)
    
    # Push to registry
    yield PushImage(image, job_spec.registry)
    
    # Deploy to cluster
    pod_name = yield SystemCall(
        cmd=f"kubectl apply -f {job_spec.k8s_manifest}",
        context={"job_id": job_spec.id}
    )
    
    # Sync files
    yield SyncFiles(
        src=job_spec.code_path,
        dst=f"{pod_name}:/workspace",
        method=mount_plan.sync_method
    )
    
    # Wait for completion
    result = yield WaitForPod(pod_name, timeout=3600)
    return result
```

**The function signature has 2 parameters instead of 17.** The 15 services become effects yielded at the call site. Tests provide effect handlers, not constructor mocks.

## Pain Point 2: Retry as Copy-Pasted Code

### The Problem (pinjected)

ml-nexus has 5 nearly-identical retry loops:

```python
@injected
def a_execute_kubectl(_a_system: Injected, _logger: Injected, /, cmd: str) -> FutureResultE[str]:
    """Retry kubectl commands on transient failures."""
    max_retries = 3
    backoff = 1.0
    
    for attempt in range(max_retries):
        try:
            _a_event_bus(SystemCallStart(cmd=f"kubectl {cmd}"))
            result = _a_system(f"kubectl {cmd}")
            _a_event_bus(SystemCallEnd(cmd=f"kubectl {cmd}", stdout=result))
            return FutureResultE.from_value(result)
        except Exception as e:
            if "connection refused" in str(e) or "timeout" in str(e):
                if attempt < max_retries - 1:
                    _logger.warning(f"kubectl retry {attempt + 1}/{max_retries}: {e}")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    _logger.error(f"kubectl failed after {max_retries} attempts")
                    return FutureResultE.from_failure(e)
            else:
                return FutureResultE.from_failure(e)

@injected
def a_execute_ai_platform(_a_system: Injected, _logger: Injected, /, cmd: str) -> FutureResultE[str]:
    """Retry AI Platform commands on transient failures."""
    max_retries = 3
    backoff = 1.0
    
    for attempt in range(max_retries):
        try:
            result = _a_system(f"gcloud ai-platform {cmd}")
            return FutureResultE.from_value(result)
        except Exception as e:
            if "quota exceeded" in str(e) or "rate limit" in str(e):
                if attempt < max_retries - 1:
                    _logger.warning(f"ai-platform retry {attempt + 1}/{max_retries}: {e}")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    return FutureResultE.from_failure(e)
            else:
                return FutureResultE.from_failure(e)

@injected
def a_krsync_to_pod(_a_system: Injected, _logger: Injected, /, src: Path, pod: str) -> FutureResultE[None]:
    """Retry krsync on transient failures."""
    max_retries = 3
    backoff = 2.0
    
    for attempt in range(max_retries):
        try:
            _a_system(f"krsync -av {src} {pod}:/workspace")
            return FutureResultE.from_value(None)
        except Exception as e:
            if "connection reset" in str(e) or "broken pipe" in str(e):
                if attempt < max_retries - 1:
                    _logger.warning(f"krsync retry {attempt + 1}/{max_retries}: {e}")
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    return FutureResultE.from_failure(e)
            else:
                return FutureResultE.from_failure(e)
```

**Same structure, different error strings, copy-pasted 5 times.** Adding a new retry strategy (circuit breaker, jitter) requires editing 5 functions.

### The Solution (doeff)

```python
@dataclass(frozen=True)
class SystemCall(Effect):
    cmd: str
    context: dict = field(default_factory=dict)

@do
def execute_kubectl(cmd: str) -> Program[str]:
    """No retry logic here — that's a handler concern."""
    return (yield SystemCall(f"kubectl {cmd}", context={"tool": "kubectl"}))

@do
def execute_ai_platform(cmd: str) -> Program[str]:
    return (yield SystemCall(f"gcloud ai-platform {cmd}", context={"tool": "ai-platform"}))

@do
def krsync_to_pod(src: Path, pod: str) -> Program[None]:
    yield SystemCall(f"krsync -av {src} {pod}:/workspace", context={"tool": "krsync"})

# Retry is a handler that wraps any SystemCall
def retry_handler(max_retries=3, backoff=1.0, transient_errors=None):
    """Retry handler for SystemCall effects."""
    transient_errors = transient_errors or ["connection refused", "timeout", "rate limit"]
    
    def handler(effect, k):
        if not isinstance(effect, SystemCall):
            yield Delegate()
            return
        
        attempt = 0
        current_backoff = backoff
        
        while attempt < max_retries:
            try:
                result = yield Delegate()  # try the actual system call
                return result
            except Exception as e:
                if any(err in str(e).lower() for err in transient_errors):
                    if attempt < max_retries - 1:
                        yield Tell(f"Retry {attempt + 1}/{max_retries} for {effect.cmd}: {e}")
                        yield Delay(current_backoff)
                        current_backoff *= 2
                        attempt += 1
                    else:
                        yield Tell(f"Failed after {max_retries} attempts: {effect.cmd}")
                        raise
                else:
                    raise  # non-transient error, fail immediately
    
    return handler

# Use it
result = run(
    execute_kubectl("get pods"),
    handlers=[
        retry_handler(max_retries=3, backoff=1.0),
        system_call_handler(),
    ]
)
```

**Retry logic written once, applies to all SystemCall effects.** Want circuit breaker? Add a circuit breaker handler. Want jitter? Modify the retry handler once. The business logic (`execute_kubectl`, `krsync_to_pod`) never changes.

## Pain Point 3: "Silent" Variant Elimination

### The Problem (pinjected)

ml-nexus duplicates entire functions just to toggle logging:

```python
@injected
def a_execute_kubectl(_a_system: Injected, _logger: Injected, /, cmd: str) -> FutureResultE[str]:
    """Execute kubectl with logging."""
    _logger.info(f"Executing: kubectl {cmd}")
    _a_event_bus(SystemCallStart(cmd=f"kubectl {cmd}"))
    result = _a_system(f"kubectl {cmd}")
    _logger.info(f"kubectl stdout: {result}")
    _a_event_bus(SystemCallStdOut(cmd=f"kubectl {cmd}", stdout=result))
    return FutureResultE.from_value(result)

@injected
def a_execute_kubectl_silent(_a_system: Injected, /, cmd: str) -> FutureResultE[str]:
    """Execute kubectl without logging (for polling loops)."""
    result = _a_system(f"kubectl {cmd}")
    return FutureResultE.from_value(result)

@injected
def a_execute_ai_platform(_a_system: Injected, _logger: Injected, /, cmd: str) -> FutureResultE[str]:
    """Execute AI Platform command with logging."""
    _logger.info(f"Executing: gcloud ai-platform {cmd}")
    result = _a_system(f"gcloud ai-platform {cmd}")
    _logger.info(f"ai-platform stdout: {result}")
    return FutureResultE.from_value(result)

@injected
def a_execute_ai_platform_silent(_a_system: Injected, /, cmd: str) -> FutureResultE[str]:
    """Execute AI Platform command without logging."""
    result = _a_system(f"gcloud ai-platform {cmd}")
    return FutureResultE.from_value(result)
```

**Every function has a `_silent` variant.** The logic is identical, only logging differs. This doubles the API surface and creates maintenance burden.

### The Solution (doeff)

```python
@do
def execute_kubectl(cmd: str) -> Program[str]:
    """One function. Logging is a handler concern."""
    return (yield SystemCall(f"kubectl {cmd}"))

@do
def execute_ai_platform(cmd: str) -> Program[str]:
    return (yield SystemCall(f"gcloud ai-platform {cmd}"))

# Logging handler
def logging_handler():
    def handler(effect, k):
        if isinstance(effect, SystemCall):
            yield Tell(f"Executing: {effect.cmd}")
            result = yield Delegate()
            yield Tell(f"Result: {result}")
            return result
        yield Delegate()
    return handler

# Silent mode: just don't include the logging handler
result_verbose = run(
    execute_kubectl("get pods"),
    handlers=[logging_handler(), system_call_handler()]
)

result_silent = run(
    execute_kubectl("get pods"),
    handlers=[system_call_handler()]  # no logging handler
)
```

**One function, not two.** Logging is composed through handlers. Polling loops use the silent handler stack. User-facing commands use the verbose stack. The business logic never changes.

## Pain Point 4: Testability Without Infrastructure

### The Problem (pinjected)

From ml-nexus's platform adapter CLAUDE.md:

> "Testing without MLPlatform is nonsense. Tests require real K8s, real Docker, real GCS."

```python
def test_job_deployment():
    """Requires real Kubernetes cluster."""
    # Setup: create test namespace, deploy test pod
    kubectl("create namespace test-ml-nexus")
    
    # Test
    result = a_persistent_ml_platform_job_core(
        job_spec=test_job_spec,
        mount_plan=test_mount_plan,
    )
    
    # Verify: check pod exists
    pods = kubectl("get pods -n test-ml-nexus")
    assert "test-job" in pods
    
    # Cleanup: delete namespace
    kubectl("delete namespace test-ml-nexus")
```

**Tests are integration tests.** They're slow, flaky, and require infrastructure. Unit testing the orchestration logic is impossible because it's coupled to real kubectl/docker/gcs calls.

### The Solution (doeff)

```python
def test_job_deployment():
    """No infrastructure required."""
    # Stub handlers return fake responses
    def stub_system_call_handler():
        def handler(effect, k):
            if isinstance(effect, SystemCall):
                if "kubectl apply" in effect.cmd:
                    return "pod/test-job created"
                elif "kubectl get pods" in effect.cmd:
                    return "test-job   Running"
            yield Delegate()
        return handler
    
    def stub_sync_handler():
        def handler(effect, k):
            if isinstance(effect, SyncFiles):
                return None  # sync succeeded
            yield Delegate()
        return handler
    
    # Run with stub handlers
    result = run(
        persistent_ml_platform_job_core(
            job_spec=test_job_spec,
            mount_plan=test_mount_plan,
        ),
        handlers=[
            stub_system_call_handler(),
            stub_sync_handler(),
        ]
    )
    
    # Verify orchestration logic
    assert result.value.status == "completed"
    assert result.value.pod_name == "test-job"

def test_retry_on_transient_failure():
    """Test retry logic without real infrastructure."""
    call_count = 0
    
    def flaky_system_call_handler():
        def handler(effect, k):
            nonlocal call_count
            if isinstance(effect, SystemCall):
                call_count += 1
                if call_count < 3:
                    raise Exception("connection refused")
                return "success"
            yield Delegate()
        return handler
    
    result = run(
        execute_kubectl("get pods"),
        handlers=[
            retry_handler(max_retries=3),
            flaky_system_call_handler(),
        ]
    )
    
    assert call_count == 3  # retried twice, succeeded on third
    assert result.value == "success"
```

**Tests are fast, deterministic, and require no infrastructure.** The orchestration logic is unit-testable. Integration tests still exist (with real handlers), but unit tests cover the majority of logic.

## Pain Point 5: One Setup Function, Every Environment

This is the strongest argument. ml-nexus currently has **separate job runner classes per environment** — `DockerHostEnvironment`, `MLPlatformJob`, `PersistentMLPlatformJob`, `MLPlatformJobFromSchematics`, `PersistentMLPlatformFromSchematics`, `VertexAICustomJobFromSchematics`. Each implements `IScriptRunner.run_script()` with its own version of build, push, sync, deploy, run, collect. The orchestration logic is duplicated across all of them.

With effects, there is **one setup function**. Each environment provides its handler stack.

### The Problem (pinjected)

Each environment is a separate class that reimplements the full job lifecycle:

```python
@dataclass
class DockerHostEnvironment(IScriptRunner):
    _a_system: Callable
    _a_rsync: Callable
    _a_docker_build: Callable
    _logger: object
    _storage_resolver: IStorageResolver
    # ... 6 more injected deps

    async def run_script(self, script: str) -> PsResult:
        # 1. Build image locally
        image = await self._a_docker_build(self.project.dockerfile)
        # 2. Push to local Docker
        # (no push needed — local)
        # 3. Sync files via rsync over SSH
        await self._a_rsync(self.project.code_path, f"{self.docker_host}:/workspace")
        # 4. Deploy: docker run
        container = await self._a_system(f"docker run -d {image} sleep infinity")
        # 5. Run script via docker exec
        result = await self._a_system(f"docker exec {container} bash -c '{script}'")
        # 6. Collect results: docker cp
        await self._a_system(f"docker cp {container}:/results ./results")
        return result


@dataclass
class MLPlatformJobFromSchematics(IScriptRunner):
    _a_system: Callable
    _a_krsync: Callable
    _a_docker_build: Callable
    _a_docker_push: Callable
    _a_mlplatform_run_job: Callable
    _logger: object
    _storage_resolver: IStorageResolver
    _default_mlplatform_image_registry: str
    # ... 8 more injected deps

    async def run_script(self, script: str) -> PsResult:
        # 1. Build image with schematics
        image = await self._a_docker_build(self.schematic.builder)
        # 2. Push to Artifact Registry
        await self._a_docker_push(image, self._default_mlplatform_image_registry)
        # 3. Sync files via krsync to NFS
        for mount in self.mount_plan:
            await self._sync_mount(mount)  # krsync, kubectl cp, or NFS
        # 4. Deploy: kubectl ai-platform submit
        job = await self._a_mlplatform_run_job(image, script, self.machine_config)
        # 5. Run: wait for job completion
        result = await self._wait_for_job(job)
        # 6. Collect: krsync results back
        await self._a_krsync(f"{job.pod}:/results", "./results")
        return result


@dataclass
class VertexAICustomJobFromSchematics(IScriptRunner):
    _a_system: Callable
    _a_docker_build: Callable
    _a_docker_push: Callable
    _a_gcs_upload: Callable
    _logger: object
    # ... 7 more injected deps

    async def run_script(self, script: str) -> PsResult:
        # 1. Build image
        image = await self._a_docker_build(self.schematic.builder)
        # 2. Push to Artifact Registry
        await self._a_docker_push(image, self._registry)
        # 3. Sync files via GCS
        await self._a_gcs_upload(self.project.code_path, f"gs://{self._bucket}/code")
        # 4. Deploy: Vertex AI Custom Job
        job = await self._submit_vertex_ai_job(image, script)
        # 5. Run: poll Vertex AI job status
        result = await self._wait_for_vertex_job(job)
        # 6. Collect: gsutil cp results
        await self._a_system(f"gsutil cp gs://{self._bucket}/results ./results")
        return result
```

**The same 6-phase pipeline (build → push → sync → deploy → run → collect) is reimplemented in every class.** Each class has 10-20 constructor parameters. Each has subtly different error handling. Adding a new phase (e.g., "validate mounts before sync") means editing every class.

### The Solution (doeff)

Every environment-specific operation becomes an effect. One setup function works for all environments.

```python
# ---- Effects: what the pipeline CAN do ----

@dataclass(frozen=True)
class BuildImage(Effect):
    builder: DockerBuilder

@dataclass(frozen=True)
class PushImage(Effect):
    image: str
    registry: str

@dataclass(frozen=True)
class SyncFiles(Effect):
    src: Path
    dst: str
    method: str  # "rsync", "krsync", "gcs", "docker_cp"

@dataclass(frozen=True)
class DeployJob(Effect):
    image: str
    script: str
    machine_config: dict

@dataclass(frozen=True)
class WaitForJob(Effect):
    job_ref: Any
    timeout: int = 3600

@dataclass(frozen=True)
class CollectResults(Effect):
    job_ref: Any
    remote_path: str
    local_path: Path


# ---- The one setup function ----

@do
def run_ml_job(
    project: ProjectDef,
    schematic: ContainerSchematic,
    script: str,
    machine_config: dict,
    mount_plan: list[MountPlan],
) -> Program[PsResult]:
    """
    One function for every environment.
    Build, push, sync, deploy, run, collect — all effects.
    Handlers decide HOW each step happens.
    """
    # 1. Build
    yield Tell(f"Building image for {project.name}")
    image = yield BuildImage(schematic.builder)

    # 2. Push
    yield PushImage(image, project.registry)

    # 3. Sync (every mount in the plan)
    for mount in mount_plan:
        yield SyncFiles(
            src=mount.src,
            dst=mount.container_path,
            method=mount.sync_method,
        )

    # 4. Deploy
    job_ref = yield DeployJob(image, script, machine_config)

    # 5. Run (wait for completion)
    result = yield WaitForJob(job_ref, timeout=machine_config.get("timeout", 3600))

    # 6. Collect
    yield CollectResults(job_ref, remote_path="/results", local_path=Path("./results"))

    return result
```

**That's it. One function. No `_a_system`, no `_logger`, no `_a_krsync`.** Now each environment is a handler stack:

```python
# ---- Local Docker handlers ----

def local_docker_handlers():
    def handler(effect, k):
        if isinstance(effect, BuildImage):
            return (yield SystemCall(f"docker build -t {effect.builder.tag} {effect.builder.context}"))
        elif isinstance(effect, PushImage):
            return None  # local — no push needed
        elif isinstance(effect, SyncFiles):
            return (yield SystemCall(f"rsync -av {effect.src} {effect.dst}"))
        elif isinstance(effect, DeployJob):
            cid = yield SystemCall(f"docker run -d --gpus all {effect.image} sleep infinity")
            yield SystemCall(f"docker exec {cid} bash -c '{effect.script}'")
            return cid
        elif isinstance(effect, WaitForJob):
            return (yield SystemCall(f"docker wait {effect.job_ref}"))
        elif isinstance(effect, CollectResults):
            return (yield SystemCall(f"docker cp {effect.job_ref}:{effect.remote_path} {effect.local_path}"))
        yield Delegate()
    return handler


# ---- K8s GPUaaS handlers ----

def k8s_gpuaas_handlers(registry: str):
    def handler(effect, k):
        if isinstance(effect, BuildImage):
            return (yield SystemCall(f"docker build -t {effect.builder.tag} {effect.builder.context}"))
        elif isinstance(effect, PushImage):
            tag = f"{registry}/{effect.image}"
            yield SystemCall(f"docker tag {effect.image} {tag}")
            yield SystemCall(f"docker push {tag}")
            return tag
        elif isinstance(effect, SyncFiles):
            # krsync for code, NFS for data
            if effect.method == "krsync":
                return (yield SystemCall(f"krsync -av {effect.src} {effect.dst}"))
            elif effect.method == "nfs":
                return None  # NFS is pre-mounted, nothing to sync
            elif effect.method == "kubectl_cp":
                return (yield SystemCall(f"kubectl cp {effect.src} {effect.dst}"))
        elif isinstance(effect, DeployJob):
            return (yield SystemCall(
                f"kubectl ai-platform jobs submit training {effect.image} "
                f"--machine-type={effect.machine_config['machine_type']} "
                f"-- bash -c '{effect.script}'"
            ))
        elif isinstance(effect, WaitForJob):
            # Poll pod status
            while True:
                status = yield SystemCall(f"kubectl get pod {effect.job_ref} -o jsonpath='{{.status.phase}}'")
                if status in ("Succeeded", "Failed"):
                    return status
                yield Ask("delay", 5)  # poll interval
        elif isinstance(effect, CollectResults):
            return (yield SystemCall(f"krsync -av {effect.job_ref}:{effect.remote_path} {effect.local_path}"))
        yield Delegate()
    return handler


# ---- GCE / Vertex AI handlers ----

def vertex_ai_handlers(registry: str, gcs_bucket: str):
    def handler(effect, k):
        if isinstance(effect, BuildImage):
            return (yield SystemCall(f"docker build -t {effect.builder.tag} {effect.builder.context}"))
        elif isinstance(effect, PushImage):
            tag = f"{registry}/{effect.image}"
            yield SystemCall(f"docker tag {effect.image} {tag}")
            yield SystemCall(f"docker push {tag}")
            return tag
        elif isinstance(effect, SyncFiles):
            return (yield SystemCall(f"gsutil -m cp -r {effect.src} gs://{gcs_bucket}/{effect.dst}"))
        elif isinstance(effect, DeployJob):
            return (yield SystemCall(
                f"gcloud ai custom-jobs create "
                f"--region=asia-northeast1 "
                f"--worker-pool-spec=machine-type={effect.machine_config['machine_type']},"
                f"container-image-uri={effect.image} "
                f"-- bash -c '{effect.script}'"
            ))
        elif isinstance(effect, WaitForJob):
            while True:
                status = yield SystemCall(f"gcloud ai custom-jobs describe {effect.job_ref} --format='value(state)'")
                if status in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED"):
                    return status
                yield Ask("delay", 10)
        elif isinstance(effect, CollectResults):
            return (yield SystemCall(f"gsutil -m cp -r gs://{gcs_bucket}/results {effect.local_path}"))
        yield Delegate()
    return handler


# ---- Usage: same function, different handlers ----

# Local Docker
result = run(run_ml_job(project, schematic, "python train.py", A100_CONFIG, mounts),
    handlers=[local_docker_handlers(), retry_handler(), logging_handler()])

# K8s GPUaaS
result = run(run_ml_job(project, schematic, "python train.py", A100_CONFIG, mounts),
    handlers=[k8s_gpuaas_handlers(registry), retry_handler(), logging_handler()])

# Vertex AI
result = run(run_ml_job(project, schematic, "python train.py", A100_CONFIG, mounts),
    handlers=[vertex_ai_handlers(registry, bucket), retry_handler(), logging_handler()])

# Test (no infrastructure)
result = run(run_ml_job(project, schematic, "python train.py", A100_CONFIG, mounts),
    handlers=[stub_handlers()])
```

The key insight visualized:

```
Current ml-nexus: N classes x M phases = N*M implementations

  DockerHostEnvironment.run_script()      build  push  sync  deploy  run  collect
  MLPlatformJob.run_script()              build  push  sync  deploy  run  collect
  PersistentMLPlatformJob.run_script()    build  push  sync  deploy  run  collect
  MLPlatformJobFromSchematics.run_script()build  push  sync  deploy  run  collect
  PersistentMLPlatformFromSchematics...   build  push  sync  deploy  run  collect
  VertexAICustomJobFromSchematics...      build  push  sync  deploy  run  collect
                                          ─────────────────────────────────────────
                                          6 classes x 6 phases = 36 implementations


With effects: 1 function + N handler stacks

  run_ml_job()                            yield BuildImage
                                          yield PushImage
                                          yield SyncFiles
                                          yield DeployJob
                                          yield WaitForJob
                                          yield CollectResults
                                          ─────────────────────────────────────────
                                          1 function + 4 handler stacks = 10 implementations
```

**36 implementations → 10. The orchestration logic is written once. Adding a new environment means writing one handler stack, not a new class with 15 constructor parameters.**

And because handlers compose, cross-cutting concerns stack orthogonally:

```python
# Production K8s with retry + logging + event bus + recording
result = run(run_ml_job(project, schematic, script, config, mounts),
    handlers=[
        recording_handler("runs/run_042.json"),  # audit trail
        retry_handler(max_retries=3),             # transient error recovery
        event_bus_handler(bus),                    # observability
        logging_handler(),                        # user-facing logs
        k8s_gpuaas_handlers(registry),            # K8s-specific operations
    ])

# Same job, replay a failed run on Vertex AI instead
result = run(run_ml_job(project, schematic, script, config, mounts),
    handlers=[
        replay_handler("runs/run_042.json"),      # replay decisions from failed run
        vertex_ai_handlers(registry, bucket),     # but on Vertex AI this time
    ])
```

**You can't do this with DI.** DI can swap `KrsyncService` for `GsutilService`. But DI cannot replay a K8s run on Vertex AI by composing a replay handler with a different environment handler. That requires controlling execution flow — which is what effects do.

## Pain Point 6: The `returns` Library Escape Hatch

### The Problem (pinjected)

ml-nexus uses the `returns` library (`Future`, `FutureResultE`, `IOResultE`) to encode effects in types. But it constantly escapes with `unsafe_perform_io`:

```python
@injected
def a_build_and_push_image(
    _a_build_image: Injected,
    _a_push_image: Injected,
    /,
    dockerfile: Path,
    registry: str,
) -> FutureResultE[str]:
    """Build and push Docker image."""
    # Build image (returns FutureResultE)
    image_result: FutureResultE[str] = _a_build_image(dockerfile)
    
    # Can't compose without unsafe_perform_io
    image = unsafe_perform_io(image_result.unwrap())
    
    # Push image (returns FutureResultE)
    push_result: FutureResultE[None] = _a_push_image(image, registry)
    
    # Escape again
    unsafe_perform_io(push_result.unwrap())
    
    return FutureResultE.from_value(image)
```

**The `returns` library tries to encode effects in types, but Python has no language-level support for it.** Every composition point requires `unsafe_perform_io`. This is algebraic effects trying to happen through a library.

### The Solution (doeff)

```python
@do
def build_and_push_image(dockerfile: Path, registry: str) -> Program[str]:
    """Build and push Docker image. No escape hatches."""
    image = yield BuildImage(dockerfile)
    yield PushImage(image, registry)
    return image
```

**Effects are first-class in doeff.** No `unsafe_perform_io`. No escape hatches. Composition is natural.

## Pain Point 7: Event Bus as Manual Effect Handling

### The Problem (pinjected)

ml-nexus has `ml_nexus_system_call_event_bus` manually called at every system call site:

```python
@injected
def a_execute_kubectl(_a_system: Injected, _a_event_bus: Injected, /, cmd: str) -> FutureResultE[str]:
    # Emit start event
    _a_event_bus(SystemCallStart(cmd=f"kubectl {cmd}"))
    
    try:
        result = _a_system(f"kubectl {cmd}")
        
        # Emit stdout event
        _a_event_bus(SystemCallStdOut(cmd=f"kubectl {cmd}", stdout=result))
        
        # Emit end event
        _a_event_bus(SystemCallEnd(cmd=f"kubectl {cmd}", exit_code=0))
        
        return FutureResultE.from_value(result)
    except Exception as e:
        # Emit stderr event
        _a_event_bus(SystemCallStdErr(cmd=f"kubectl {cmd}", stderr=str(e)))
        
        # Emit end event
        _a_event_bus(SystemCallEnd(cmd=f"kubectl {cmd}", exit_code=1))
        
        return FutureResultE.from_failure(e)
```

**Every system call site manually emits 3-4 events.** This is boilerplate. It's error-prone (easy to forget an event). It's what effect handlers do automatically.

### The Solution (doeff)

```python
@do
def execute_kubectl(cmd: str) -> Program[str]:
    """No event bus calls. That's a handler concern."""
    return (yield SystemCall(f"kubectl {cmd}"))

# Event bus handler
def event_bus_handler(bus):
    def handler(effect, k):
        if isinstance(effect, SystemCall):
            # Emit start event
            bus.emit(SystemCallStart(cmd=effect.cmd))
            
            try:
                result = yield Delegate()
                
                # Emit stdout event
                bus.emit(SystemCallStdOut(cmd=effect.cmd, stdout=result))
                
                # Emit end event
                bus.emit(SystemCallEnd(cmd=effect.cmd, exit_code=0))
                
                return result
            except Exception as e:
                # Emit stderr event
                bus.emit(SystemCallStdErr(cmd=effect.cmd, stderr=str(e)))
                
                # Emit end event
                bus.emit(SystemCallEnd(cmd=effect.cmd, exit_code=1))
                
                raise
        
        yield Delegate()
    return handler

# Use it
result = run(
    execute_kubectl("get pods"),
    handlers=[
        event_bus_handler(ml_nexus_event_bus),
        system_call_handler(),
    ]
)
```

**Event emission is automatic.** Every `SystemCall` effect triggers events through the handler. The business logic never mentions the event bus.

## The Handler Stack for ML Infrastructure

```
ML job deployment pipeline with effect handlers:

  [recording_handler]           -> audit trail (replay failed jobs)
  [retry_handler]               -> transient error recovery
  [event_bus_handler]           -> observability (start/end/stdout/stderr)
  [logging_handler]             -> user-facing logs (or silent for polling)
  [k8s_sync_handler]            -> krsync, kubectl cp, NFS mount
  [system_call_handler]         -> actual kubectl/docker/gcloud execution

Same job logic, different handler stacks:

  [stub_handlers]                           -> unit tests (no infrastructure)
  [recording + real_handlers]               -> production (with audit trail)
  [replay_handler + real_handlers]          -> resume failed job from checkpoint
  [logging + retry + real_handlers]         -> verbose mode with retry
  [retry + real_handlers]                   -> silent mode (polling loops)
  [gce_sync + real_handlers]                -> GCE deployment
  [vertex_ai_sync + real_handlers]          -> Vertex AI deployment
```

## The Evolution: pinjected → doeff

This isn't "DI is bad." This is "effects are the next step beyond DI."

| Aspect | pinjected (DI) | doeff (Effects) |
|--------|----------------|-----------------|
| Service swapping | `@injected` with mocks | Effect handlers |
| Constructor parameters | 15-parameter constructors | Zero-parameter effects |
| Retry logic | Copy-pasted 5 times | One retry handler |
| Silent variants | Duplicate functions | Compose handlers |
| Testing | Requires infrastructure | Stub handlers |
| Job lifecycle | 6 classes x 6 phases = 36 impls | 1 function + 4 handler stacks |
| Event bus | Manual calls | Automatic handler |
| Composition | `unsafe_perform_io` escapes | Natural `yield` |

**pinjected solves service swapping. doeff solves cross-cutting concerns, composition, and testability.**

## ASCII Diagram: ML Job Deployment Flow

```
User Code (business logic):
┌─────────────────────────────────────────────────────────────┐
│ @do                                                          │
│ def deploy_ml_job(spec, plan):                              │
│     image = yield BuildImage(spec.dockerfile)               │
│     yield PushImage(image, spec.registry)                   │
│     pod = yield SystemCall(f"kubectl apply -f {spec.yaml}") │
│     yield SyncMount(plan)                                   │
│     result = yield WaitForPod(pod)                          │
│     return result                                           │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ yield effects
                            ▼
Handler Stack (cross-cutting concerns):
┌─────────────────────────────────────────────────────────────┐
│ RecordingHandler                                            │
│   └─> captures all effects for replay                      │
├─────────────────────────────────────────────────────────────┤
│ RetryHandler                                                │
│   └─> retries SystemCall on transient errors               │
├─────────────────────────────────────────────────────────────┤
│ EventBusHandler                                             │
│   └─> emits SystemCallStart/End/StdOut/StdErr              │
├─────────────────────────────────────────────────────────────┤
│ LoggingHandler                                              │
│   └─> logs effect execution (or omit for silent mode)      │
├─────────────────────────────────────────────────────────────┤
│ K8sSyncHandler                                              │
│   └─> handles SyncMount with krsync/kubectl cp/NFS         │
├─────────────────────────────────────────────────────────────┤
│ SystemCallHandler                                           │
│   └─> executes actual kubectl/docker/gcloud commands       │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ real execution
                            ▼
Infrastructure (K8s, Docker, GCS):
┌─────────────────────────────────────────────────────────────┐
│ kubectl apply -f job.yaml                                   │
│ docker build -t image:tag .                                 │
│ docker push registry/image:tag                              │
│ krsync -av /local/code pod:/workspace                       │
│ kubectl wait --for=condition=complete pod/job               │
└─────────────────────────────────────────────────────────────┘

For tests, swap bottom 3 handlers with StubHandlers:
┌─────────────────────────────────────────────────────────────┐
│ StubSystemCallHandler                                       │
│   └─> returns fake kubectl output                          │
├─────────────────────────────────────────────────────────────┤
│ StubSyncHandler                                             │
│   └─> returns success without actual sync                  │
└─────────────────────────────────────────────────────────────┘
```

## The Pitch

> "You've built ML infrastructure with dependency injection. You have 15-parameter constructors, copy-pasted retry loops, and tests that require real Kubernetes. Algebraic effects are the next step: zero-parameter functions, retry as a handler, and tests with stub handlers. Same orchestration logic, different execution modes. Write it once, run it everywhere — local Docker, K8s, GCE, Vertex AI. The job logic never changes. Only the handlers."

**This isn't replacing pinjected. It's evolving beyond it.**
