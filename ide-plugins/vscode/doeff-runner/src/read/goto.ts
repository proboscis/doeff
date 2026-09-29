// 読む面で実体の名を押した時の VS Code の側(v12・agora-redesign #910 U19)— 候補が複数なら quick pick で選ばせ、行き先
// (resolve.ts の destinationOf)へ動く。file の面と repo 全体の面が同じ関数を使う(面ごとに別の動きを書かないため)。

import * as vscode from 'vscode';
import { LABELS } from './labels';
import { destinationOf, type EntityDestination } from './resolve';
import type { CallGraph } from './tree';

/** 候補から 1 つを選ぶ(1 つなら選ばずにそれ・複数なら quick pick — 名・種類・置き場を並べる)。 */
async function pickCandidate(graph: CallGraph, candidates: readonly string[]): Promise<string | undefined> {
  if (candidates.length <= 1) {
    return candidates[0];
  }
  const items = candidates.flatMap((qn) => {
    const found = graph.definitions.get(qn);
    return found === undefined
      ? []
      : [{ label: found.definition.name, description: found.definition.kind, detail: vscode.workspace.asRelativePath(found.path), qualifiedName: qn }];
  });
  const picked = await vscode.window.showQuickPick(items, { title: LABELS.pickDefinition, matchOnDescription: true, matchOnDetail: true });
  return picked?.qualifiedName;
}

/**
 * 押した名の候補から行き先を決めて動く。カードへ行く時は面ごとの見せ方(file の面はそのカード・別の file はその file の面・
 * repo 全体の面は積む)を showCard に任せ、editor へ行く時はここで text editor の定義の名の位置を開く。
 */
export async function followEntity(
  graph: CallGraph,
  candidates: readonly string[],
  editor: boolean,
  showCard: (destination: Extract<EntityDestination, { tag: 'card' }>) => void | Promise<void>
): Promise<void> {
  const chosen = await pickCandidate(graph, candidates);
  const destination = chosen === undefined ? undefined : destinationOf(graph, chosen, editor);
  if (destination === undefined) {
    return;
  }
  switch (destination.tag) {
    case 'card':
      await showCard(destination);
      return;
    case 'editor': {
      const { start, end } = destination.range;
      const selection = new vscode.Range(start.line, start.character, end.line, end.character);
      await vscode.window.showTextDocument(vscode.Uri.file(destination.path), { selection, preview: false });
      return;
    }
    default: {
      const unreachable: never = destination;
      throw new Error(`網羅されていない行き先: ${JSON.stringify(unreachable)}`);
    }
  }
}
