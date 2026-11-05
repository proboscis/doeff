# doeff-adr

`doeff-adr` turns ADRs into executable contracts.

The package provides Hy macros for:

- `defadr`: register an ADR as structured data and emit a pytest contract check.
- `defsemgrep`: register an installed Semgrep rule with hit/clean fixtures, or
  an inline Semgrep rule body, and emit a pytest check (both through the
  effects `RegisterSemgrep` and `AssertSemgrep` — see "Semgrep checks as
  effects" below).
- `deftest`: re-exported from `doeff-hy` for ADR-local executable examples.
- pytest plugin: collect `defadr_*.hy` / `test_defadr_*.hy` executable ADR
  files and run generated `test_*` functions.
- wiring self-check: compare every executable ADR in the repository with the
  files that the current pytest invocation actually collected.

Accepted ADRs must carry at least one executable enforcement.

## Collection records for dynamic parametrize

Constant references in `:interpreters` and expressions in `:params` record their
real values and explicit ids after the first import. Strings, integers, and the like
are saved as values. Counterpart objects, records, and the like save only their position,
and only when they have an explicit id; at setup they are replaced with the real value
at the same position. For shapes that records cannot reproduce collection for, such as
per-item marks, indirect fixtures, and multiple arguments, the reason is shown and the
conventional import collection is kept.

Because the origin of a dynamic value is not guessed, the sources that were loaded under
the pytest root at save time are recorded conservatively. Changes such as re-exports or a
value count growing from 0 also rebuild the record on a source change. The record may be
rebuilt even on an unrelated source change. Warm collection first compares file size and
mtime, and also uses ctime, device, and inode to tell apart rewrites of the same size and
different worktrees. Only files whose state changed have their hash computed; if the
contents are identical, the state record is updated. The comparison does not import modules.

The collection report shows the number of files compared, the number of stat matches, the
number of files hashed, the elapsed time, and the number of test files rebuilt because of
dependency changes. Changes in values other than source, such as environment variables, are
rejected by the setup comparison and the stale record is deleted. Because an import can
cause arbitrary side effects, the record does not guarantee the sameness of an arbitrary
execution environment.
