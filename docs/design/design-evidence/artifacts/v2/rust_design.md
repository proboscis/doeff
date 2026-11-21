# Rust source shape reading and design evidence comparison (Round 2)

Version v2 (2026-11-20). Status: on work branch `wt/design-evidence-rust`; not on main.

## 1. Where to place the Rust source shape reading (comparison and rationale)

We evaluated three placement options for reading Rust source shapes using system-design principles (DDD, Volatility-based decomposition, Change Cases, ADD):

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Add to `doeff-linter` (chosen)** | • Single source of truth for the language-agnostic evidence model, ledger, fingerprinting, and comparison logic.<br>• Reuses DOEFF212 infrastructure directly (TOML parsing, missing/different/gap finding, `--print-existing-shapes`).<br>• Fast: written in Rust, runs in 0.0x s per file, fitting authoring hook latency requirements.<br>• Changes localized to adding a language reader (`shapes_of` dispatch). | • Extends doeff-linter to read Rust in addition to Python/Hy. | **Adopted** |
| **B. Separate small binary (e.g. `rust-evidence-lint`)** | • Decoupled from Python/Hy linter. | • Duplicates evidence model, ledger format, fingerprint rules, and answer validation.<br>• Increased maintenance overhead (two tools, dual CI/hook setup).<br>• Violates "one model for every language" rule. | Rejected |
| **C. Place on `mnemosyne` side (e.g. `scripts/architecture_lint.py`)** | • Localized to mnemosyne. | • Python implementation cannot meet hook latency requirement.<br>• Inapplicable to other Rust crates/repos.<br>• Drifts from doeff's core design evidence specification. | Rejected |

### Volatility-Based Decomposition and Single Responsibility:
Language-specific parsing is isolated to `rust_record_shapes` (reading AST shapes into `RecordShape`). All downstream stages (ledger verification, evidence TOML parsing, answer validation, error reporting) remain pure, language-agnostic functions.

## 2. Shapes of Rust struct and enum

- **`struct`**:
  - Named fields: field names and types (e.g., `id: u64`, `name: String`).
  - Tuple structs: 0-indexed field names (e.g., `0: u64`, `1: String`).
  - Unit structs: empty fields list.
  - Optional fields: types matching `Option<T>` (`std::option::Option<T>`, `core::option::Option<T>`) require `absent_when` in evidence.
- **`enum`**:
  - Variants are treated as sum-type branches. Each variant corresponds to a field entry in `RecordShape` and `element.field`:
    - Unit variant: `type_text = None`.
    - Tuple variant: `type_text = Some("(...)")`.
    - Struct variant: `type_text = Some("{ ... }")`.
  - This allows documenting the fact represented by each variant, matching the design evidence philosophy.

## 3. Proposal for extending to pub functions, trait methods, and mod locations (Next Round)

In the next round, the same evidence model (`ElementEvidence`) is extended to other code elements:

1. **`pub` functions (`kind = "function"`)**:
   - Signature fingerprint: `fn <name>(<arg_name>: <arg_type>, ...) -> <return_type>`.
   - Evidence questions:
     - `concern`: One-sentence single responsibility of the function.
     - `inputs`: Facts asserted about each parameter (preconditions).
     - `output`: Fact asserted by the return value (postcondition).
     - `side_effects`: What effects (I/O, locks, mutations) it performs, or pure.
     - `review`: Verdict ID.
2. **Trait methods (`kind = "interface"` or `kind = "trait"`)**:
   - Signature fingerprint: `method <name>(<params>) -> <return_type>`.
   - Evidence questions: contract invariant, extensibility requirement, default impl rationale.
3. **`mod` placement (`kind = "module"`)**:
   - Signature fingerprint: list of public items exported and dependencies imported.
   - Evidence questions: layer assignment, coupling constraints, boundary responsibilities.

All three kinds plug into `ElementKind` (`Function`, `Interface`, `Module`), requiring only reader extractors, while ledger storage and comparison remain identical.

## 4. Measurement: One-file judgment cost on mnemosyne

Target: `mnemosyne/crates/client-nfs/src/lib.rs` (contains `CommitFs`).

Measurements across 10 iterations:
- **CLI file run (`doeff-linter crates/client-nfs/src/lib.rs`)**:
  - Min: 10.97 ms
  - **Median: 13.44 ms (0.0134 s)**
  - Max: 16.00 ms
- **Editor stdin run (`--stdin --path ... --output-format editor-json`)**:
  - Min: 8.90 ms
  - **Median: 10.71 ms (0.0107 s)**
  - Max: 13.35 ms

Both modes comfortably beat the authoring hook threshold (< 0.20 s / 200 ms) by an order of magnitude, adding almost zero noticeable delay to editor feedback or git pre-commit hooks.

