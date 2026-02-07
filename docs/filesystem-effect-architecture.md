# Filesystem Effect Architecture

This document sketches the design for a dedicated filesystem effect that unifies local and remote file access behind a single doeff abstraction. The core library stays lightweight by shipping a stdlib-only local backend, while richer targets (such as `fsspec` for `s3://`, `gs://`, etc.) live in optional adapter packages that reuse the same effect surface.

## Goals

- Provide a typed `FileSystemEffect` union whose operations compose naturally with existing doeff programs.
- Hide backend-specific file handles behind opaque identifiers so interpreters control lifecycle and cleanup.
- Guarantee asynchronous ergonomics by routing blocking filesystem calls through configurable executors.
- Expose metadata operations (`Exists`, `Stat`) alongside read/write so callers can reason about files without leaking backend details.

## Effect API

All operations will be defined as dataclasses inside the `FileSystemEffect` union, using the agreed `Fs*` naming scheme.

- `FsOpen(path: str | Path, mode: str, binary: bool = False, encoding: str | None = None) -> FileHandleId`
- `FsRead(handle: FileHandleId, size: int | None = None) -> bytes | str`
- `FsReadAll(handle: FileHandleId) -> bytes | str`
- `FsWrite(handle: FileHandleId, data: bytes | str) -> int`
- `FsFlush(handle: FileHandleId) -> None`
- `FsSeek(handle: FileHandleId, offset: int, whence: SeekFrom = SeekFrom.START) -> int`
- `FsGetPosition(handle: FileHandleId) -> int`
- `FsClose(handle: FileHandleId) -> None`
- `FsExists(path: str | Path) -> bool`
- `FsStat(path: str | Path) -> FileStat`

Supporting types:

- `FileHandleId`: opaque token assigned when `FsOpen` succeeds; clients never touch raw file objects.
- `FileHandleRecord`: internal handler struct capturing the `fsspec` file object, binary/text mode, encoding, and owning filesystem reference.
- `FileStat`: dataclass mirroring common metadata (`path`, `size`, `is_dir`, `is_file`, `mtime`, `backend_extra: Mapping[str, Any] | None`).
- `SeekFrom`: small enum that mirrors Python’s `os.SEEK_*` constants for readability.
- `FileSystemError` hierarchy to normalize backend exceptions (`FsOpenError`, `FsReadError`, etc.).

## Handler Architecture

### Core Local Handler

- Ships inside `doeff` and relies solely on the Python stdlib (`pathlib.Path.open`, `io.IOBase`, etc.).
- Registers a `ThreadPoolExecutor` (configurable, default `max_workers=4`) to execute blocking file operations without stalling the event loop.
- Tracks open handles in a table keyed by `FileHandleId` and guarantees idempotent closing plus teardown cleanup.

### Optional Adapter Packages

- Additional handlers (for example, an `fsspec` adapter under `packages/doeff-fs-fsspec`) reuse the same effect API but delegate operations to their specific backends.
- Each adapter manages its own executor strategy (e.g., thread pool around `fsspec` calls) while honoring the `FileSystemEffect` contract.
- Interpreter wiring allows users to choose which handler to register; the core distribution defaults to the local handler while leaving room for plugins.

## Metadata Operations

- Local handler implements `FsExists`/`FsStat` using `Path.exists()` and `Path.stat()`, mapping results into `FileStat`.
- Adapter handlers (e.g., `fsspec`) map their backend metadata into the same structure and may cache filesystem lookups if needed.
- Errors (missing files, permission issues, unsupported operations) are translated into the new `FileSystemError` types so callers receive consistent failures regardless of backend.

## Async Considerations

- Core handler routes every blocking call through its thread pool to avoid event-loop stalls; adapters can swap in custom executors (including `Trio`/`anyio` friendly versions).
- Provide handler configuration to override the executor (e.g., custom `max_workers`, reuse an external executor, or disable pooling for test environments).
- Evaluate whether small reads/writes can optionally bypass the executor for performance, but keep the default path thread-pooled until benchmarks say otherwise.

## TODO Checklist

- [ ] Define `FileHandleId`, `FileStat`, `SeekFrom`, and the `FileSystemError` hierarchy (`doeff/effects/filesystem.py` + `doeff/types.py` updates as needed).
- [ ] Add the `FileSystemEffect` union with `Fs*` dataclasses in `doeff/effects/filesystem.py` and export the effect in the public API surface.
- [ ] Implement the core local handler in `doeff/handlers/filesystem.py` that owns the thread pool, manages handle lifecycles, and delegates to stdlib file objects.
- [ ] Update the interpreter wiring (`doeff/interpreter.py`) to register the filesystem handler and guarantee graceful shutdown.
- [ ] Publish an optional `packages/doeff-fs-fsspec` adapter that plugs into the effect and handles remote URLs via `fsspec`.
- [ ] Write integration tests (`tests/test_filesystem_effect.py`) covering open→write→seek→read→close, and metadata (`FsExists`, `FsStat`) for local paths via `pytest`.
- [ ] Add adapter-specific tests (e.g., `packages/doeff-fs-fsspec/tests`) verifying thread-pooled behavior and remote path support (mocked).
- [ ] Document configuration knobs (executor sizing, optional caching, adapter selection) in the public docs and surface a quickstart example under `examples/`.