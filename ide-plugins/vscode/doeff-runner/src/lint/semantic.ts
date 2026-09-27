// 保存した時と、編集中に打つのが止まった時の Jev の意味の判定(doeff-linter の --semantic)を回す仕組みの純粋な部分 — 同時に 1 本・
// 同じ file の依頼が続いたら最後の 1 回だけにする列、打つのが止まるのを待つ時計、古い中身の答えを捨てる見分け、ステータスバーの文、
// キーが無いことの見分け。Jev を呼ぶのは linter で、拡張は linter を呼ぶだけ(書きかけで読めない定義を問わないのも linter)。

import type { LintReport, LintSemantic } from './contract';
import type { Linter, SemanticRequest } from './runner';

/** 予約 1 つの取り消しの口。 */
export interface Scheduled {
  cancel(): void;
}

/** 時計の口(本物は setTimeout・検では偽物)— ms 後に callback を呼ぶ予約をする。 */
export interface Clock {
  after(ms: number, callback: () => void): Scheduled;
}

/** 本物の時計。 */
export const SYSTEM_CLOCK: Clock = {
  after: (ms, callback) => {
    const timer = setTimeout(callback, ms);
    return { cancel: () => clearTimeout(timer) };
  }
};

/**
 * 打つのが止まったら 1 回だけ撃つ係 — 鍵(file)ごとに、最後の touch から delayMs の間に次の touch が来なければ fire(鍵) を呼ぶ。
 * 途中の touch は待ちを最初からやり直す。
 */
export class PauseTrigger {
  private readonly timers = new Map<string, Scheduled>();

  constructor(
    private readonly clock: Clock,
    private readonly fire: (key: string) => void
  ) {}

  /** 編集があった — 待ちを最初からやり直す。 */
  touch(key: string, delayMs: number): void {
    this.cancel(key);
    this.timers.set(
      key,
      this.clock.after(delayMs, () => {
        this.timers.delete(key);
        this.fire(key);
      })
    );
  }

  /** その鍵の待ちをやめる(保存した時 — 保存の判定が代わりに問う)。 */
  cancel(key: string): void {
    const scheduled = this.timers.get(key);
    if (scheduled !== undefined) {
      scheduled.cancel();
      this.timers.delete(key);
    }
  }

  /** 待っている鍵の数。 */
  get pending(): number {
    return this.timers.size;
  }

  /** 全部の待ちをやめる。 */
  dispose(): void {
    for (const scheduled of this.timers.values()) {
      scheduled.cancel();
    }
    this.timers.clear();
  }
}

/**
 * 答えが今の中身の物か — 問うた時の document の版と今の版が同じ時だけ使う。問うた後に打った(版が進んだ)なら、その答えは古い中身の物
 * として捨てる(最新の中身の答えだけを出す)。編集中の依頼は閉じた document(今の版が無い)の答えも捨てる。保存の依頼は disk の中身を
 * 問うたので、閉じても disk の中身のまま — 使う。
 */
export function isCurrentAnswer(request: SemanticRequest, currentVersion: number | undefined): boolean {
  switch (request.tag) {
    case 'semantic':
      return currentVersion === undefined || currentVersion === request.version;
    case 'semantic-change':
      return currentVersion === request.version;
    default: {
      const unreachable: never = request;
      throw new Error(`網羅されていない依頼: ${JSON.stringify(unreachable)}`);
    }
  }
}

/** 設定の「打つのが止まってから問うまでの秒」を ms にする(無い・有限でない時は既定・0.5 秒未満は 0.5 秒・60 秒を超えれば 60 秒)。 */
export function pauseDelayMs(seconds: number | undefined): number {
  const value = seconds !== undefined && Number.isFinite(seconds) ? seconds : DEFAULT_PAUSE_SECONDS;
  return Math.round(Math.min(60, Math.max(0.5, value)) * 1000);
}

/** 打つのが止まってから問うまでの既定の秒。 */
export const DEFAULT_PAUSE_SECONDS = 2;

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

  /** 待っている依頼を取り下げる(走っている物は止めない)。 */
  drop(key: string): void {
    this.pending.delete(key);
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
        tooltip: `${state.path} の定義を Jev に問っている(保存した時は file の定義・編集中は中身の変わった定義だけ)${state.waiting > 0 ? `(待ち ${state.waiting} 件)` : ''}`
      };
    case 'done': {
      const s = state.summary;
      // 何も問わなかった実行(編集中に中身の変わった定義が無かった)は較正を撃たないので not-run — 警告にしない
      const calibrated = s.calibration === 'ok' || (s.calibration === 'not-run' && s.asked === 0);
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
  'Jev のキーが見つからない — Jev の呼び出しを覚える代理を向けた repo(pyproject の [tool.doeff-linter.semantic] proxy_url)は代理の token の file(既定 ~/.config/jev/proxy-token)が、向けていない repo は VS Code を起動した環境の TYPESAFE_API_KEY(shell から `code` で起動するか、JEV_API_KEY_FILE を指す)が要る。この session では保存した時と編集中の Jev の判定を止めます。';

/** 開いている document の今の中身と版(閉じた・workspace の外なら undefined)。 */
export interface OpenDocument {
  readonly root: string;
  readonly text: string;
  readonly version: number;
}

/** Jev の判定の入り切り(読むたびに今の設定の値)。 */
export interface SemanticTriggers {
  /** 保存した時に問う(doeff-runner.hy.semanticOnSave) */
  readonly onSave: boolean;
  /** 編集中に打つのが止まったら問う(doeff-runner.hy.semanticOnChange) */
  readonly onChange: boolean;
  /** 打つのが止まってから問うまで(ms・doeff-runner.hy.semanticOnChangeDelaySeconds) */
  readonly pauseMs: number;
}

/** Jev の判定の係が使う口(service が VS Code と linter で作る・検では偽物)。 */
export interface SemanticPorts {
  /** Jev の判定の子 process の口(決定的な実行とは別 — 待たせない) */
  readonly linter: Linter;
  readonly clock: Clock;
  readonly triggers: () => SemanticTriggers;
  readonly document: (path: string) => OpenDocument | undefined;
  /** 今の中身の答えを置き場へ入れる */
  readonly deliver: (request: SemanticRequest, report: LintReport) => void;
  readonly log: (line: string) => void;
  readonly onState: (state: SemanticState) => void;
  readonly notify: (message: string) => void;
}

/**
 * Jev の判定の係 — 保存した時は保存した中身を、編集中は打つのが止まった時の中身(中身の変わった定義だけ)を linter 経由で Jev に問い、
 * 答えが返った時に中身が変わっていれば捨てる。同時に 1 本・同じ file の依頼が待っている間に続けば最後の 1 つだけ。
 * Jev のキーが無いと分かった session では、それ以降は問わない。
 */
export class SemanticJudge {
  private readonly queue: LatestPerKeyQueue<SemanticRequest>;
  private readonly pause: PauseTrigger;
  private stopped = false;

  constructor(private readonly ports: SemanticPorts) {
    this.queue = new LatestPerKeyQueue((_key, request) => this.run(request));
    this.pause = new PauseTrigger(ports.clock, (path) => this.paused(path));
  }

  /**
   * 編集があった — まだ走り始めていないその file の依頼(古い中身)を取り下げ、打つのが止まるのを待ち直す(設定が切・キーが無いと
   * 分かった session では待たない)。
   */
  edited(path: string): void {
    this.queue.drop(path);
    const triggers = this.ports.triggers();
    if (this.stopped || !triggers.onChange) {
      this.pause.cancel(path);
      return;
    }
    this.pause.touch(path, triggers.pauseMs);
  }

  /** 保存した(決定的な実行の後)— 編集中の待ちをやめ(保存の判定が代わりに問う)、保存した中身の判定を積む。 */
  saved(root: string, path: string, version: number): void {
    this.pause.cancel(path);
    if (this.stopped || !this.ports.triggers().onSave) {
      return;
    }
    this.enqueue({ tag: 'semantic', root, path, version });
  }

  /** 待ちを全部やめる。 */
  dispose(): void {
    this.pause.dispose();
  }

  /** 打つのが止まった — その時の中身で、中身の変わった定義を問う依頼を積む。 */
  private paused(path: string): void {
    const document = this.ports.document(path);
    if (document === undefined || this.stopped || !this.ports.triggers().onChange) {
      return;
    }
    this.enqueue({ tag: 'semantic-change', root: document.root, path, text: document.text, version: document.version });
  }

  private enqueue(request: SemanticRequest): void {
    this.queue.enqueue(request.path, request);
    this.ports.onState({ tag: 'running', path: this.queue.active ?? request.path, waiting: this.queue.waiting });
  }

  /** 1 本走らせ、今の中身の答えだけを置き場へ入れ、状態を知らせる。 */
  private async run(request: SemanticRequest): Promise<void> {
    if (this.stopped) {
      return;
    }
    this.ports.onState({ tag: 'running', path: request.path, waiting: this.queue.waiting });
    const outcome = await this.ports.linter.lint(request);
    switch (outcome.tag) {
      case 'disabled':
        this.ports.onState({ tag: 'idle' });
        return;
      case 'failed':
        this.ports.log(`[lint] Jev の判定に失敗: ${outcome.reason}`);
        this.ports.onState({ tag: 'failed', reason: outcome.reason });
        return;
      case 'ok':
        break;
      default: {
        const unreachable: never = outcome;
        throw new Error(`網羅されていない結果: ${JSON.stringify(unreachable)}`);
      }
    }
    const current = isCurrentAnswer(request, this.ports.document(request.path)?.version);
    if (current) {
      this.ports.deliver(request, outcome.report);
    }
    if (isMissingJevKey(outcome.report.errors)) {
      this.stopped = true;
      this.pause.dispose();
      this.ports.notify(MISSING_KEY_MESSAGE);
      this.ports.onState({ tag: 'no-key' });
      return;
    }
    if (!current) {
      this.ports.log(`[lint] Jev の答えを捨てた — 問うた後に ${request.path} の中身が変わった(最新の中身の答えだけを出す)`);
      this.ports.onState(this.queue.waiting > 0 ? { tag: 'running', path: request.path, waiting: this.queue.waiting } : { tag: 'idle' });
      return;
    }
    const summary = outcome.report.semantic;
    this.ports.onState(
      summary === null ? { tag: 'failed', reason: 'linter が意味の規則の要約(semantic)を出さない — 設定か版を確かめる' } : { tag: 'done', summary }
    );
  }
}
