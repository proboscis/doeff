# doeff-agentic-cli

Fast Rust CLI for doeff-agentic workflow management.

This is a Rust implementation of the doeff-agentic CLI that provides faster startup times (~5ms vs ~300ms Python) for plugin integration.

## Build

```bash
cargo build --release
```

## Usage

```bash
doeff-agentic ps
doeff-agentic watch <id>
doeff-agentic attach <id>
```