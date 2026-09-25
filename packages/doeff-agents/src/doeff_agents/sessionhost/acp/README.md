# sessionhost/acp — agentd = ACP(agent-control-plane)への腕

`doeff-sessionhost join --role agentd` の code。ACP に機体を登録し、割り当てられた手番(agent-job)を取って
host(`--role host` = claude / codex の CLI の親)に命令し、turn-record と結果を ACP に書く仲介の process。
この dir 名の `acp` は agent-control-plane の意味で、Agent Client Protocol ではない。

目標の構成では、この判断の全部が k3s の doeff service「手番の実行係」になり、host は機体の doeff worker が受ける
「agent 実行 task」に縮む(agentd・sessionhost・host という名前は目標の図から消える)。呼び名・決定・移る順番の正本 =
`docs/design/agentd-turn-runner/README.md`(repo の根から)。
