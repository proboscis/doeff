# JSON-Schema-Test-Suite (vendored subset)

- Source: https://github.com/json-schema-org/JSON-Schema-Test-Suite
- Commit: 92acb61eb772a932c077d5ffa634ded719d2d738 (fetched 2026-07-06)
- License: MIT (see LICENSE)
- Vendored: tests/draft2020-12 の required ケースのみ(optional/ は除外 —
  format 等の spec-optional 挙動は契約面に含めない)。
- 用途: agentd result-contract validator(sessionhost/schema.hy の
  validate-against-schema)を **JSON Schema 仕様そのもの**に対して検証する
  (ACP plan U1: 正解定義は仕様から取る。実装 parity は正解定義ではない)。
- runner: packages/doeff-agents/tests/test_jsonschema_official_suite.py。
  refRemote.json は remote $ref 解決(HTTP レジストリ)を要するため runner
  で明示 skip — agentd の契約 schema は自己完結が前提(remote $ref を使う
  契約は validate 時に Unresolvable で fail-loud する)。
