# SEDA Scenario Checklist

Status legend:
- ✅ Done (covered by existing fixtures/tests)
- 🚧 In progress / partially covered
- ❌ Not yet implemented

1. ✅ Single `@do` function yielding a primitive effect (baseline smoke test).
2. ✅ `@do` function yielding multiple different effect types sequentially.
3. ✅ `@do` function using `yield from` on another `@do` program.
4. ✅ Nested helper (non-`@do`) calling a `@do` function and returning its result, consumed via `yield`.
5. ✅ Non-`@do` helper returning a `Program.map(...)` result with inline lambda.
6. ✅ Non-`@do` helper returning a `Program.flat_map(...)` that chains two programs.
7. ✅ Usage of `Program.list(...)` combining raw values and programs.
8. ✅ Usage of `Program.tuple(...)` mixing direct yields and nested programs.
9. ✅ Usage of `Program.set(...)` to ensure deduped collections still report unique effects.
10. ✅ Usage of `Program.dict(...)` with mixed values and `Program.map` calls.
11. ✅ Explicit `Program.sequence([...])` invoked inside a `@do` function.
12. ✅ `Program.traverse(...)` pattern emitting effects inside traversal callback (`tests/effect_tracking.rs::scenario_traverse_items`).
13. ✅ `Program.first_success(...)` handling multiple candidates with differing effects (`tests/effect_tracking.rs::scenario_first_success_some`).
14. ✅ `Program.first_some(...)` with lambdas returning optional programs (`tests/effect_tracking.rs::scenario_first_success_some`).
15. ✅ `Program.list` composed with `.map`/`.flat_map` downstream (`tests/effect_tracking.rs::complex_program_structure`).
16. ✅ Effect interception via `.intercept(...)` altering yielded effects (`tests/effect_tracking.rs::scenario_intercept_and_lift`).
17. ✅ `Program.lift(...)` on plain values and existing programs inside a `@do` function (`tests/effect_tracking.rs::scenario_intercept_and_lift`).
18. ✅ `Program.dict(...)` called outside of `@do` context and later yielded (`tests/effect_tracking.rs::scenario_intercept_and_lift`).
19. ❌ Recursive `@do` definition guarded to avoid infinite traversal (mutual recursion).
20. ✅ `@do` functions defined across multiple modules, imported and composed (`tests/effect_tracking.rs::complex_program_structure`).
21. ❌ Effects yielded inside list/dict comprehensions referenced in a `@do` body.
22. ✅ `@do` function wrapped by decorators (other than `@do`) that should still be recognized (`tests/effect_tracking.rs::scenario_comprehension_decorated_methods`).
23. ✅ `@do` function defined as a class method (both `@classmethod` and instance method cases) (`tests/effect_tracking.rs::scenario_comprehension_decorated_methods`).
24. ❌ Async `@do` variant (if supported) yielding async-aware effects.
25. ✅ `@do` functions using pattern matching (PEP 634) before yielding effects (`tests/effect_tracking.rs::scenario_pattern_try_dataclass`).
26. ✅ `@do` functions with `try/except` around yields (should warn about unsupported pattern) (`tests/effect_tracking.rs::scenario_pattern_try_dataclass`).
27. ❌ Usage of custom effect types registered via config (TOML-driven detection).
28. ❌ Integration with `Program.first_success` combined with `Program.dict` outputs.
29. ✅ `Program` values stored in dataclasses or containers before being yielded later (`tests/effect_tracking.rs::scenario_pattern_try_dataclass`).
30. ✅ Large orchestrator function combining 10+ helper programs across map/flat_map/dict/sequence (fixture `doeff-test-target`).

Keep this list updated as new fixtures/tests land, and link to paths for traceability.
