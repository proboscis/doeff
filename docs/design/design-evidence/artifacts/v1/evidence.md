# Verification record — design evidence v1

What was checked, on which object, how, and the result. Linter: release build of this branch
(commit `dd3d38f92`).

## 1. Tests (execution)

| Claim | Test | Result |
|---|---|---|
| A defrecord's fields (order, types, optional) and bases are read from source | `project::design_evidence::tests::shapes_are_read_in_order_with_optional_fields` | pass |
| Complete evidence of the current shape is green | `matching_complete_evidence_is_green` | pass |
| A new type without evidence is a finding | `a_new_type_without_evidence_is_missing` | pass |
| An existing unchanged type is green; the same type reshaped is a finding | `an_existing_unchanged_type_is_green_and_a_reshaped_one_is_missing` | pass |
| A shape that differs from the evidence (added field, changed type) is a finding | `a_shape_that_differs_from_the_evidence_is_a_hit` | pass |
| Each missing answer is named (concern, key, fact, absent_when, rejected, fan_out, review) | `missing_answers_are_gaps` | pass |
| `review` follows `:review-pattern` | `review_spelling_follows_the_declared_pattern` | pass |
| Orphan entries and unreadable evidence are findings | `orphan_and_unreadable_evidence_are_hits` | pass |
| The binary: new / reshaped types are red, existing unchanged types are green, the ledger covers only the population (`--print-existing-shapes`) | `tests/design_evidence.rs::new_and_reshaped_types_need_evidence_and_existing_unchanged_types_do_not` | pass |
| The binary: an added field and dropped answers are red with the named gaps | `tests/design_evidence.rs::evidence_must_match_the_shape_and_answer_every_question` | pass |
| One-file runs judge a `.hy` from stdin and an `.evidence.toml` from stdin (reported at the `.hy`) | `tests/design_evidence.rs::one_file_runs_judge_the_module_and_its_evidence` | pass |
| The rule's registration tables stay exhaustive (families, declaration items, config keys) | `project::rule::tests::*`, `project::declaration_items::tests::*`, `project::architecture::tests::*` | pass (37 lib tests in total with the above) |

## 2. Trial on a real tree (execution)

Object: the 8,791 tracked `.hy` files of a downstream project at one commit, copied into a scratch
directory, with `architecture.hy` declaring
`:design-evidence {:files ["**/*.hy"] :except ["**/tests/**"] :existing "design-existing.toml"}`
and only DOEFF212 enabled.

| Step | Result |
|---|---|
| `--print-existing-shapes` | a ledger of 12,297 defrecords; 2 files the Hy reader cannot read were named on stderr (exit 2, as designed) |
| Whole run | 0 DOEFF212 findings (every type is in the ledger with its current fingerprint), 8.97 s wall |
| One-file run, the module holding the 26-field material type, unchanged | 0 findings |
| One-file run, the same module with one optional field added to that type | 1 critical finding: `…notice_types.hy::DOEFF212::DoneMaterial` at line 119 — "defrecord DoneMaterial changed its shape (ledger 92b80efdef9ea5fd → now c9c47caf457503dd) and has no entry in …notice_types.evidence.toml — a reshaped type needs the answers to the required design questions" |

## 3. Time of a one-file judgment (execution)

`--output-format editor-json --stdin --path <the module>`, on the trial tree, with
`DOEFF_LINTER_TIMING=1`:

| Run | Wall | Of which DOEFF212 |
|---|---|---|
| warm, unchanged (3 runs) | 0.22–0.56 s | — |
| warm, with the added field (3 runs) | 0.23–0.57 s | — |
| cold (a fresh copy of the tree) | 193 s | `single.design-evidence.ledger` 0.184 s, `single.design-evidence` 0.002 s |

The cold run's time is `facts-cache.roots` (192.6 s): the linter's existing per-root fact cache is
built once for a new root. It is not part of DOEFF212 and does not recur in a working tree. The
rule's own cost is the ledger read (0.18 s for 12,297 lines) plus 2 ms of judging — well inside the
authoring-time hook's 3-second budget. For comparison, the whole run of the downstream project's
full configuration takes about 220 s.

## 4. Not checked (and what therefore cannot be claimed)

- No reviewer verdict was checked for existence (round 1 checks the spelling only — design §6).
- The commit-hook and merge-gate paths were not run with DOEFF212 enabled; `needs_whole_repo` =
  true places it in their whole-repo comparison by the rule's own declaration.
- No evidence was written for the downstream project's types (out of scope for this round).
