// Rust の doc-linter の JSON を検証し、共通の表示形式へ写す。文章の良し悪しをここで再判定しない。
import type { LintViolation } from './contract';

type Row = { readonly [key: string]: unknown };
function row(value: unknown): Row {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('object が必要です');
  }
  return value as Row;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error('空でない文字列が必要です');
  }
  return value;
}
function list(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error('配列が必要です');
  }
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {
    throw new Error('正の行番号が必要です');
  }
  return value;
}
function probability(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error('確率が不正です');
  }
  return value;
}

/** 検査できなかった事実も同じ表示へ載せ、違反ゼロの成功と区別する。 */
export function docFailure(filePath: string, reason: string, line = 0, end = line, textLength = 1): LintViolation {
  return {
    rule: 'DOC000',
    law: null,
    adr: null,
    severity: 'warning',
    path: filePath,
    range: { start: { line, character: 0 }, end: { line: end, character: textLength } },
    message: `未測定: ${reason}`,
    hint: null,
    key: null,
    registered: false,
    baseSeverity: 'warning',
    standing: 'new',
    level: 'minor',
    explanation: null,
    source: 'doc-linter',
    probability: null,
  };
}

/** CLI が返す対象の行範囲・状態・出典を確認する。不完全な応答は丸ごと拒否する。 */
export function parseDocReport(raw: string, filePath: string, source: string): readonly LintViolation[] {
  const parsed: unknown = JSON.parse(raw);
  const report = row(parsed);
  if (report.schema_version !== 1) {
    throw new Error('doc-linter の出力の版が違います（0.2.1 以降が必要です）');
  }
  text(report.policy_version);
  const results = list(report.results);
  if (results.length === 0 && report.status !== 'not-applicable') {
    throw new Error('対象なしの理由がありません');
  }
  const lines = source.split('\n');
  return results.flatMap((rawResult) => {
    const result = row(rawResult);
    const unit = row(result.unit);
    if (unit.source !== filePath) {
      throw new Error('別ファイルの結果です');
    }
    text(unit.text);
    const start = integer(unit.line) - 1;
    const end = integer(unit.end_line) - 1;
    if (end < start || end >= lines.length) {
      throw new Error('診断が本文の行範囲を超えています');
    }
    const kind = unit.kind;
    if (kind !== 'function' && kind !== 'comment' && kind !== 'document') {
      throw new Error('文章の種類が不正です');
    }
    const base: LintViolation = { ...docFailure(filePath, '', start, end, lines[end].length), documentKind: kind };
    if (result.status === 'unmeasured' && result.measurement === 'unmeasured') {
      return [{ ...base, message: `未測定: ${text(result.reason)}` }];
    }
    if (!['pass', 'review', 'violation'].includes(text(result.status)) || result.measurement !== 'measured') {
      throw new Error('測定状態が不正です');
    }
    text(result.model);
    const scores = list(result.scores).map((value) => {
      const score = row(value);
      return { rule: text(score.rule), p: probability(score.probability) };
    });
    if (
      scores
        .map((s) => s.rule)
        .sort()
        .join(',') !== 'DOC001,DOC002,DOC003,DOC004'
    ) {
      throw new Error('観点が欠けています');
    }
    const findings = list(result.findings).map((value) => {
      const finding = row(value);
      const rule = text(finding.rule);
      const p = probability(finding.probability);
      if (finding.message_origin !== 'rule' || !scores.some((s) => s.rule === rule && s.p === p)) {
        throw new Error('診断の出典が不正です');
      }
      const review = result.status === 'review';
      return { ...base, rule, message: `${review ? '要確認: ' : ''}${text(finding.message)}`, probability: p };
    });
    if (result.status === 'pass') {
      if (findings.length > 0) {
        throw new Error('合格と診断が矛盾しています');
      }
      return [];
    }
    if (findings.length === 0) {
      throw new Error('診断の理由がありません');
    }
    return findings;
  });
}
