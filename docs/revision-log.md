# Revision Log

This file stores historical and migration-oriented notes that are intentionally
kept out of architecture guides.

## DA-003: Kleisli Arrows Documentation Refresh

### Scope

- Updated `docs/11-kleisli-arrows.md` to describe only the current call-time
  macro architecture for `KleisliProgram.__call__`.

### Historical Notes Moved Out of Inline Docs

- Earlier Kleisli docs used legacy framing around automatic unwrapping behavior
  and protocol details that are no longer the active architecture model.
- Historical protocol references (including retired runtime/protocol names and
  migration-style comparisons) are tracked in this revision log instead of
  inline chapter content.

