// 定義を読む面に出る文字の表 — 面の文字はここからだけ引く(v5・operator 2026-09-29 "instead of 引数/答え use args / return type")。
// ラベルは一般に通じる英語の技術用語(小文字)。実体の種類のバッジ・tags の key・説明・本体は source の語のままで、この表に置かない。
// 戻す時はこの表だけを直す(docs/design/hy-reading-plane/artifacts/v5/design.md 2 節)。

export const LABELS = {
  /** 1 行に出す欄の切り替え(v5 の表) */
  showInLine: 'show in line',
  argsReturnType: 'args / return type',
  effects: 'effects',
  tags: 'tags',
  docFirstLine: 'doc (first line)',
  callersTests: 'callers / tests',
  location: 'location',
  foldAll: 'fold all',
  unfoldAll: 'unfold all',
  /** カードの頭のボタン */
  source: 'source',
  openInEditor: 'open in editor',
  fold: 'fold',
  unfold: 'unfold',
  /** カードの欄 */
  args: 'args',
  returnType: 'return type',
  handles: 'handles',
  handledBy: 'handled by:',
  fields: 'fields',
  values: 'values',
  methods: 'methods',
  bases: 'bases',
  contract: 'contract',
  type: 'type',
  value: 'value',
  /** カードの関係の帯(defeffect・defrecord・defhandler の分は v5 の表) */
  usedBy: 'used by',
  handlers: 'handlers',
  returnedBy: 'returned by',
  acceptedBy: 'accepted by',
  installedAt: 'installed at',
  callers: 'callers',
  callees: 'callees',
  tests: 'tests',
  types: 'types',
  /** 呼び出しの依存の木(v7) */
  callTree: 'call tree',
  root: 'root',
  depth: 'depth',
  close: 'close',
  treeEffects: 'effects in this tree',
  nodes: 'nodes',
  repeats: 'repeats',
  cycles: 'cycles',
  seenAbove: 'seen above',
  cycle: 'cycle',
  pickRoot: 'pick a root',
  /** 左の軸 */
  axis: 'axis',
  kind: 'kind',
  effect: 'effect',
  hasTests: 'has tests',
  noTests: 'no tests',
  /** 面の上の行 */
  clearFilter: 'clear filter',
  definitions: 'definitions',
  /** 値が無い・まだ無い時の短い語 */
  none: 'none',
  noArgs: 'no args',
  inferencePartial: 'inference partial',
  /** 推論が途中の注記の hover — 何が追えなかったか(追えない呼び = repo の外の関数・deff・method) */
  inferencePartialTitle: 'effects behind these calls could not be followed (functions outside the repo, deff, methods)',
  waitingForLinter: 'waiting for linter',
  violations: 'violations',
  hySource: 'Hy source (read only)',
  lispAsIs: 'not drawn yet — shown as written (lisp)',
  warning: 'warning',
  /** 面の左の欄の下の説明(文) */
  axesHint: 'axes can be entered from any side and intersected. counts come from the index (hy-index). a tag chip on a card also filters by its value.',
  /** 面を出せない理由(文) */
  disabled: 'the reading view is turned off by the setting',
  notIndexed: 'this file is not in the Hy index (hy-index) yet'
} as const;

/** 表の鍵。 */
export type LabelKey = keyof typeof LABELS;
