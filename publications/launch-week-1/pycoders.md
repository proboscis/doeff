# PyCoder's Weekly: the suggestion for the first post

The four fields of the link suggestion form, filled in for the first post
([docs/articles/algebraic-effects-in-python.md](../../docs/articles/algebraic-effects-in-python.md)).
A suggestion cannot be taken back once sent, so it is sent on the day the post is out.

| Field | Text |
|---|---|
| URL | [the URL of the post, filled in once it is out] |
| Title | Algebraic Effects for Python: Stack Handlers Around One Unchanged Program |
| Description | doeff brings algebraic effects to Python: a handler receives each effect with the rest of the program, so time, concurrency, retries, budgets and I/O become layers you stack around the same code. The post builds one small program that checks two pages every half hour and runs it under five stacks: a test run with fake pages and a virtual clock, a budget, retries, a recorder that sees every effect, and production on the wall clock with an async client. Tests are one of those stacks, and nothing is patched. Every example runs with pip install doeff alone, and the post compares plain Python, effect, effectful, OCaml 5 and Koka. |
| Your Name | proboscis |
