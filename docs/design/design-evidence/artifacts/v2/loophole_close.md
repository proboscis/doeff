# Closing the two ways around DOEFF212

The trial of DOEFF212 found two ways to change a type's shape without the considered design the
rule asks for:

1. **The ledger.** A line added to the ledger of existing shapes (or a fingerprint rewritten) lets
   a new or reshaped type pass as an existing one.
2. **One sentence of evidence.** A field added to a type that existed before the rule passes as soon
   as the author writes complete answers — one sentence of `concern` was enough for a type that grew
   to 27 fields.

Both are closed by the `:guard` of `:design-evidence` (DOEFF214, and the `Grew` finding of DOEFF212).
`packages/doeff-linter/tests/evidence_loopholes.rs` runs each way around under the declaration
without `:guard` (it passes) and with `:guard` (it is red).

## 照らした標準 (standards compared)

- **Ratchet baselines** (ESLint's suppressions file, Betterer, RuboCop's `.rubocop_todo.yml`): a
  file of known findings that may only shrink. The ledgers follow this. The difference is that an
  entry may grow only through an approval that landed first, not by regenerating the file.
- **CODEOWNERS / required reviewers** (GitHub, GitLab): who may approve a change is decided outside
  the change. The approvals file counts only at the base for the same reason: the change cannot
  approve itself.
- **Architecture Decision Records** (Nygard): the reason for a design lives next to the code. The
  evidence file is that record per type. The user's permission is a separate fact, not part of the
  author's record.
- **Not adopted: a Rust parser crate (`syn`)** for the shapes. Only item headers and field lists are
  needed, and the linter's binary is called from the commit hook (see `TokenKind`'s evidence).

## 1. The ledgers only shrink

DOEFF214 compares each ledger with its version at the base (the merge-base of HEAD and
`:guard :base`). An added or rewritten shapes line, a raised substitutes count, and a changed
`:design-evidence` declaration are red unless an approval at the base covers them. A removed line
passes.

## 2. An existing type grows only with the user's approval

`Grew`: a type of the base's shapes ledger that has fields its version at the base did not have is
red, whatever its evidence says, unless an approval `ledger = "fields"`, `key = "<rel>::<Type>"`,
`value = "<field>"` at the base covers the field.

- A record (defrecord, Rust struct) grows by a field.
- A sum (Rust enum) grows by a member added to the payload of a variant the base had
  (`Rejected { reason }` → `Rejected { reason, retry_after }` grows `Rejected.retry_after`;
  `Pending(u64)` → `Pending(u64, String)` grows `Pending.1`). A new variant is a new case of a closed
  set, not a new fact of a record, and needs evidence only. Penalising it would push authors from
  enums to strings.
- A retyped field is a reshape (evidence answers it), not growth.

## 3. Where the approval lives

The card that asked for this said "the request id in the evidence file". It lives in the approvals
file (`:guard :approvals`) instead, and only the file **at the base** counts:

- The evidence is the author's answer, written in the same change as the type. An approval written
  there could be written by the author in that same change. The user's approval is another fact,
  and one fact has one place.
- An approval lands alone first (its own reviewed change), and only then lets the change pass. An
  approval added on the same branch counts for nothing.
- An approval counts only if its `review` is of the declared spelling (`:approval-pattern`, a
  request id) and it says `why` — at the base as well as on the branch. `Approval::problem` is the
  one check: an added entry with a problem is red (DOEFF214), and an entry at the base with a problem
  lets nothing pass (`Approvals::spelled` → `BaseApprovals`, the only type with `covers`).

**Limit.** The linter checks the spelling only. Whether the id names a request that was put to the
user, answered, and answered with a permission — and is not reused — cannot be read from the tree.
The check where a change enters the merge queue reads that from the request records. Until it does,
the review of the change that lands an approval is where a person reads it.

## 4. The shape's kind

`RecordShape.kind` is `ShapeKind { Defrecord, Struct, Enum }` (the Rust reader first wrote it as a
string of three values). The kind enters the fingerprint, so a struct that becomes an enum changes
it. A defrecord has no kind line in its canonical spelling, so the fingerprints of the Hy ledgers
written before the Rust reader stay the same — rewriting every line would itself be a rewritten
ledger line for DOEFF214. Keys hold the path, so kinds cannot collide across languages. Evidence of
an enum is written with `kind = "sum"` (`ElementKind::Sum`); a record's with `kind = "record"`.

## 5. The rule applied to its own types

`packages/doeff-linter/self-evidence/` declares DOEFF212 over the three modules of the mechanism
(`design_evidence.rs`, `ledger_guard.rs`, `type_substitutes.rs`, 25 types). There is no ledger, so
every type carries evidence. `tests/self_evidence.rs` requires 0 hits and that the reader reads the
25 types. DOEFF213 reads `.hy` files only and is not applied. `architecture.rs` and `typed_values.rs`
are outside the population: they hold the types of every other rule (see the declaration's comment).

## 6. Review

`design-evidence-review-2026-11-20` — the review of this change against normalisation, one fact in
one place, composition, and SRP. Its verdict was *fix*. What was fixed on the same branch:

- variant payloads grow (§2);
- the self-application claims DOEFF212 only and counts the types read;
- the evidence's review names this review;
- the base's approvals are their own type;
- the approval ledger's words are spelled once;
- enums are evidenced as sums;
- this record.

## 7. Known gaps

- `RecordShape` holds a record's fields and a sum's variants in one `fields` list. A part type per
  body (`Record { bases, fields }` / `Sum { variants }`) would make variants carry no `optional` or
  `bases`. `grown_against` reads the payload from the variant's spelling instead.
- `Approval::problem` returns its three cases as a string, and an approval's `key` and `value` mean
  different things per ledger. An `ApprovalTarget` sum would normalise them.
- `FileHits` and `FileExcess` have the same shape. The pair `start`/`end` appears in `RecordShape`,
  `Token` and `Hit`, while `crate::position::Range` already exists. A per-file findings type and a
  span type are missing parts.
