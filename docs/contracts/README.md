# docs/contracts — ACP の契約の写し(正本は proboscis/agent-control-plane)

この dir の JSON は **写し** で、正本は ACP の `docs/contracts/` にある。ここで直さない —
正本を直して写し直す。消費者は doeff の agentd(`packages/doeff-agents/src/doeff_agents/sessionhost/acp/`)。

写しと正本の名乗り(正本側の検 `scripts/check_cross_repo_contracts.hy` がこの行を読む):

- `agora-kinds.json` — acp-contract-canon: proboscis/agent-control-plane:docs/contracts/agora-kinds.json

読む欄の宣言 = `reads.json`(schema `acp.contract-reads.v1` — 正本側の検が「読む欄が正本に実在し deprecated を読まない」を撃つ)。
写しの中で閉じる自己整合(読む欄が写しに実在する・版と互換の規則・agentd の `effects.py` が写しとして持つ値との一致)は
`packages/doeff-agents/tests/test_sessionhost_acp.py` の「契約 agora-kinds.json の写しの自己整合」の節が撃つ(agentd の焦点の検)。

段 9f lane 9f-8(agora-redesign #59)で置いた。
