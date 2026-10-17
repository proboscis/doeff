# 設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4(card ki-d51b7baa38bc)

実行環境の root の作り直しで、前の root と変わらない file(repo の木と .pyc)を書き直さない変更(doeff L2515 — main 869980ffc・9fe8c330b)の
設計検証。盲検 A・B の所見への直しは、この記録と同じ branch の commit「doeff-hy code_store に .pyc の bytes の組み方…」(code_store の entry_pyc・link_entry_pyc と ADR-DOE-BUILD-002 の R3b の law)。

| file | 中身 |
|---|---|
| report.json | 報告(version 1)— 検査 agentcli.ai.entry.design_check_io.verify_report の問題 0 |
| design.md | 設計の本文(構成の図・責務・主張と実測・強制・盲検・限界) |
| design-pre-blind.md | 盲検の前に保存した設計と主張(commit f207e4d36) |
| blind-input.md・blind-a-prompt.md・blind-b-prompt.md | 盲検に渡した入力と依頼文 |
| blind-meta.md | 盲検の起こし方(model・effort・識別子・所要・fallback) |
| blind-a-reply.md・blind-b-reply.md | 盲検の返答(加工なし) |
| evidence/ | 再現と確かめの記録(log・script・盲検 B の差分) |
