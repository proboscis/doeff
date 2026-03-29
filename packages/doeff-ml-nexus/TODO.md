# doeff-ml-nexus-hy TODO

## Hacks to fix

### ~~1. Double COPY workaround (docker.hy)~~ DONE
Fixed: `prepare-build-context` (context.hy) overwrites pyproject.toml/uv.lock in the build context with rewritten versions before Docker build. No double COPY needed.

### ~~2. Mutable list in collect-dockerfile (dockerfile.hy)~~ NOT A HACK
doeff's own `Listen` handler uses the same mutable list + observer pattern internally. WithObserve + mutable closure is idiomatic doeff for effect collection.

### ~~3. Hardcoded _REMOTE_RUNNER string (docker.hy)~~ DONE
Fixed: Created `doeff_ml_nexus.runner` module. Docker handler now uses `uv run python -m doeff_ml_nexus.runner`.

### ~~4. String replacement for pyproject.toml/uv.lock (uv_deps.py)~~ ACCEPTABLE
String replacement targets specific unique patterns (`path = "..."` and `editable = "..."`). TOML re-serialization risks format changes that break uv.lock compatibility. Current approach is safe enough.

### ~~5. Code duplication in uv-image / uv-gpu-image (docker.hy)~~ DONE
Fixed: Both delegate to `uv-image-core` with `:gpu` flag.

### ~~6. Direct file write in setup-local-deps (docker.hy:79)~~ DONE
Fixed: `setup-local-deps` removed. File writing moved to `prepare-build-context` (context.hy). SSH file writes remain as implementation detail (addressed in #7).

### ~~7. SSH mixed into _run-cmd (docker.hy)~~ PARTIALLY DONE
Fixed: `WriteFile` effect + handler extracted from context.hy (no more raw subprocess SSH calls for file writes). Docker handler's `_run_cmd` still has SSH branching but this is appropriate — Docker effects have a `host` field and the handler decides how to execute.

### ~~8. macros.hy copied from proboscis-ema~~ DONE
Fixed: Replaced with `doeff-hy` package dependency (from doeff monorepo). Local macros.hy deleted.

### ~~9. Rust toolchain installed in every container build~~ ACCEPTABLE
Docker layer cache makes Rust install a one-time cost. Subsequent builds hit cache. Multi-stage build would add complexity without significant benefit for the Dockerfile=Program pattern.

## Design improvements (not urgent)

### ~~10. rewrite_uv_lock_for_container calls find_local_deps repeatedly~~ DONE
Fixed: Cached deps lookup outside the regex callback.

### ~~11. Interpreter doesn't orchestrate the full flow~~ DONE
Fixed: `make-remote-uv-interpreter` uses `prepare-build-context` to handle rsync, local dep detection, and pyproject/uv.lock rewriting.

### 12. No .dockerignore generation
Docker build context includes everything (.venv, __pycache__, etc.) unless excluded by rsync.
**Fix**: Generate `.dockerignore` from rsync excludes or provide a default.

### 13. No image caching strategy
Every invocation rebuilds the Docker image even if nothing changed.
**Fix**: Hash pyproject.toml + uv.lock + local deps to generate a stable image tag. Skip build if image exists.
