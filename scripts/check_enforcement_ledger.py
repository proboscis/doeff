#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Enforcement ledger — the single home for generation and reconciliation (ADR-DOE-ENFORCE-001 R5 / R7 / R9).

The ledger docs/adr/enforcement-ledger.json is a **generated artifact**; never write it by hand. This file builds
the list of enforcement assets from the tree:

- defadr_files                 = file names of docs/adr/defadr_*.hy
- semgrep_rules                = rule ids in .semgrep.yaml
- adr_laws                     = (law name ...) in the ADRs -- "<ADR id> <law name>"
- adr_deftest_enforcements     = (deftest name ...) in the ADRs -- "<ADR id> <deftest name>"
- adr_defsemgrep_enforcements  = (defsemgrep name ...) in the ADRs -- "<ADR id> <defsemgrep name>"

ADRs are read as forms, following Hy's reading rules (strings, `;` comments, forms dropped with `#_`, and the
inside of `#[[...]]` strings are not counted) -- a literal-text count (occurrences of `"(law "`) also counted
spellings inside comments and strings (measured 2026-09-28: 134 laws against 132 forms, 82 deftests against 80
forms). tests/test_enforcement_ledger.py checks, against every real ADR, that this reader returns the same answer
as the Hy reader.

The ledger is a list of item names, not counts, so when two parallel changes each add a different item, git merges
them correctly line by line (a count ledger silently merged to 105 when two changes both wrote the same "104 -> 105"
-- 2026-09-17 ed98775a). A removed item remains in the ledger diff by name. stdlib only (no venv, no dependencies --
invoked through `uv run --script`; the machine's python is not used) -- the same accounting is used from the git
pre-commit hook (scripts/git-hooks/pre-commit, .pre-commit-config.yaml) and from the default pytest run
(tests/test_enforcement_ledger.py).

modes:
  (default)  reconcile the working tree against the ledger (exact match -- the same surface as the daily pytest)
  --staged   reconcile the git index (the cross-section that goes into the commit) -- for the hook. Passes if it
             matches the ledger. Even if it does not match, passes when the items this commit moved (the
             additions/removals from HEAD) are reflected as-is in the ledger's additions/removals, and only
             reports drift that already existed in HEAD (that is the responsibility of the commit that
             introduced it; it does not block later commits).
  --write    generate the ledger from the working tree and write it. Items dropped from the ledger are reported by name.
  --root=P   explicit repo root (default = the git toplevel of cwd)

exit code: 0 = match (or --write succeeded) / 1 = mismatch or an ADR cannot be read / 2 = execution environment failure (git cannot be invoked, etc.).
"""

import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

LEDGER_PATH = "docs/adr/enforcement-ledger.json"
SEMGREP_PATH = ".semgrep.yaml"
ADR_DIR = "docs/adr"
ADR_GLOB = "docs/adr/defadr_*.hy"
ADR_NAME = re.compile(r"defadr_[^/]*\.hy")

KEYS = (
    "defadr_files",
    "semgrep_rules",
    "adr_laws",
    "adr_deftest_enforcements",
    "adr_defsemgrep_enforcements",
)
#: Heads of the forms counted inside an ADR -> ledger keys.
FORM_KEYS = {
    "law": "adr_laws",
    "deftest": "adr_deftest_enforcements",
    "defsemgrep": "adr_defsemgrep_enforcements",
}
LEDGER_COMMENT = (
    "Generated file — do not edit by hand. `make enforcement-ledger` (= scripts/check_enforcement_ledger.py --write) "
    "builds it from the tree (ADR-DOE-ENFORCE-001 R5 / R9). Each entry is the name of one enforcement asset; a removed "
    "entry stays visible by name in this file's diff. The commit hook and the default pytest check that this list matches the tree."
)

_CLOSERS = {"(": ")", "[": "]", "{": "}", "#(": ")", "#{": "}"}
_DELIMITERS = frozenset('()[]{}"; \t\r\n\f')
_SPACE = frozenset(" \t\r\n\f")
_SEMGREP_RULE = re.compile(r"^  - id:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)


class UnreadableAdrError(ValueError):
    """The ADR file cannot be read as Hy forms (unclosed parenthesis or string)."""


# ---------------------------------------------------------------------------
# Hy reading (only as much as is needed for counting).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Atom:
    """Spelling (symbol, keyword, number, quote prefixes ' ` ~ ~@, prefixes such as `#^`)."""

    text: str


@dataclass(frozen=True)
class Text:
    """String ("..." and #[[...]]). The spelling inside is not counted as forms."""

    content: str


@dataclass(frozen=True)
class Form:
    """A parenthesized sequence. opener is one of ( [ { #( #{."""

    opener: str
    children: list = field(default_factory=list)

    def head(self) -> str | None:
        if self.opener == "(" and self.children and isinstance(self.children[0], Atom):
            return self.children[0].text
        return None

    def name(self) -> str:
        """Spelling of the second element (the defined name)."""
        second = self.children[1] if len(self.children) > 1 else None
        if isinstance(second, Atom):
            return second.text
        if isinstance(second, Text):
            return second.content
        return "<名なし>"  # kept: also used by tests/test_enforcement_ledger.py and recorded in ledger names ("<unnamed>")


class _Reader:
    """Reads the text of one file from the top. _mut_pos is the next position to read."""

    def __init__(self, text: str, where: str) -> None:
        self.text = text
        self.where = where
        self._mut_pos = 0

    def fail(self, what: str) -> UnreadableAdrError:
        return UnreadableAdrError(f"{self.where}: {what}(offset {self._mut_pos})")

    def skip_space(self) -> None:
        text = self.text
        while self._mut_pos < len(text):
            if text[self._mut_pos] in _SPACE:
                self._mut_pos += 1
            elif text[self._mut_pos] == ";":
                end = text.find("\n", self._mut_pos)
                self._mut_pos = len(text) if end < 0 else end + 1
            else:
                return

    def at_end(self) -> bool:
        self.skip_space()
        return self._mut_pos >= len(self.text)

    def string(self) -> Text:
        start = self._mut_pos + 1
        pos = start
        while pos < len(self.text):
            if self.text[pos] == "\\":
                pos += 2
            elif self.text[pos] == '"':
                self._mut_pos = pos + 1
                return Text(self.text[start:pos])
            else:
                pos += 1
        raise self.fail("unclosed string")

    def bracket_string(self) -> Text:
        """`#[delim[ ... ]delim]` (pos is the position of `#`)."""
        open_end = self.text.find("[", self._mut_pos + 2)
        if open_end < 0:
            raise self.fail("unclosed opening of a #[ string")
        closer = f"]{self.text[self._mut_pos + 2 : open_end]}]"
        end = self.text.find(closer, open_end + 1)
        if end < 0:
            raise self.fail(f"unclosed string that should close with {closer}")
        self._mut_pos = end + len(closer)
        return Text(self.text[open_end + 1 : end])

    def atom(self) -> Atom:
        start = self._mut_pos
        while self._mut_pos < len(self.text) and self.text[self._mut_pos] not in _DELIMITERS:
            self._mut_pos += 1
        return Atom(self.text[start : self._mut_pos])

    def sequence(self, opener: str) -> Form:
        """The sequence from just after an opening bracket to the matching closing bracket."""
        closer = _CLOSERS[opener]
        children = []
        while not self.at_end():
            if self.text[self._mut_pos] == closer:
                self._mut_pos += 1
                return Form(opener, children)
            child = self.form()
            if child is not None:
                children.append(child)
        raise self.fail(f"{opener!r} is not closed")

    def hashed(self) -> Form | Text | Atom | None:
        """A form starting with `#`. `#(` `#{` are sequences, `#[` is a string, `#_` drops the next form (None), anything else is a spelling."""
        following = self.text[self._mut_pos + 1 : self._mut_pos + 2]
        if following in ("(", "{"):
            self._mut_pos += 2
            return self.sequence("#" + following)
        if following == "[":
            return self.bracket_string()
        if following == "_":
            self._mut_pos += 2
            if self.at_end():
                raise self.fail("no form after #_")
            self.form()
            return None
        return self.atom()

    def form(self) -> Form | Text | Atom | None:
        """Reads one form from pos. A dropped form (`#_`) is None."""
        char = self.text[self._mut_pos]
        if char in ")]}":
            raise self.fail(f"unmatched closing bracket {char!r}")
        if char in "([{":
            self._mut_pos += 1
            return self.sequence(char)
        if char == '"':
            return self.string()
        if char == "#":
            return self.hashed()
        if char in "'`~":
            # Quote prefixes (' ` ~ ~@) are not forms themselves -- read the next form as an element of the sequence.
            step = 2 if self.text.startswith("~@", self._mut_pos) else 1
            self._mut_pos += step
            return Atom(self.text[self._mut_pos - step : self._mut_pos])
        return self.atom()


def read_forms(text: str, where: str = "<adr>") -> list:
    """The sequence of top-level forms of a Hy file (strings, comments, and `#_` forms do not become countable forms)."""
    reader = _Reader(text, where)
    if text.startswith("#!"):
        end = text.find("\n")
        reader._mut_pos = len(text) if end < 0 else end + 1
    forms = []
    while not reader.at_end():
        form = reader.form()
        if form is not None:
            forms.append(form)
    return forms


def adr_items(file_name: str, text: str) -> dict[str, list[str]]:
    """The law / deftest / defsemgrep items of one ADR (owner = id of the enclosing defadr, or the file name if none)."""
    items: dict[str, list[str]] = {key: [] for key in FORM_KEYS.values()}

    def visit(node: object, owner: str) -> None:
        if not isinstance(node, Form):
            return
        head = node.head()
        if head == "defadr":
            owner = node.name()
        elif head in FORM_KEYS:
            items[FORM_KEYS[head]].append(f"{owner} {node.name()}")
        for child in node.children:
            visit(child, owner)

    for form in read_forms(text, file_name):
        visit(form, file_name)
    return items


def inventory_from_texts(adr_texts: dict[str, str], semgrep_text: str) -> dict[str, list[str]]:
    """List of enforcement assets in the tree (a list of names per key; duplicates are kept as counts)."""
    found: dict[str, list[str]] = {key: [] for key in KEYS}
    found["defadr_files"] = list(adr_texts)
    found["semgrep_rules"] = _SEMGREP_RULE.findall(semgrep_text)
    for file_name, text in adr_texts.items():
        for key, names in adr_items(file_name, text).items():
            found[key].extend(names)
    return {key: sorted(names) for key, names in found.items()}


def counts(inventory: dict[str, list[str]]) -> dict[str, int]:
    return {key: len(inventory.get(key, [])) for key in KEYS}


# ---------------------------------------------------------------------------
# Reading/writing the ledger and diffs.
# ---------------------------------------------------------------------------


def ledger_from_text(text: str) -> dict[str, list[str]]:
    """The ledger list. The old format (counts per key) cannot be read as a list, so ValueError."""
    data = json.loads(text)
    ledger = {key: value for key, value in data.items() if not key.startswith("_")}
    for key, value in ledger.items():
        if not isinstance(value, list):
            raise ValueError(f"ledger {key} is not a list of names (old count format) -- regenerate")
    return {key: sorted(str(item) for item in value) for key, value in ledger.items()}


def ledger_text(inventory: dict[str, list[str]]) -> str:
    data = {"_comment": LEDGER_COMMENT, **{key: inventory[key] for key in KEYS}}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class Change:
    """Diff for one key -- names only in after (added) and names only in before (removed)."""

    added: list
    removed: list


def difference(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, Change]:
    """Diff per key (duplicates are compared as counts). Keys with no diff are omitted."""
    changes = {
        key: Change(
            sorted((Counter(after.get(key, [])) - Counter(before.get(key, []))).elements()),
            sorted((Counter(before.get(key, [])) - Counter(after.get(key, []))).elements()),
        )
        for key in sorted(set(before) | set(after))
    }
    return {key: change for key, change in changes.items() if change.added or change.removed}


# ---------------------------------------------------------------------------
# Tree snapshots (working tree / git index / HEAD).
# ---------------------------------------------------------------------------


def worktree_inventory(root: Path) -> dict[str, list[str]]:
    adr_files = sorted(root.glob(ADR_GLOB))
    return inventory_from_texts(
        {path.name: path.read_text(encoding="utf-8") for path in adr_files},
        (root / SEMGREP_PATH).read_text(encoding="utf-8"),
    )


def worktree_ledger(root: Path) -> dict[str, list[str]]:
    return ledger_from_text((root / LEDGER_PATH).read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _adr_names(listing: str) -> list[str]:
    """Only defadr_*.hy directly under docs/adr from git's listing (the same population as the working-tree glob)."""
    return sorted(
        name
        for name in listing.splitlines()
        if name.rsplit("/", 1)[0] == ADR_DIR and ADR_NAME.fullmatch(name.rsplit("/", 1)[-1])
    )


def _revision_inventory(root: Path, revision: str) -> dict[str, list[str]]:
    """The listing of a git snapshot. revision = "" is the index; "HEAD" etc. are commits."""
    listing = (
        _git(root, "ls-files", "--cached", "--", ADR_DIR)
        if not revision
        else _git(root, "ls-tree", "-r", "--name-only", revision, "--", ADR_DIR)
    )
    return inventory_from_texts(
        {
            name.rsplit("/", 1)[-1]: _git(root, "show", f"{revision}:{name}")
            for name in _adr_names(listing)
        },
        _git(root, "show", f"{revision}:{SEMGREP_PATH}"),
    )


def staged_inventory(root: Path) -> dict[str, list[str]]:
    return _revision_inventory(root, "")


def staged_ledger(root: Path) -> dict[str, list[str]]:
    return ledger_from_text(_git(root, "show", f":{LEDGER_PATH}"))


@dataclass(frozen=True)
class HeadSnapshot:
    """The HEAD snapshot -- the tree listing and the ledger."""

    actual: dict
    ledger: dict


def head_snapshot(root: Path) -> HeadSnapshot | None:
    """The HEAD snapshot. None (cannot be judged) when there is no HEAD, the HEAD ledger is in the old format, or an ADR in HEAD cannot be read."""
    try:
        return HeadSnapshot(
            _revision_inventory(root, "HEAD"),
            ledger_from_text(_git(root, "show", f"HEAD:{LEDGER_PATH}")),
        )
    except (RuntimeError, ValueError):
        return None


def unrecorded_own_change(head: HeadSnapshot, actual: dict, ledger: dict) -> dict[str, Change]:
    """The part of this commit's changes (HEAD -> index) that is not reflected in the ledger's changes.

    Per key, counts (tree change) - (ledger change) with sign: positive = added to the tree but missing from the ledger,
    negative = remains in the ledger / only the ledger moved. If empty, this commit's changes are reflected in the ledger as-is.
    """
    changes = {}
    for key in sorted(set(actual) | set(ledger) | set(head.actual) | set(head.ledger)):
        signed = Counter(actual.get(key, []))
        signed.subtract(head.actual.get(key, []))
        signed.subtract(ledger.get(key, []))
        signed.update(head.ledger.get(key, []))
        added = sorted(name for name, n in signed.items() if n > 0 for _ in range(n))
        removed = sorted(name for name, n in signed.items() if n < 0 for _ in range(-n))
        if added or removed:
            changes[key] = Change(added, removed)
    return changes


# ---------------------------------------------------------------------------
# Reports.
# ---------------------------------------------------------------------------

_SHOWN = 12


def _listing(label: str, names: list) -> list[str]:
    shown = [f"      {name}" for name in names[:_SHOWN]]
    rest = [f"      ... and {len(names) - _SHOWN} more"] if len(names) > _SHOWN else []
    return [f"    {label} {len(names)} items:", *shown, *rest] if names else []


def describe(diff: dict[str, Change]) -> str:
    return "\n".join(
        line
        for key, change in diff.items()
        for line in (
            f"  {key}:",
            *_listing("in the tree but not in the ledger (added / not yet recorded in the ledger)", change.added),
            *_listing("in the ledger but not in the tree (removed)", change.removed),
        )
    )


def _repair_hint(root: Path) -> str:
    return (
        "How to fix: the ledger is a generated artifact -- regenerate it with `make enforcement-ledger`"
        "(= scripts/check_enforcement_ledger.py --write), confirm that the \"removed\" items in the diff are"
        f"intended deletions, then stage the ledger. Ledger: {root / LEDGER_PATH}"
    )


def _mismatch_message(face: str, diff: dict[str, Change], root: Path) -> str:
    return (
        f"The enforcement ledger and the tree ({face}) disagree -- ADR-DOE-ENFORCE-001 R5 / R9.\n"
        f"{describe(diff)}\n{_repair_hint(root)}"
    )


def _unreadable_ledger(root: Path, error: ValueError) -> int:
    print(f"Cannot read the enforcement ledger -- {error}\n{_repair_hint(root)}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def check_worktree(root: Path) -> int:
    actual = worktree_inventory(root)
    try:
        ledger = worktree_ledger(root)
    except ValueError as error:
        return _unreadable_ledger(root, error)
    drift = difference(ledger, actual)
    if drift:
        print(_mismatch_message("working tree", drift, root), file=sys.stderr)
    return 1 if drift else 0


def check_staged(root: Path) -> int:
    actual = staged_inventory(root)
    try:
        ledger = staged_ledger(root)
    except ValueError as error:
        return _unreadable_ledger(root, error)
    drift = difference(ledger, actual)
    if not drift:
        return 0
    head = head_snapshot(root)
    if head is None:
        print(_mismatch_message("staged snapshot", drift, root), file=sys.stderr)
        return 1
    own = unrecorded_own_change(head, actual, ledger)
    inherited = difference(head.ledger, head.actual)
    inherited_note = (
        f"\n  (for reference) drift already in HEAD -- not this commit's responsibility:\n{describe(inherited)}"
        if inherited
        else ""
    )
    if not own:
        # This commit's changes are reflected in the ledger. The remaining drift already existed in HEAD.
        print(
            "enforcement ledger: this commit's own changes are reflected in the ledger (passing). Drift already in HEAD "
            "remains -- wait for the fix of the commit that brought it in, or fix it here.\n"
            f"{describe(drift)}\n{_repair_hint(root)}",
            file=sys.stderr,
        )
        return 0
    # Red comes only from this commit's own changes (HEAD drift does not turn it red -- R9). Bypassing with --no-verify
    # on the grounds that "the mainline was already drifted" makes you the one who carries drift into the mainline (2026-09-28 b32941c3).
    print(
        "enforcement ledger: the enforcement changed by this commit is not reflected in the ledger -- ADR-DOE-ENFORCE-001 R5 / R9.\n"
        "This red does not come from drift in HEAD (the mainline) but from this commit's own changes. Do not bypass it with --no-verify; "
        "`make enforcement-ledger` and stage it.\n"
        f"  Of this commit's own changes, the part missing from the ledger:\n{describe(own)}{inherited_note}\n{_repair_hint(root)}",
        file=sys.stderr,
    )
    return 1


def write_ledger(root: Path) -> int:
    actual = worktree_inventory(root)
    path = root / LEDGER_PATH
    try:
        previous = ledger_from_text(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    path.write_text(ledger_text(actual), encoding="utf-8")
    summary = ", ".join(f"{key} {value}" for key, value in counts(actual).items())
    print(f"Generated the enforcement ledger ({summary}): {path}", file=sys.stderr)
    removed = {
        key: Change([], change.removed)
        for key, change in difference(previous, actual).items()
        if previous and change.removed
    }
    if removed:
        print(
            "WARNING: items dropped from the ledger (enforcement that disappeared from the tree) -- confirm the deletion is intended before staging:\n"
            + describe(removed),
            file=sys.stderr,
        )
    return 0


def main(argv: list[str]) -> int:
    roots = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--root=")]
    if "--write" in argv:
        run = write_ledger
    elif "--staged" in argv:
        run = check_staged
    else:
        run = check_worktree
    try:
        root = (
            Path(roots[-1]).resolve()
            if roots
            else Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        )
        return run(root)
    except UnreadableAdrError as error:
        print(f"check_enforcement_ledger: an ADR cannot be read as Hy forms -- {error}", file=sys.stderr)
        return 1
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"check_enforcement_ledger: cannot run the reconciliation -- {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
