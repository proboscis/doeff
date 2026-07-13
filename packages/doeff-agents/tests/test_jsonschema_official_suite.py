"""agentd result-contract validator vs the official JSON-Schema-Test-Suite.

ACP plan U1 (2026-07-06): the correctness definition for contract
validation is the JSON Schema SPECIFICATION — never the measured behavior
of any implementation.  This runner drives every required draft2020-12
case from the official test suite through the actual adapter the session
host uses at report time (``validate_against_schema``), asserting
valid <=> reason is None.

Skipped by design (see tests/data/json_schema_test_suite/PROVENANCE.md):
- ``optional/`` cases (not vendored): spec-optional behavior (format
  assertion vocabulary etc.) is not part of the contract surface.
- ``refRemote.json``: requires an HTTP remote-ref registry; agentd
  contract schemas are self-contained, and a schema with an unresolvable
  remote $ref fails loudly at validation time instead.
"""

import json
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # registers the .hy importer

from doeff_agents.sessionhost.schema import validate_against_schema

SUITE_DIR = Path(__file__).resolve().parent / "data" / "json_schema_test_suite" / "draft2020-12"
SKIP_FILES = {"refRemote.json"}

REMOTE_SKIP = (
    "requires the suite's HTTP remotes registry; agentd contract schemas are "
    "self-contained — an unresolvable $ref is rejected loudly at validation "
    "time (see test-unresolvable-ref-is-fail-loud in the schema deftests)"
)
REGEX_DIALECT_SKIP = (
    "ECMA-262 \\p{...} property escapes are outside Python re; NOT a silent "
    "deviation — such schemas are rejected fail-closed at session.launch by "
    "the meta-schema's format:regex assertion (see "
    "test-admission-rejects-noncompilable-regex in the schema deftests)"
)
# 裁定済み skip(ACP plan U1 / DOE-004 R8): file × group description。
SKIP_GROUPS = {
    ("dynamicRef", "strict-tree schema, guards against misspelled properties"): REMOTE_SKIP,
    ("dynamicRef", "tests for implementation dynamic anchor and reference link"): REMOTE_SKIP,
    ("dynamicRef", "$ref and $dynamicAnchor are independent of order - $defs first"): REMOTE_SKIP,
    ("dynamicRef", "$ref and $dynamicAnchor are independent of order - $ref first"): REMOTE_SKIP,
    ("dynamicRef", "$ref to $dynamicRef finds detached $dynamicAnchor"): REMOTE_SKIP,
    ("vocabulary", "schema that uses custom metaschema with with no validation vocabulary"): REMOTE_SKIP,
    ("pattern", "pattern with Unicode property escape requires unicode mode"): REGEX_DIALECT_SKIP,
    ("patternProperties", "patternProperties with Unicode property escape"): REGEX_DIALECT_SKIP,
}


def _iter_cases():
    for suite_file in sorted(SUITE_DIR.glob("*.json")):
        if suite_file.name in SKIP_FILES:
            continue
        for group in json.loads(suite_file.read_text(encoding="utf-8")):
            skip_reason = SKIP_GROUPS.get((suite_file.stem, group["description"]))
            marks = [pytest.mark.skip(reason=skip_reason)] if skip_reason else []
            for test in group["tests"]:
                case_id = f"{suite_file.stem}::{group['description']}::{test['description']}"
                yield pytest.param(
                    group["schema"], test["data"], test["valid"], id=case_id, marks=marks
                )


CASES = list(_iter_cases())
assert len(CASES) > 1000, f"suite looks truncated: {len(CASES)} cases"


@pytest.mark.parametrize(("schema", "data", "valid"), CASES)
def test_official_suite_case(schema, data, valid) -> None:
    reason = validate_against_schema(data, schema, "payload")
    if valid:
        assert reason is None, f"spec says VALID, adapter rejected: {reason}"
    else:
        assert reason is not None, "spec says INVALID, adapter accepted"
