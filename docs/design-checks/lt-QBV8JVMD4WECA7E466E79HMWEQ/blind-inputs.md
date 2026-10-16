# 盲検に渡した入力(A・B 共通の材料と、それぞれの依頼文)

- 渡した材料: 設計本文 design.md(sha256 ba492ac44b862104eb95f696ced775c3f3658dde17685f106167adc36a7e013d — 盲検の前に固定)と、
  作業木 /home/ubuntu/.worktrees/doeff-wt-stall-design-check(HEAD e8d9481453ebea5511faa94ff2c7e109080fc45c)の実コードの読み取り。
- 渡さなかった物: 著者の自己評価・先の Fable 5.1 のレビュー・既知の失敗例・望む結論・親会話。
- 依頼文: skill design-change-validation の references/blind-counterexamples.md の「Aへ渡す依頼文」「Bへ渡す依頼文」をそのまま使い、
  その後に「材料(固定済み)」と「作業の決まり」(作業木を書き換えない・一時の file は mktemp -d の下だけ・機体の python3 を使わない・
  他の会話へ連絡しない)を足した。B には提示する検査として 4 つ(判断の test・模擬の test・static_stub --check・defk の :pre / :post)を
  実体と検出範囲つきで渡した。A には check として判断の test と模擬の test の 2 つを渡した。
- 起動: Agent tool の subagent(general-purpose)を A・B 同時に 2 つ、新しい文脈で起こした(fork・resume なし)。要求 model = opus
  (claude-opus-5-5 の階層)・effort = xhigh。
- 優先順の 1 番目 gpt-6-astra(effort low)は、この会話の Agent tool の model の選択肢(sonnet・opus・haiku・fable)に無く、
  起動手段が無いので試せなかった — 決まりの順の 2 番目(Opus・xhigh)へ落とした。指定の effort xhigh は Agent tool が受け付けた。
- 実際に走った model は Agent tool の答えに出ないので未確認(要求値だけ記録)。
