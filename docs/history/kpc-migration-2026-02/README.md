# KPC migration records (2026-02)

Design and verification records from the February 2026 migration that turned
KPC (`KleisliProgram.__call__`) from an effect into a macro expansion: calling a
`KleisliProgram` now produces a `Call` DoCtrl and the VM has no knowledge of KPC.
The resulting contract lives in `specs/` (SPEC-KPC-001); these files are the
working record of how it was reached.

- `plans/` — the staged plans (spec extraction, macro expansion, the
  implementation-hang fix).
- `evidence/` — measurements taken during the migration: the hang baseline, the
  targeted and negative-path verifications, the final readiness check, and the
  research notes.
- `drafts/` — the confirmed requirements as first written down.

These files were previously tracked under `.sisyphus/`, the state directory of
the planning tool that drove the migration. That directory is ignored
(`.gitignore`), so the records were tracked only by accident of their creation
order; they are kept here because they are design and verification records, not
tool state. The one file left behind, `.sisyphus/boulder.json`, is tool state —
it holds an absolute path on the author's machine and planning-session ids — and
was untracked in the same change.
