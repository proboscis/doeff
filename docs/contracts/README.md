# docs/contracts — ACP の契約の写し(正本は proboscis/agent-control-plane)

この dir の JSON は **生成物の写し** で、正本は ACP の `docs/contracts/` にある。ここで直さない。
消費者は doeff の agentd(`packages/doeff-agents/src/doeff_agents/sessionhost/acp/`)。

pin の定義点は repo の root の `contracts.lock` の 1 点(ACP の commit 1 つ + 写しごとの sha256)で、
写しを作る口は `scripts/sync_contracts.py` の 1 本(段 11 lane 11e・agora-redesign #126):

```sh
python3 scripts/sync_contracts.py --pin   # pin を ACP の origin/main の先端へ進め、写しも生成する
python3 scripts/sync_contracts.py         # 検 — pin の commit の正本との差分 0(ACP の checkout が要る)
python3 scripts/sync_contracts.py --check-copies   # 写しの中で閉じる検(checkout が要らない)
```

⚠ 正本の repo は private で、この repo は公開。だから既定 pytest が撃つのは**写しの中で閉じる検**
(写しが pin の sha256 ちょうど・版と互換の規則・読む欄の実在・正本の名乗り)で、正本との byte 一致は
checkout を持てる機体の `python3 scripts/sync_contracts.py` と ACP 側の `scripts/check_cross_repo_contracts.hy`
が撃つ。契約を変える便の順は、ACP に 1 commit → この repo で pin を進める 1 commit(生成物の更新を含む)。

写しと正本の名乗り(正本側の検 `scripts/check_cross_repo_contracts.hy` がこの行を読む):

- `agora-kinds.json` — acp-contract-canon: proboscis/agent-control-plane:docs/contracts/agora-kinds.json
- `read-freshness.json` — acp-contract-canon: proboscis/agent-control-plane:docs/contracts/read-freshness.json

読む欄の宣言 = `reads.json`(schema `acp.contract-reads.v1` — 正本側の検が「読む欄が正本に実在し deprecated を読まない」を撃つ)。
写しの中で閉じる自己整合(読む欄が写しに実在する・版と互換の規則・agentd の `effects.py` が写しとして持つ値との一致)は
`packages/doeff-agents/tests/test_sessionhost_acp.py` の「契約 agora-kinds.json の写しの自己整合」の節が撃つ(agentd の焦点の検)。

段 9f lane 9f-8(agora-redesign #59)で置いた。
