# doeff-effect-anlyzer

Static effect dependency analyzer for the doeff ecosystem.

## Overview
This crate provides the Rust core for SEDA (Static Effect Dependency Analyzer). It exposes
incremental, tree-structured effect dependency reports to Python via PyO3 bindings and serves as the
foundation for the `seda` command-line interface.

## Building
```
uv run maturin develop --manifest-path packages/doeff-effect-anlyzer/Cargo.toml
```

## Status
🚧 Work in progress — see `specs/effect-analyzer/` for design details and the implementation
checklist.