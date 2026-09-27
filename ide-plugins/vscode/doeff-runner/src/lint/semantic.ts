// 保存した時の Jev の意味の判定(doeff-linter の --semantic)を回す仕組みの純粋な部分 — 同時に 1 本・同じ file の保存が続いたら
// 最後の 1 回だけにする列、ステータスバーの文、キーが無いことの見分け。Jev を呼ぶのは linter で、拡張は linter を呼ぶだけ。

import type { LintSemantic } from './contract';

/**
 * 同時に 1 本だけ走らせ、待っている間に同じ鍵(file)の依頼が来たら最後の 1 つに置き換える列。
 * 走らせる中身は口(run)で受け取る(テストは偽物で)。
 */
export class LatestPerKeyQueue<T> {
  private readonly pending = new Map<string, T>();
  private running: string | undefined;

  constructor(private readonly run: (key: string, request: T) => Promise<void>) {}

  /** 依頼を積む — 同じ鍵が待っていれば置き換える(走っている物は止めない)。 */
  enqueue(key: string, request: T): void {
    this.pending.delete(key); // 最後に来た物を列の後ろへ
    this.pending.set(key, request);
    void this.drain();
  }

  /** 今走っている鍵(無ければ undefined)。 */
  get active(): string | undefined {
    return this.running;
  }

  /** 待っている鍵の数。 */
  get waiting(): number {
    return this.pending.size;
  }

  /** 待っている物を 1 本ずつ走らせる。 */
  private async drain(): Promise<void> {
    if (this.running !== undefined) {
      return;
    }
    const next = this.pending.entries().next();
    if (next.done === true) {
      return;
    }
    const [key, request] = next.value;
    this.pending.delete(key);
    this.running = key;
    try {
      await this.run(key, request);
    } finally {
      this.running = undefined;
      void this.drain();
    }
  }
}

/** 保存した時の Jev の判定の状態。 */
export type SemanticState =
  | { readonly tag: 'idle' }
  | { readonly tag: 'running'; readonly path: string; readonly waiting: number }
  | { readonly tag: 'done'; readonly summary: LintSemantic }
  | { readonly tag: 'no-key' }
  | { readonly tag: 'failed'; readonly reason: string };

/** ステータスバーの表示 1 つ。 */
export interface SemanticStatus {
  readonly text: string;
  /** 警告の色にするか */
  readonly warning: boolean;
  readonly tooltip: string;
}

/** 状態をステータスバーの文にする(出さない時は undefined)。 */
export function semanticStatus(state: SemanticState): SemanticStatus | undefined {
  switch (state.tag) {
    case 'idle':
      return undefined;
    case 'running':
      return {
        text: '$(sync~spin) Jev: 判定中…',
        warning: false,
        tooltip: `保存した file の未判定の定義を Jev に問っている${state.waiting > 0 ? `(待ち ${state.waiting} 件)` : ''}`
      };
    case 'done': {
      const s = state.summary;
      const calibrated = s.calibration === 'ok';
      const text = s.unjudged === 0 ? 'Jev: 済(未判定 0)' : `Jev: 未判定 ${s.unjudged}`;
      const lines = [`model ${s.model} · 判定済み ${s.judged} · 未判定 ${s.unjudged} · 今回問うた ${s.asked}`];
      if (!calibrated) {
        lines.push(`較正の見張り: ${s.calibration}(ok でない — Jev の答えをそのまま信じない)`);
      }
      return { text: `$(sparkle) ${text}${calibrated ? '' : ` · 較正 ${s.calibration}`}`, warning: !calibrated, tooltip: lines.join('\n') };
    }
    case 'no-key':
      return { text: '$(sparkle) Jev: キー無し', warning: true, tooltip: 'Jev のキーが見つからないので、この session では Jev に問わない' };
    case 'failed':
      return { text: '$(sparkle) Jev: 失敗', warning: true, tooltip: state.reason };
    default: {
      const unreachable: never = state;
      throw new Error(`網羅されていない状態: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** linter の errors が「Jev のキーが無い」を言っているか(doeff-linter の文「Jev の API キーが無い(…)」)。 */
export function isMissingJevKey(errors: readonly string[]): boolean {
  return errors.some((e) => e.includes('Jev の API キーが無い'));
}

/** キーが無い時に一度だけ出す通知の文(キーの値は扱わない)。 */
export const MISSING_KEY_MESSAGE =
  'Jev のキーが見つからない — VS Code を起動した環境に TYPESAFE_API_KEY が無い。shell から `code` で起動するか、JEV_API_KEY_FILE を指す。この session では保存した時の Jev の判定を止めます。';
