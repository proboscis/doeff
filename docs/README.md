# docs

This directory holds two kinds of files: documentation for people who use doeff, and records
written while doeff is developed. Only the first kind is user documentation.

## For users

Start at [index.md](index.md). It lists the guides in reading order, from
[Getting Started](01-getting-started.md) to the [API Reference](13-api-reference.md), and the
pages that need components `pip install doeff` does not install.

## Development records

These files explain why the code is the way it is. They may describe plans that changed later,
and they are not kept in step with the current release.

| Path | For | What is inside |
| --- | --- | --- |
| [`adr/`](adr/) | contributors | design decisions written as executable tests (`defadr_*.hy`); the test suite runs them |
| [`design/`](design/) | maintainers | design notes and their diagrams, one directory per topic |
| [`design-checks/`](design-checks/) | maintainers | checks of a design against the code, one directory per check, with the evidence |
| [`history/`](history/) | maintainers | older planning notes, kept as they were written |
| [`crystallization/`](crystallization/), [`proposals/`](proposals/), [`specs/`](specs/), [`impl-requests/`](impl-requests/), [`positioning/`](positioning/) | maintainers | design drafts, proposals, and notes on how doeff compares with other tools |

Most pages at the top of `docs/` that [index.md](index.md) does not link to are also notes for
people who work on doeff.
