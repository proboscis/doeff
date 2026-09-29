// defk の見出しと束縛の hover の Markdown を作る純粋な関数(VS Code に触らない)。型と effect の名は定義へ飛ぶ link にする。

import type { LintBinding, LintLocation, LintSignature, LintTypeRef } from '../lint/contract';
import { namedTypes, signatureText, typeText } from './model';

/** 定義へ飛ぶ命令(hover の型と effect の名から呼ぶ)。 */
export const OPEN_LOCATION_COMMAND = 'doeff-runner.defk.openLocation';

/**
 * 名 1 つを、定義の位置へ飛ぶ link の Markdown にする(位置の無い名は文字のまま)— editor の hover の中の実体の名はどれもここを通す
 * (v12 — hover の中の名も押せる・agora-redesign #910 U19b)。
 */
export function locationLink(name: string, location: LintLocation | null): string {
  if (location === null) {
    return `\`${name}\``;
  }
  const args = encodeURIComponent(JSON.stringify([location.path, location.range.start.line, location.range.start.character]));
  return `[\`${name}\`](command:${OPEN_LOCATION_COMMAND}?${args} "${location.path}")`;
}

/** 型の名 1 つを、定義へ飛ぶ link の Markdown にする(定義の無い名は文字のまま)。 */
function typeLink(type: Extract<LintTypeRef, { kind: 'name' }>): string {
  return locationLink(type.name, type.definition);
}

/** 見出しの hover の Markdown(型の流れの文・型と effect の定義への link・effect の答えの分け方・tags)。 */
export function headerHover(signature: LintSignature): string {
  const lines: string[] = [];
  lines.push(`**${signature.kind} ${signature.name}**`);
  lines.push('```');
  lines.push(signatureText(signature));
  lines.push('```');
  if (signature.params.length > 0) {
    lines.push(`引数: ${signature.params.map((p) => `\`${p.name}\` ${p.type === null ? '?' : typeText(p.type)}`).join('・')}`);
  }
  const types = namedTypes([...signature.params.map((p) => p.type), signature.answer, ...signature.raises]);
  if (types.length > 0) {
    lines.push('', `型: ${types.map(typeLink).join('・')}`);
  }
  // 宣言と推論を合わせた一覧(食い違いは linter が違反の場所に出す — ここでは印を付けない)
  const byName = new Map([...(signature.declared ?? []), ...signature.inferred].map((e) => [e.name, e] as const));
  const effects = [...byName.values()];
  if (effects.length > 0) {
    lines.push('');
    lines.push('| effect | 値 | Absent | Raise |');
    lines.push('|---|---|---|---|');
    for (const effect of effects) {
      const name = typeLink({ kind: 'name', name: effect.name, definition: effect.definition });
      const answer = effect.answer === null ? '?' : `\`${typeText(effect.answer)}\``;
      lines.push(`| ${name} | ${answer} | ${effect.absent.map(typeText).join(' ') || '—'} | ${effect.failure.map(typeText).join(' ') || '—'} |`);
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
