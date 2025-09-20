# doeff-indexer

Rust-based static indexer for discovering `Program` and `KleisliProgram` definitions in a doeff
codebase. The tool scans Python modules for:

- Functions decorated with `@do` (reported as Kleisli programs)
- Functions that accept `Program[...]` or `ProgramInterpreter` parameters
- Functions whose return annotation references `Program` or `KleisliProgram`
- Module-level assignments annotated with or returning `Program`/`KleisliProgram`

Each indexed item includes detected type-argument usage so you can query for specific
`Program`/`KleisliProgram` generics.

## Building

```bash
cd packages/doeff-indexer
cargo build --release
```

## Usage

```bash
# Print JSON index for the repository root
cargo run --release -- --root .. --pretty

# Filter by Program type argument
cargo run --release -- --root .. --kind program --type-arg int --pretty

# Filter by Kleisli programs (any type argument)
cargo run --release -- --root .. --kind kleisli

# Write index to a file
cargo run --release -- --root .. --output index.json
```

Omit `--type-arg` (or set it to `Any`) to match every `Program`/`KleisliProgram` regardless of the
captured type parameter.

## Testing

```bash
cargo test
```
