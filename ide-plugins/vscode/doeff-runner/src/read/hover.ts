// 定義を読む面の hover の文(v1 2.5 節「束縛の型・effect の絵の hover」・agora-redesign #910)を組む純粋な関数(VS Code に触らない)。
// 本体の名には、その名の束縛の型(linter の bindings)か引数の型(linter の見出し)、effect の絵とチップには、索引の defeffect の
// 引数と答えと説明の 1 行目を出す。読み方の正本は linter と索引で、ここは並べるだけ。

import { typeText } from '../defk/model';
import type { LintBinding, LintSignature } from '../lint/contract';
import { docFirstLine } from './html';
import { LABELS } from './labels';
import { indexTypeText, type CallGraph } from './tree';

/** 定義 1 つの名の表 — 本体の名の hover に型を引くため。 */
export interface NameScope {
  /** 名 → その名の束縛(source の順) */
  readonly bindings: ReadonlyMap<string, readonly LintBinding[]>;
  /** 引数の名 → 型の綴り */
  readonly params: ReadonlyMap<string, string>;
}

/** 定義の束縛と見出しの引数から名の表を作る(カード 1 枚ごと)。 */
export function nameScope(bindings: readonly LintBinding[], signature: LintSignature | undefined): NameScope {
  const byName = new Map<string, LintBinding[]>();
  const ordered = [...bindings].sort((a, b) => a.range.start.line - b.range.start.line || a.range.start.character - b.range.start.character);
  for (const binding of ordered) {
    byName.set(binding.name, [...(byName.get(binding.name) ?? []), binding]);
  }
  const params = new Map((signature?.params ?? []).map((p) => [p.name, typeText(p.type)]));
  return { bindings: byName, params };
}

/**
 * 本体の line 行(0 始まり)に出る名の hover の文 — その行まで(同じ行を含む)の最後の束縛の型と束縛した行、束縛でなければ
 * 引数の型。どちらでもなければ undefined(hover を付けない)。
 */
export function nameHover(scope: NameScope, name: string, line: number): string | undefined {
  const before = (scope.bindings.get(name) ?? []).filter((b) => b.range.start.line <= line);
  const bound = before.length === 0 ? undefined : before[before.length - 1];
  if (bound !== undefined) {
    return `${name}: ${typeText(bound.type)}\n${LABELS.boundAtLine} ${bound.range.start.line + 1}`;
  }
  const param = scope.params.get(name);
  return param === undefined ? undefined : `${name}: ${param}\n${LABELS.argument}`;
}

/**
 * effect の絵とチップの hover の文 — 索引の defeffect が名で 1 つに決まれば `Name(引数: 型) → 答え` と説明の 1 行目、
 * 決まらなければ(無い・同名が複数)名だけ。
 */
export function effectHover(name: string, graph: CallGraph): string {
  const found = graph.effectsByName.get(name) ?? [];
  const definition = found.length === 1 ? graph.definitions.get(found[0])?.definition : undefined;
  if (definition === undefined) {
    return name;
  }
  const params = definition.paramTypes.map((p) => `${p.name}: ${indexTypeText(p.type)}`).join(', ');
  const answer = definition.answerType === null ? '' : ` → ${indexTypeText(definition.answerType)}`;
  const doc = docFirstLine(definition.docstring);
  return `${name}(${params})${answer}${doc === '' ? '' : `\n${doc}`}`;
}
