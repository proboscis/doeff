// defk の見出しと束縛の hover の Markdown を作る純粋な関数(VS Code に触らない)。型と effect の名は定義へ飛ぶ link にする。

import type { LintBinding, LintSignature, LintTypeRef } from '../lint/contract';
import { namedTypes, shownEffects, signatureText, typeText } from './model';

/** 定義へ飛ぶ命令(hover の型と effect の名から呼ぶ)。 */
export const OPEN_LOCATION_COMMAND = 'doeff-runner.defk.openLocation';

/** 型の名 1 つを、定義へ飛ぶ link の Markdown にする(定義の無い名は文字のまま)。 */
function typeLink(type: Extract<LintTypeRef, { kind: 'name' }>): string {
  if (type.definition === null) {
    return `\`${type.name}\``;
  }
  const args = encodeURIComponent(JSON.stringify([type.definition.path, type.definition.range.start.line, type.definition.range.start.character]));
  return `[\`${type.name}\`](command:${OPEN_LOCATION_COMMAND}?${args} "${type.definition.path}")`;
}

/** 見出しの hover の Markdown(型の流れの文・型と effect の定義への link・effect の答えの分け方・tags)。 */
export function headerHover(signature: LintSignature): string {
  const lines: string[] = [];
  lines.push(`**${signature.kind} ${signature.name}**`);
  lines.push('```');
  lines.push(signatureText(signature));
  lines.push('```');
  const types = namedTypes([...signature.params.map((p) => p.type), signature.answer, ...signature.raises]);
  if (types.length > 0) {
    lines.push(`型: ${types.map(typeLink).join('・')}`);
  }
  const effects = shownEffects(signature);
  if (effects.length > 0) {
    lines.push('');
    lines.push('| effect | 宣言と推論 | 値 | Absent | Raise |');
    lines.push('|---|---|---|---|---|');
    const state = { both: '宣言 = 推論', undeclared: '推論だけ(:effects に無い)', unused: '宣言だけ(起こしていない)', inferred: '推論(宣言なし)' } as const;
    for (const { effect, state: s } of effects) {
      const name = typeLink({ kind: 'name', name: effect.name, definition: effect.definition });
      const answer = effect.answer === null ? '?' : `\`${typeText(effect.answer)}\``;
      lines.push(`| ${name} | ${state[s]} | ${answer} | ${effect.absent.map(typeText).join(' ') || '—'} | ${effect.failure.map(typeText).join(' ') || '—'} |`);
    }
  }
  if (signature.tags.size > 0) {
    lines.push('');
    lines.push(`tags: ${[...signature.tags].map(([k, v]) => `\`${k}: ${v}\``).join(' ')}`);
  }
  lines.push('');
  lines.push('_型の読みは doeff-linter(editor-json 版 2)。file の中身は元の lisp のまま — カーソルを定義に入れると元の文字を見せる。_');
  return lines.join('\n');
}

/** 束縛の hover の Markdown。 */
export function bindingHover(binding: LintBinding): string {
  const origin = {
    annotation: '`<-` の注釈',
    effect: '撃った effect の値の答え',
    call: '呼んだ定義の答え',
    literal: '字面',
    constructor: '型の名の呼び',
    var: '`var` で宣言した型',
    unknown: '分からない(linter が型を読めなかった)'
  } as const;
  const types = namedTypes([binding.type, ...binding.raises]);
  const lines = [`**${binding.form} ${binding.name}** : \`${binding.type === null ? '?' : typeText(binding.type)}\` — ${origin[binding.origin]}`];
  if (binding.absent) {
    lines.push('', 'Maybe — 無い時は呼び手へ Absent が抜ける');
  }
  if (binding.raises.length > 0) {
    lines.push('', `Raise: ${binding.raises.map(typeText).join('・')}`);
  }
  if (types.length > 0) {
    lines.push('', `型: ${types.map(typeLink).join('・')}`);
  }
  return lines.join('\n');
}
