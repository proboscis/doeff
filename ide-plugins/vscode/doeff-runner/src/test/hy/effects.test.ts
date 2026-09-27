import * as assert from 'assert';
import { incomingCalls, outgoingCalls, type CallTarget, type Resolve } from '../../hy/callGraph';
import type { HyFileIndex } from '../../hy/contract';
import { EffectGraph, EffectGraphSource, type DefRef } from '../../hy/effects';
import { NO_EXTERNAL_FILES, NO_EXTERNAL_MODULES, type ExternalFileView } from '../../hy/external';
import {
  definitionTargetsWithHandlers,
  hoverExtras,
  implementationTargets,
  lensSpecs,
  lensTitle
} from '../../hy/navigation';
import { childNodes, rootNodes, type NavNode } from '../../hy/navTree';
import type { PythonModuleSource } from '../../hy/python';
import { resolveDefinition, type DefinitionTarget } from '../../hy/resolve';
import type { HyIndexStore } from '../../hy/store';
import { loadDocument, workspaceStore } from './fixtures';

const EFFECTS = '/ws/pkg/effects.hy';
const LOGGING = '/ws/pkg/logging_handler.hy';

/** Python の source を持たない口。 */
const NO_PYTHON: PythonModuleSource = {
  findModuleFiles: async () => [],
  readText: async (filePath) => ({ tag: 'unreadable', reason: `${filePath} は無い` })
};

/** fixture の置き場・表・解決の口の組。 */
interface World {
  readonly store: HyIndexStore;
  readonly graph: EffectGraph;
  readonly resolve: Resolve;
}

/** workspace.json の置き場から表と解決の口を作る(外の module は無し)。 */
function world(external: ExternalFileView = NO_EXTERNAL_FILES): World {
  const store = workspaceStore();
  return {
    store,
    graph: new EffectGraphSource(store, external).current(),
    resolve: (query) => resolveDefinition(store, NO_PYTHON, NO_EXTERNAL_MODULES, query)
  };
}

/** file の中の名前の定義を引く(同名が複数なら kind で選ぶ)。 */
function def(graph: EffectGraph, filePath: string, name: string, kind?: string): DefRef {
  const found = graph
    .definitionsIn(filePath)
    .find((r) => r.definition.name === name && (kind === undefined || r.definition.kind === kind));
  assert.ok(found, `${filePath} に ${name} が無い`);
  return found;
}

/** 定義の参照を「file 名:名前(kind)」にする。 */
function label(ref: DefRef): string {
  return `${ref.path.split('/').pop()}:${ref.definition.name}(${ref.definition.kind})`;
}

/** 呼び出しの行き先を短い文字列にする。 */
function targetLabel(target: CallTarget): string {
  switch (target.tag) {
    case 'definition':
      return label(target.ref);
    case 'python':
      return `py:${target.name}`;
    case 'effect-name':
      return `effect-name:${target.name}`;
  }
}

/** 定義へ移動の行き先を短い文字列にする。 */
function definitionTargetLabel(target: DefinitionTarget): string {
  return target.tag === 'hy-definition'
    ? `${target.path.split('/').pop()}:${target.definition.name}(${target.definition.kind})`
    : target.tag;
}

suite('effect の判定', () => {
  test('基底に EffectBase(dotted の最後の区切り)・effect のクラス(推移的)・どこかの handler の節の名前は effect', () => {
    const { graph } = world();
    assert.ok(graph.isEffect('PutRow'), 'bases に EffectBase');
    assert.ok(graph.isEffect('GetRow'), 'bases に doeff.EffectBase');
    assert.ok(graph.isEffect('SpecialPut'), 'bases に effect の PutRow(推移的)');
    assert.ok(graph.isEffect('Tell'), 'クラスは無いが handler の節がある');
    assert.ok(!graph.isEffect('Plain'));
    assert.ok(!graph.isEffect('save_row'));
    assert.ok(!graph.isEffect('Emit'), '撃たれているだけで、クラスも節も無い');
  });

  test('推移的な判定は file の順に依らない', () => {
    const document = loadDocument('workspace.json');
    const reversed = new EffectGraph([...document.files].reverse().map((file) => ({ file, external: false })));
    assert.ok(reversed.isEffect('SpecialPut'));
  });

  test('外の module の effect は、外の cache に入った物だけを数える', () => {
    const memory = loadDocument('external-memory.json').files[0];
    const asEffect: HyFileIndex = {
      ...memory,
      definitions: memory.definitions.map((d) => (d.name === 'MemoryStore' ? { ...d, bases: ['EffectBase'] } : d))
    };
    const external: ExternalFileView = { cachedFile: () => asEffect, cachedFiles: () => [asEffect], version: 1 };
    assert.ok(!world().graph.isEffect('MemoryStore'));
    assert.ok(world(external).graph.isEffect('MemoryStore'));
  });

  test('handler の節・節の handler・撃つ場所・定義の中で撃つ effect', () => {
    const { graph } = world();
    assert.deepStrictEqual(graph.clausesFor('PutRow').map(label).sort(), [
      'effects.hy:PutRow(effect-clause)',
      'logging_handler.hy:PutRow(effect-clause)'
    ]);
    const handler = def(graph, EFFECTS, 'memory-handler');
    assert.deepStrictEqual(graph.handlerClauses(handler).map(label), [
      'effects.hy:PutRow(effect-clause)',
      'effects.hy:GetRow(effect-clause)'
    ]);
    assert.strictEqual(graph.handlerOfClause(def(graph, LOGGING, 'Tell'))?.definition.name, 'logging-handler');
    assert.deepStrictEqual(
      graph.performSites('PutRow').map((s) => `${s.caller?.definition.name}:${s.call.performed}`),
      ['save-row:true', 'load-row:true']
    );
    // helper-fn・Plain は effect でない。GetRow は `<-` 無しの生成でもクラスが effect なので数える
    assert.deepStrictEqual(
      graph.performedEffects(def(graph, EFFECTS, 'save-row')).map((p) => p.name),
      ['PutRow', 'GetRow']
    );
  });
});

suite('effect の行き来 — クリック・実装へ移動・注記・hover', () => {
  test('Cmd+クリックは effect の名前ならクラスに加えて全 handler の節、それ以外は解決のまま', async () => {
    const { graph, resolve } = world();
    const onEffect = await resolve({ filePath: EFFECTS, name: 'PutRow', qualifier: null });
    assert.deepStrictEqual(definitionTargetsWithHandlers(graph, onEffect, 'PutRow').map(definitionTargetLabel).sort(), [
      'effects.hy:PutRow(defclass)',
      'effects.hy:PutRow(effect-clause)',
      'logging_handler.hy:PutRow(effect-clause)'
    ]);
    const onFunction = await resolve({ filePath: EFFECTS, name: 'helper-fn', qualifier: null });
    assert.deepStrictEqual(definitionTargetsWithHandlers(graph, onFunction, 'helper_fn').map(definitionTargetLabel), [
      'effects.hy:helper-fn(defn)'
    ]);
  });

  test('実装へ移動 — effect なら扱う全 handler の節、handler ならその節、他は空', async () => {
    const { graph, resolve } = world();
    const at = async (filePath: string, name: string, mangled: string): Promise<string[]> =>
      implementationTargets(graph, await resolve({ filePath, name, qualifier: null }), mangled).map(label).sort();
    assert.deepStrictEqual(await at(LOGGING, 'PutRow', 'PutRow'), [
      'effects.hy:PutRow(effect-clause)',
      'logging_handler.hy:PutRow(effect-clause)'
    ]);
    assert.deepStrictEqual(await at(EFFECTS, 'memory-handler', 'memory_handler'), [
      'effects.hy:GetRow(effect-clause)',
      'effects.hy:PutRow(effect-clause)'
    ]);
    assert.deepStrictEqual(await at(EFFECTS, 'save-row', 'save_row'), []);
  });

  test('注記の中身と見出し', () => {
    const { graph } = world();
    const titles = lensSpecs(graph, EFFECTS).map((spec) => `${spec.ref.definition.name}: ${lensTitle(spec, 3)}`);
    assert.deepStrictEqual(titles, [
      'PutRow: handler 2 個',
      'PutRow: 撃つ場所 2 箇所',
      'GetRow: handler 1 個',
      'GetRow: 撃つ場所 2 箇所',
      'SpecialPut: handler 0 個',
      'SpecialPut: 撃つ場所 1 箇所',
      'save-row: 撃つ effect 2 個',
      'save-row: 呼び出し元 3 箇所',
      'memory-handler: 扱う effect: PutRow, GetRow',
      'load-row: 撃つ effect 2 個',
      'load-row: 呼び出し元 3 箇所'
    ]);
    const callers = lensSpecs(graph, EFFECTS).find((s) => s.tag === 'program-callers');
    assert.ok(callers);
    assert.strictEqual(lensTitle(callers, undefined), '呼び出し元を数えています…');
  });

  test('hover の追記 — effect は handler の数、プログラムは撃つ effect、handler は扱う effect', () => {
    const { graph } = world();
    assert.deepStrictEqual(hoverExtras(graph, def(graph, EFFECTS, 'PutRow', 'defclass')), [
      'effect — handler 2 個(memory-handler, logging-handler)'
    ]);
    assert.deepStrictEqual(hoverExtras(graph, def(graph, EFFECTS, 'load-row')), ['撃つ effect: GetRow, PutRow']);
    assert.deepStrictEqual(hoverExtras(graph, def(graph, LOGGING, 'logging-handler')), ['扱う effect: PutRow, Tell']);
    assert.deepStrictEqual(hoverExtras(graph, def(graph, EFFECTS, 'helper-fn')), []);
  });
});

suite('呼び出し階層', () => {
  test('出ていく呼び出し — 行き先ごとに束ね、effect の生成に印を付ける', async () => {
    const { graph, resolve } = world();
    const groups = await outgoingCalls(graph, resolve, { tag: 'definition', ref: def(graph, EFFECTS, 'save-row') });
    assert.deepStrictEqual(
      groups.map((g) => `${targetLabel(g.target)}${g.effect ? ' [effect]' : ''}`),
      ['effects.hy:PutRow(defclass) [effect]', 'effects.hy:helper-fn(defn)', 'effects.hy:GetRow(defclass) [effect]', 'effects.hy:Plain(defclass)']
    );
  });

  test('effect のクラスから出ていくと handler の節へ降りる・handler からは自分の節', async () => {
    const { graph, resolve } = world();
    const fromEffect = await outgoingCalls(graph, resolve, { tag: 'definition', ref: def(graph, EFFECTS, 'PutRow', 'defclass') });
    assert.deepStrictEqual(fromEffect.map((g) => targetLabel(g.target)).sort(), [
      'effects.hy:PutRow(effect-clause)',
      'logging_handler.hy:PutRow(effect-clause)'
    ]);
    const fromHandler = await outgoingCalls(graph, resolve, { tag: 'definition', ref: def(graph, LOGGING, 'logging-handler') });
    assert.deepStrictEqual(fromHandler.map((g) => targetLabel(g.target)), [
      'logging_handler.hy:PutRow(effect-clause)',
      'logging_handler.hy:Tell(effect-clause)'
    ]);
  });

  test('import 越し・名前だけの effect・解決できない呼び出しの扱い', async () => {
    const { graph, resolve } = world();
    const groups = await outgoingCalls(graph, resolve, { tag: 'definition', ref: def(graph, LOGGING, 'announce') });
    assert.deepStrictEqual(groups.map((g) => `${targetLabel(g.target)}${g.effect ? ' [effect]' : ''}`), [
      'logging_handler.hy:Tell(effect-clause) [effect]',
      'effects.hy:save-row(defk)',
      'effects.hy:SpecialPut(defclass) [effect]',
      'effect-name:Emit [effect]'
    ]);
    const ask = groups.find((g) => g.target.tag === 'effect-name');
    assert.ok(ask);
    assert.deepStrictEqual(await outgoingCalls(graph, resolve, ask.target), [], 'Emit を扱う handler は無い');
  });

  test('入ってくる呼び出し — 名前で絞ってから解決し、呼び出し元ごとに束ねる', async () => {
    const { graph, resolve } = world();
    const incoming = await incomingCalls(graph, resolve, { tag: 'definition', ref: def(graph, EFFECTS, 'save-row') });
    assert.deepStrictEqual(
      incoming.map((g) => (g.from.tag === 'definition' ? label(g.from.ref) : `top:${g.from.module}`)).sort(),
      ['logging_handler.hy:announce(defk)', 'top:pkg.effects']
    );
    const toClause = await incomingCalls(graph, resolve, { tag: 'definition', ref: def(graph, LOGGING, 'PutRow', 'effect-clause') });
    assert.deepStrictEqual(
      toClause.map((g) => (g.from.tag === 'definition' ? g.from.ref.definition.name : 'top')).sort(),
      ['load-row', 'save-row'],
      'handler の節へは、その effect を撃つ場所から入ってくる'
    );
  });
});

/** 節を短い文字列にする(木の形を比べる用)。 */
function nodeLabel(node: NavNode): string {
  switch (node.tag) {
    case 'group':
      return `[${node.label}]`;
    case 'effect':
      return `effect ${node.entry.name}`;
    case 'handler':
    case 'clause':
    case 'program':
      return `${node.tag} ${node.ref.definition.name}${node.tag === 'clause' ? `@${node.ref.definition.container}` : ''}`;
    case 'section':
      return `<${node.section}>`;
    case 'site':
      return `site ${node.site.caller?.definition.name ?? 'top'}:${node.site.call.callee}`;
    case 'target':
      return `target ${targetLabel(node.target)}`;
    case 'top-level':
      return `top ${node.module}`;
    case 'cycle':
      return `cycle ${node.label}`;
    case 'empty':
      return `(${node.label})`;
  }
}

suite('ナビゲーションパネルの木', () => {
  test('最上段は module ごとの束(クラスの見えない effect は別の束)と絞り込み', () => {
    const { graph } = world();
    const effects = rootNodes(graph, 'effects', '', undefined);
    assert.deepStrictEqual(
      effects.map((g) => `${nodeLabel(g)} ${g.tag === 'group' ? g.children.map(nodeLabel).join(', ') : ''}`),
      ['[(クラスの定義が索引に無い effect)] effect Ask, effect Tell', '[pkg.effects] effect GetRow, effect PutRow, effect SpecialPut']
    );
    assert.deepStrictEqual(rootNodes(graph, 'handlers', '', undefined).map(nodeLabel), ['[pkg.app]', '[pkg.effects]', '[pkg.logging_handler]']);
    const programs = rootNodes(graph, 'programs', 'save', undefined);
    assert.deepStrictEqual(
      programs.map((g) => (g.tag === 'group' ? g.children.map(nodeLabel) : [])),
      [['program save-row']]
    );
  });

  test('effect → Handlers(節)・Performed by(撃つ場所)、節から同じ effect へ戻ると循環で止める', async () => {
    const { graph, resolve } = world();
    const ctx = { graph, resolve };
    const [group] = rootNodes(graph, 'effects', 'PutRow', undefined);
    const [putRow] = await childNodes(ctx, group);
    const [handlers, performedBy] = await childNodes(ctx, putRow);
    assert.deepStrictEqual((await childNodes(ctx, handlers)).map(nodeLabel), [
      'clause PutRow@memory-handler',
      'clause PutRow@logging-handler'
    ]);
    assert.deepStrictEqual((await childNodes(ctx, performedBy)).map(nodeLabel), [
      'site save-row:PutRow',
      'site load-row:PutRow'
    ]);
    const [clause] = await childNodes(ctx, handlers);
    const clauseChildren = await childNodes(ctx, clause);
    assert.deepStrictEqual(clauseChildren.map(nodeLabel), ['cycle PutRow', '<other-handlers>']);
    assert.deepStrictEqual((await childNodes(ctx, clauseChildren[1])).map(nodeLabel), ['clause PutRow@logging-handler']);
  });

  test('プログラム → Performs・Calls・Called by、Performs の effect から Handlers へ降りる', async () => {
    const { graph, resolve } = world();
    const ctx = { graph, resolve };
    const program: NavNode = { tag: 'program', ref: def(graph, EFFECTS, 'save-row'), trail: [] };
    const [performs, calls, calledBy] = await childNodes(ctx, program);
    const performed = await childNodes(ctx, performs);
    assert.deepStrictEqual(performed.map(nodeLabel), ['effect PutRow', 'effect GetRow']);
    assert.deepStrictEqual((await childNodes(ctx, calls)).map(nodeLabel), ['program helper-fn', 'program Plain']);
    assert.deepStrictEqual((await childNodes(ctx, calledBy)).map(nodeLabel).sort(), ['program announce', 'top pkg.effects']);
    const [handlersOfPut] = await childNodes(ctx, performed[0]);
    assert.deepStrictEqual((await childNodes(ctx, handlersOfPut)).map(nodeLabel), [
      'clause PutRow@memory-handler',
      'clause PutRow@logging-handler'
    ]);
  });

  test('Current file は今の file の effect・handler・プログラムの 3 つの束', () => {
    const { graph } = world();
    const nodes = rootNodes(graph, 'current-file', '', EFFECTS);
    assert.deepStrictEqual(
      nodes.map((g) => `${nodeLabel(g)} ${g.tag === 'group' ? g.children.map(nodeLabel).join(', ') : ''}`),
      [
        '[Effects] effect PutRow, effect GetRow, effect SpecialPut',
        '[Handlers] handler memory-handler',
        '[Programs] program save-row, program load-row'
      ]
    );
    assert.deepStrictEqual(rootNodes(graph, 'current-file', '', undefined).map((n) => n.tag), ['empty']);
  });
});
