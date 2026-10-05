// Rust の workspace ストリームの境界。位置とパスを検証してから表示へ渡す。
import * as path from 'path';
import type { LintViolation } from './contract';
import { docFailure, parseDocReport } from './docContract';

export interface TermPosition {
  readonly line: number;
  readonly character: number;
}
export interface TermLocation {
  readonly path: string;
  readonly start: TermPosition;
  readonly end: TermPosition;
}
export interface TermDefinition {
  readonly id: string;
  readonly title: string;
  readonly explanation: string;
  readonly location: TermLocation;
}
export interface TermReference {
  readonly id: string;
  readonly label: string;
  readonly location: TermLocation;
}
export interface DocIndex {
  readonly definitions: readonly TermDefinition[];
  readonly references: readonly TermReference[];
}
export interface Counts {
  readonly files: number;
  readonly total: number;
  readonly completed: number;
  readonly cacheHits: number;
  readonly unmeasured: number;
}
export type DocProgress = Counts &
  ({ readonly phase: 'queued' | 'running' | 'complete' } | { readonly phase: 'failed'; readonly reason: string });
export const EMPTY_COUNTS: Counts = { files: 0, total: 0, completed: 0, cacheHits: 0, unmeasured: 0 };
export const EMPTY_INDEX: DocIndex = { definitions: [], references: [] };
export interface DocSnapshot {
  readonly files: ReadonlyMap<string, string>;
  readonly index: DocIndex;
  readonly issues: readonly LintViolation[];
  readonly total: number;
}
export type WorkspaceEvent =
  | { readonly event: 'index'; readonly snapshot: DocSnapshot }
  | {
      readonly event: 'result';
      readonly path: string;
      readonly unit: string;
      readonly violations: readonly LintViolation[];
      readonly completed: number;
      readonly cacheHits: number;
      readonly unmeasured: number;
    }
  | { readonly event: 'done'; readonly code: number; readonly completed: number; readonly cacheHits: number; readonly unmeasured: number };

function row(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('object が必要です');
  }
  return value as Record<string, unknown>; // JSON 境界の object 検証。各欄は以下で検査する。
}
function string(value: unknown): string {
  if (typeof value !== 'string') {
    throw new Error('文字列が必要です');
  }
  return value;
}
function array(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error('配列が必要です');
  }
  return value;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error('非負整数が必要です');
  }
  return value;
}
function inside(root: string, value: unknown): string {
  const file = string(value);
  const relative = path.relative(root, file);
  if (!path.isAbsolute(file) || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('workspace 外のパスです');
  }
  return file;
}
function position(value: unknown): TermPosition {
  const p = row(value);
  return { line: count(p.line), character: count(p.character) };
}
function location(root: string, value: unknown, files: ReadonlyMap<string, string>): TermLocation {
  const v = row(value);
  const file = inside(root, v.path);
  const start = position(v.start);
  const end = position(v.end);
  if (end.line < start.line || (end.line === start.line && end.character < start.character)) {
    throw new Error('位置の順序が不正です');
  }
  const source = files.get(file);
  if (source !== undefined) {
    const lines = source.split('\n');
    for (const p of [start, end]) {
      if (p.line >= lines.length || p.character > lines[p.line].length) {
        throw new Error('位置が本文の外です');
      }
    }
  }
  return { path: file, start, end };
}

export function readWorkspaceEvent(raw: string, root: string, snapshot?: DocSnapshot): WorkspaceEvent {
  const v = row(JSON.parse(raw));
  if (v.event === 'index') {
    if (v.schema_version !== 1) {
      throw new Error('workspace 出力の版が不正です');
    }
    const s = row(v.snapshot);
    const index = row(s.index);
    const files = new Map<string, string>();
    for (const rawFile of array(s.files)) {
      const f = row(rawFile);
      const p = inside(root, f.path);
      if (files.has(p)) {
        throw new Error('ファイルが重複しています');
      }
      files.set(p, string(f.text));
    }
    const definitions = array(index.definitions).map((rawDef): TermDefinition => {
      const d = row(rawDef);
      return { id: string(d.id), title: string(d.title), explanation: string(d.explanation), location: location(root, d.location, files) };
    });
    const references = array(index.references).map((rawRef): TermReference => {
      const r = row(rawRef);
      return { id: string(r.id), label: string(r.label), location: location(root, r.location, files) };
    });
    const issues = array(index.issues).map((rawIssue): LintViolation => {
      const i = row(rawIssue);
      const loc = location(root, i.location, files);
      const rule = string(i.rule);
      if (!['DOC000', 'DOC101', 'DOC102', 'DOC103', 'DOC104'].includes(rule)) {
        throw new Error('用語の規則が不正です');
      }
      return { ...docFailure(loc.path, string(i.message)), rule, range: { start: loc.start, end: loc.end } };
    });
    return { event: 'index', snapshot: { files, index: { definitions, references }, issues, total: count(v.total) } };
  }
  if (snapshot === undefined) {
    throw new Error('索引より先に判定が届きました');
  }
  const progress = { completed: count(v.completed), cacheHits: count(v.cache_hits), unmeasured: count(v.unmeasured) };
  if (progress.completed > snapshot.total || progress.cacheHits + progress.unmeasured > progress.completed) {
    throw new Error('進捗件数が不正です');
  }
  if (v.event === 'result') {
    const file = inside(root, v.path);
    const source = snapshot.files.get(file);
    if (source === undefined) {
      throw new Error('索引にない判定です');
    }
    const report = row(v.report);
    const results = array(report.results);
    if (results.length !== 1) {
      throw new Error('結果は1対象ずつ必要です');
    }
    const unit = row(row(results[0]).unit);
    return {
      event: 'result',
      path: file,
      unit: `${count(unit.line)}:${count(unit.end_line)}:${string(unit.kind)}`,
      violations: parseDocReport(JSON.stringify(report), file, source),
      ...progress,
    };
  }
  if (v.event === 'done') {
    const code = count(v.code);
    if (![0, 1, 2].includes(code) || progress.completed !== snapshot.total) {
      throw new Error('未完了の終了通知です');
    }
    return { event: 'done', code, ...progress };
  }
  throw new Error('未知の workspace イベントです');
}
