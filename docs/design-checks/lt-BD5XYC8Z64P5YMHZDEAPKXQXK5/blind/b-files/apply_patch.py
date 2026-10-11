"""overlay の handlers.py に、提案の差分(sequential の on_progress)を当てる。"""
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new)

sub('''def sequential():  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Sequential handler for Traverse/Reduce/Zip/Inspect.
''', '''class _Progress:
    """for/do 1 つの進捗。答えの件(From を重ねた for/do では組)で数える。"""

    def __init__(self, label, total):
        self.label = label
        self.total = total
        self.done = 0

    def step(self, on_progress):
        self.done += 1
        if on_progress is not None:
            on_progress(self.label, self.done, self.total)


def sequential(on_progress=None):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Sequential handler for Traverse/Reduce/Zip/Inspect.

    on_progress: ``on_progress(label, done, total)`` を答えの件が 1 つ終わるたびに呼ぶ(ex08 の tqdm の
    handler の代わり — Pass で f を包む handler は件の失敗・skip を数えられず、From を重ねると外の件ごとに
    bar が立っていた)。for/do 1 つにつき 1 本で、From を重ねた for/do は答えの件(組)の数で数える:
    内側の From の Traverse は外の件の program の中からこの handler に戻ってくる(件ごとに install_self を
    置き直す)ので、外の Traverse が走っている間に来た Traverse は外の進捗に足す — 外の件 1 つが内側の件 n 個に
    割れる(total に n - 1 を足す)。答えが Collection の件は flatten で内側の件に割れるので、その件自身は数えない。
''')

sub('''    from doeff.handler_utils import get_inner_handlers, inner_of
    @do
    def handler(effect: CollectionEffect, k):''', '''    from doeff.handler_utils import get_inner_handlers, inner_of

    # 走っている for/do の進捗(内側ほど後ろ)。空でない間に来た Traverse は、外の From の件の中の内側の From。
    running = []

    @do
    def handler(effect: CollectionEffect, k):''')

sub('''            # Iterate: Collection (from previous traverse) or raw iterable/generator
            if isinstance(effect.items, Collection):
                items_iter = effect.items.all_items
            else:
                items_iter = (
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                )

            for item in items_iter:
                # Carry forward already-failed items without re-processing
                if item.failed:
                    results.append(item)
                    continue
''', '''            # Iterate: Collection (from previous traverse) or raw iterable/generator
            # (進捗の total のため、件を先に並べる)
            if isinstance(effect.items, Collection):
                items_iter = effect.items.all_items
            else:
                items_iter = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]

            if running:
                # 内側の From: 外の件 1 つが、この Traverse の件の数に割れる(Collection.flatten)
                progress = running[-1]
                progress.total += len(items_iter) - 1
            else:
                progress = _Progress(effect.label, len(items_iter))
            running.append(progress)

            for item in items_iter:
                # Carry forward already-failed items without re-processing
                if item.failed:
                    results.append(item)
                    progress.step(on_progress)
                    continue
''')

sub('''                try:
                    value = yield prog
                except Exception as error:
                    results.append(ItemResult(
                        index=item.index,
                        value=error,
                        failed=True,
                        history=item.history + [HistoryEntry(
                            stage=effect.label,
                            event="failed",
                            detail=str(error),
                        )],
                    ))
                    continue
                if value is _SKIPPED:
                    results.append(ItemResult(
                        index=item.index,
                        value=item.value,
                        failed=True,
                        history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                    ))
                else:
                    results.append(ItemResult(
                        index=item.index,
                        value=value,
                        history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                    ))

            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Reduce):
            inner_hs = yield get_inner_handlers(k)
            # Fold over valid items (skip failed)''', '''                try:
                    value = yield prog
                except Exception as error:
                    results.append(ItemResult(
                        index=item.index,
                        value=error,
                        failed=True,
                        history=item.history + [HistoryEntry(
                            stage=effect.label,
                            event="failed",
                            detail=str(error),
                        )],
                    ))
                    progress.step(on_progress)
                    continue
                if value is _SKIPPED:
                    results.append(ItemResult(
                        index=item.index,
                        value=item.value,
                        failed=True,
                        history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                    ))
                    progress.step(on_progress)
                else:
                    results.append(ItemResult(
                        index=item.index,
                        value=value,
                        history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                    ))
                    # 答えが Collection の件(外の From の件)は flatten で内側の件に割れる — 内側の件で数え済み
                    if not isinstance(value, Collection):
                        progress.step(on_progress)

            running.pop()
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Reduce):
            inner_hs = yield get_inner_handlers(k)
            # Fold over valid items (skip failed)''')

path.write_text(text, encoding="utf-8")
print("patched", path)
