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
| [`crystallization/`](crystallization/) | maintainers | the catalogue of the VM's runtime invariants |
| [`specs/`](specs/) | maintainers | the specification of how a handler's effect annotations route effects |
| [`positioning/`](positioning/) | maintainers | notes on how doeff compares with other tools |

Most pages at the top of `docs/` that [index.md](index.md) does not link to are also notes for
people who work on doeff.
