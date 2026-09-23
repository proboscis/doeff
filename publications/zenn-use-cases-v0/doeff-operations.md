---
title: "Git・通知・秘密情報も、テストできる操作にする"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

GitでPRを作り、秘密情報を使って通知内容へ署名し、レビューを依頼する。doeffなら、この順序を`@do`で書き、外側のハンドラで操作の実行方法を選べます。

最初に、この記事で組み立てる処理の中心を見てください。`propose_change`と`signing_key`も、後で示す`@do`関数です。

```python
from doeff import do  # 処理の順序をProgramへ組み立てる。
from hashlib import sha256  # HMAC署名にSHA-256を使う。
from hmac import new as make_hmac  # PR URLと鍵から署名を作る。
from doeff_notify.effects import Notify  # 署名付きの通知を依頼する。

@do  # 小さなProgramを順番に組み合わせる。
def example_excerpt(work_dir):  # 詳細な分岐は後の完全な例で示す。
    pr = yield propose_change(work_dir)  # PR作成の結果を受け取る。
    if pr is None:  # 差分がなければ、秘密情報の取得も通知も行わない。
        return None  # 変更なしを後続へ返す。
    key = yield signing_key()  # 署名に使う秘密情報をbytesで受け取る。
    signature = make_hmac(key, pr.url.encode("utf-8"), sha256).hexdigest()  # URLへ署名する。
    yield Notify(message="PRを確認してください", link=pr.url, metadata={"signature": signature})  # URLと署名を通知する。
```

![操作の依頼を、3つのハンドラへ渡す](/images/zenn-use-cases-v0/generated/operations-concept.png)

Git・秘密情報・通知の依頼を分け、テストではそれぞれメモリのハンドラで解釈します。

## Gitの操作を依頼する

`doeff-git`には`GitCommit`、`GitPush`、`GitPull`、`GitDiff`、`CreatePR`、`MergePR`があります。ハンドラはgit/ghを呼ぶ本番用と、依頼をメモリへ記録するテスト用に分かれます。

この例の入力は、**作業用branchで、commitしたい変更だけをstage済みの作業場所**です。`GitDiff(staged=True)`でその差分を確認し、`GitCommit(all=False)`でstage済みの内容だけをcommitします。branchの作成・checkout・stageは、この処理の前に行う前提です。

```python
from pathlib import Path  # Git操作の対象を表す。
from doeff import do, run  # 手順をProgramへ組み立て、テストで評価する。
from doeff_git.effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR  # 6種類の依頼を使う。
from doeff_git.handlers.testing import MockGitRuntime, mock_handlers  # Gitを実行せず記録する。
from doeff_git.types import PRHandle  # 作成したPRの情報を型として受け取る。

@do  # 差分確認からPR作成までの順番をProgramにする。
def propose_change(work_dir: Path):  # 作業用branchで必要な変更をstage済みの場所を受け取る。
    diff = yield GitDiff(work_dir=work_dir, staged=True)  # commit予定の差分を文字列で受け取る。
    if not diff:  # stageされた変更がなければ、書き込み操作へ進まない。
        return None  # 変更なしを正常な結果として返す。
    yield GitCommit(work_dir=work_dir, message="説明を更新する", all=False)  # stage済みの内容をcommitする。
    yield GitPush(work_dir=work_dir)  # 現在のbranchをpushする。別名のbranchへは切り替えない。
    return (yield CreatePR(work_dir=work_dir, title="説明の更新", draft=True))  # 同じbranchのPRを返す。

@do  # 更新操作を、まだ実行しないProgramにする。
def refresh_checkout(work_dir: Path):  # 変更を加える前の作業用branchを受け取る。
    yield GitPull(work_dir=work_dir)  # 現在のbranchを更新する。テストではpullの記録だけ残る。

@do  # PR作成とmergeを、呼び出す時点が異なるProgramに分ける。
def merge_approved(pr: PRHandle):  # 呼び出し側がレビュー・承認を確認したPRを受け取る。
    yield MergePR(pr=pr)  # 指定したPRをmergeする。承認の自動判定は行わない。

work_dir = Path("/example/document-project")  # テストでは実ディレクトリではなく記録のキーになる。
runtime = MockGitRuntime(diff_by_work_dir={str(work_dir): "+説明"})  # stage済みの差分を返す。
run(mock_handlers(runtime=runtime)(refresh_checkout(work_dir)))  # 作業開始前のpullを記録する。
pr = run(mock_handlers(runtime=runtime)(propose_change(work_dir)))  # commit・push・PR作成を記録する。
assert pr is not None  # 差分があるのでPRHandleを受け取る。
assert runtime.pushes[0][2] == pr.branch == "mock-branch"  # pushとPRの対象branchが一致する。
assert not runtime.merged_pr_numbers  # PRを作っただけではmergeされない。
run(mock_handlers(runtime=runtime)(merge_approved(pr)))  # 承認後の段階を別に評価する。
assert runtime.merged_pr_numbers == [pr.number]  # 指定したPRだけがmerge対象になる。
```

`GitPush(branch=...)`はbranchをcheckoutする操作ではありません。この例では指定を省略し、commit・push・PR作成を現在のbranchへそろえています。`GitPull`は変更を加える前の別の段階に分けました。

`merge_approved`という名前が承認を検証してくれるわけではありません。承認を確認するのは呼び出し側です。ここでは、PRを提案する処理が勝手にmergeへ進まないことを確かめています。

## 通知の意図を、配送方法から分ける

`doeff-notify`の`Notify`、`NotifyThread`、`Acknowledge`は、通知、同じスレッドへの追加、確認状態の取得を表します。組み込み先はconsole・log・testingです。Slackなどへ接続する場合は、その配送先を扱うハンドラが必要です。

```python
from doeff import handler  # 通知の生のハンドラをProgramへ取り付ける。
from doeff_notify.effects import Acknowledge, Notify, NotifyThread  # 通知の3種類の依頼を使う。
from doeff_notify.handlers.testing import testing_handler  # 配送せず依頼を収集する。

@do  # 開始・完了・確認の順番をProgramにする。
def notify_progress():  # 外部の配送先を関数内に固定せず通知する。
    sent = yield Notify(message="変換を開始しました", title="文書処理")  # 通知とスレッドのIDを受け取る。
    yield NotifyThread(thread_id=sent.thread_id, message="変換が完了しました")  # 同じスレッドへ追加する。
    return (yield Acknowledge(notification_id=sent.notification_id, timeout=0))  # 確認済みかを返す。

notify, capture = testing_handler(auto_acknowledge=True)  # 発行済みIDを確認済みとして返す。
assert run(handler(notify)(notify_progress())) is True  # テスト用ハンドラからTrueを受け取る。
assert len(capture.notifications) == len(capture.thread_updates) == 1  # 通知と追加が1件ずつになる。
assert capture.thread_updates[0].thread_id == capture.acknowledgements[0].notification_id  # 同じIDを使う。
```

`Acknowledge`の意味もハンドラが決めます。testingの`auto_acknowledge=True`は既知のIDへ`True`を返し、console/logは`False`を返します。どの配送先でも人間の確認を待ってくれるAPIという意味ではありません。

## 秘密情報の操作と、戻り値の型を確かめる

`doeff-secret`は取得・保存・一覧・削除の依頼を定義します。メモリのテスト用ストアは4種類すべてを扱い、環境変数用ハンドラは`GetSecret`の読み取りを扱います。`doeff-google-secret-manager`にはGoogle Cloud Secret Managerへ接続するハンドラがあります。

```python
from doeff_secret.effects import DeleteSecret, GetSecret, ListSecrets, SetSecret  # 秘密情報のCRUDを使う。
from doeff_secret.testing import in_memory_handlers  # 説明用の値をメモリ内だけに保存する。

@do  # 秘密情報の作成・取得・一覧・削除を同じProgramにする。
def secret_lifecycle():  # 実行側が選んだストアに対して操作する。
    yield SetSecret(secret_id="demo", value="example-only")  # 説明用の値を保存する。
    value = yield GetSecret(secret_id="demo")  # メモリハンドラではbytesが返る。
    names = yield ListSecrets()  # 保存直後なのでdemoを含む一覧が返る。
    yield DeleteSecret(secret_id="demo")  # この例で作った項目を削除する。
    remaining = yield ListSecrets()  # 空のストアで開始した場合は空リストになる。
    return value, names, remaining  # 取得値と削除前後の一覧を検証側へ返す。

assert run(in_memory_handlers()(secret_lifecycle())) == (  # 保存・取得・削除の結果を確認する。
    b"example-only", ["demo"], []  # 値はbytes、削除前は1件、削除後は0件になる。
)
```

現行実装では、メモリ版とSecret Manager版の`GetSecret`は`bytes`、環境変数版は`str`を返します。署名計算で同じように使うため、`@do`の小さな関数で`bytes`へそろえます。

```python
from doeff_secret.handlers import env_var_handler  # 環境変数形式の名前から値を取得する。

@do  # 取得先を固定せず、署名に使うバイト列を準備する。
def signing_key():  # 環境変数とメモリで異なる戻り値の型をここでそろえる。
    value = yield GetSecret(secret_id="notice-signing-key")  # ハンドラからstrまたはbytesを受け取る。
    return value.encode("utf-8") if isinstance(value, str) else value  # HMACへ渡せるbytesを返す。

p_env = env_var_handler(  # 本物の環境変数へ接続せず、説明用の辞書を使う。
    environ={"ARTICLE_NOTICE_SIGNING_KEY": "from-env"}, prefix="ARTICLE_"  # 正規化後の名前に値を置く。
)(signing_key())  # 同じGetSecretの依頼を環境変数用ハンドラへ渡す。
assert run(p_env) == b"from-env"  # 取得先がstrを返しても、署名用関数の結果はbytesになる。
```

## 小さなProgramをつなぎ、ハンドラを組み合わせる

PRの提案、署名鍵の取得、通知をつなぎます。差分なしなら通知しません。通知のメタデータへ入れるのはPR URLのHMAC署名で、秘密の値そのものではありません。

```python
from hashlib import sha256  # HMAC署名のハッシュ関数を選ぶ。
from hmac import new as make_hmac  # PR URLと鍵から署名を計算する。

@do  # Git・秘密情報・通知のProgramをyieldでつなぐ。
def propose_and_notify(work_dir: Path):  # stage済みの変更についてPRを提案する。
    pr = yield propose_change(work_dir)  # 差分があればPRHandle、なければNoneを受け取る。
    if pr is None:  # 提案がない場合は署名や通知を依頼しない。
        return None  # 変更なしを呼び出し側へ返す。
    key = yield signing_key()  # 秘密の値そのものを通知に含めず、署名計算だけに使う。
    signature = make_hmac(key, pr.url.encode("utf-8"), sha256).hexdigest()  # PR URLの署名を作る。
    yield Notify(message="PRを確認してください", link=pr.url, metadata={"signature": signature})  # URLと署名を送る。
    return pr  # 後続のレビュー処理へPRを渡す。ここではmergeしない。
```

この開発版には、既存ハンドラの委譲処理に互換性の問題があります。通知の組み込みハンドラは、未対応の依頼で引数なしの`Pass()`を呼びますが、現行VMでは`Pass(effect, k)`が必要です。そこで、通知だけを既存ハンドラへ渡す選別処理を、この記事の例に明示します。

```python
from doeff import Pass  # 未対応の依頼と継続を外側のハンドラへ渡す。

def notifications_only(raw_handler):  # 通知以外を現在のPass APIで委譲するハンドラを組み立てる。
    @do  # effectの選別と継続の受け渡しをProgramにする。
    def dispatch(effect, k):  # 依頼と、その結果を待つ継続を受け取る。
        if isinstance(effect, (Notify, NotifyThread, Acknowledge)):  # 通知の3種類だけを渡す。
            return (yield raw_handler(effect, k))  # 既存ハンドラが通知結果を返して継続を再開する。
        return (yield Pass(effect, k))  # 通知以外は引数を明示して外側のハンドラへ渡す。
    return handler(dispatch)  # Programを実行せず、取り付け用の関数を返す。

combined_git = MockGitRuntime(diff_by_work_dir={str(work_dir): "+説明"})  # Gitの応答と記録を用意する。
combined_notify, notices = testing_handler()  # 通知の記録先を用意する。
p_combined = mock_handlers(runtime=combined_git)(propose_and_notify(work_dir))  # Gitを解釈する。
p_combined = notifications_only(combined_notify)(p_combined)  # 通知を解釈し、それ以外を委譲する。
p_combined = in_memory_handlers(seed_data={"notice-signing-key": b"demo-key"})(p_combined)  # 鍵を解決する。
proposal = run(p_combined)  # ひとつのProgramを3種のハンドラで評価する。
assert proposal is not None  # 差分があればPRが返る。
assert notices.notifications[0].link == proposal.url  # 通知が作成したPRを指す。
assert not combined_git.merged_pr_numbers  # この処理にmergeは含まれない。
```

`notifications_only`はハンドラの取り付け関数を作るための関数で、Programを評価しません。中の`dispatch`は`@do`です。実行順序を持つ`propose_and_notify`は、各段階のProgramを`yield`でつなぎます。

秘密情報の環境変数用ハンドラにも、値がないときに削除済みの`Delegate()`を呼ぶ問題があります。メモリ版も未対応の依頼を委譲する箇所が同様です。したがって、この版について「既存ハンドラを任意の順序に積めば動く」「環境変数になければ自動でクラウドへ移る」とは説明できません。上の検証では、メモリ版を最も外側に置き、そこへ届くのを秘密情報の依頼だけにしています。

## 通知を別のeffectへ翻訳する

log版の通知ハンドラは、通信の代わりに`Tell`を発行します。外側にWriterとStateのハンドラを置くと、その記録を取り出せます。同じ`notify_progress()`をそのまま使います。

```python
from doeff_notify.handlers.log import log_handler  # 通知をTellへ翻訳するハンドラを使う。
from doeff_core_effects.handlers import state, writer, writer_log  # Tellの記録と読み出しを使う。

@do  # 通知を処理してから、変換先のTellの記録を読み出す。
def collect_notification_log():  # ログ用ハンドラの結果を検証側へ返す。
    acknowledged = yield notify_progress()  # log版では確認操作の結果はFalseになる。
    entries = yield writer_log()  # notify・notify_thread・acknowledgeの3件を受け取る。
    return acknowledged, entries  # 確認結果と通知の記録を返す。

p_log = notifications_only(log_handler)(collect_notification_log())  # 通知の依頼をTellへ翻訳する。
acknowledged, entries = run(state()(writer(p_log)))  # 記録を保存し、読み出しまで評価する。
assert acknowledged is False  # log版は人間の確認を待たずFalseを返す。
assert [entry["event"] for entry in entries] == [  # 3種類の通知が順番どおり記録される。
    "notify", "notify_thread", "acknowledge"  # 開始、追加、確認の依頼がそれぞれTellになる。
]
```

## 本番の接続先を組み立てる

同じ`propose_and_notify`へ、本番のGit、console通知、Secret Managerのハンドラを取り付ける構成は次のとおりです。**このコードは構成のみを作ります。この記事の検証では`p_production`を実行していません。**

Secret Managerのハンドラは、初めて必要になった時点でクライアントを解決します。その内部で使うAsk・Try・State・Tellのハンドラも外側へ置きます。実行時にはGit/ghの実行環境、Google Cloudの認証と対象プロジェクトへの権限が必要です。

```python
from doeff_git.handlers.production import production_handlers as git_handlers  # git/ghを呼ぶ実装を選ぶ。
from doeff_google_secret_manager.handlers.production import production_handlers as cloud_secrets  # クラウドの実装を選ぶ。
from doeff_notify.handlers.stdout import console_handler  # 通知先を標準出力にする。
from doeff_core_effects.handlers import reader, try_handler  # クライアント解決のAskとTryを扱う。

p_production = git_handlers()(propose_and_notify(work_dir))  # Git操作を本番ハンドラへ渡す構成を作る。
p_production = notifications_only(console_handler)(p_production)  # 通知だけを標準出力へ送る構成にする。
p_production = cloud_secrets(project="example-project")(p_production)  # 秘密情報をクラウドへ要求する構成にする。
p_production = reader(env={"secret_manager_project": "example-project"})(p_production)  # クライアントの対象を指定する。
p_production = try_handler(p_production)  # クライアント解決が使うTryを解釈できるようにする。
p_production = state()(writer(p_production))  # クライアントの保存とTellの記録先を用意する。
```

## 処理の流れ

![PR作成の結果を、署名付き通知へつなぐ](/images/zenn-use-cases-v0/generated/operations-flow.png)

差分があればPRを作り、署名鍵を取得して、PR URLと署名を通知します。差分なしなら秘密情報の取得と通知へ進みません。

## 実装・実例を読む

[完全な検証例](examples/operations.py)では、stage済みの内容だけをcommitする指定、pushとPRのbranch一致、差分なしの分岐、通知のID、署名内容、戻り値の型、Tellへの翻訳を確認しています。実際のgit/ghの実行、通知の配送、クラウド認証は行っていません。テスト用Gitハンドラはstageやbranchを実際に操作するわけではなく、依頼の引数と固定応答を検証します。

- [Gitの本番ハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-git/src/doeff_git/handlers/production.py)
- [Gitのテスト用ハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-git/src/doeff_git/handlers/testing.py)
- [通知のテスト用ハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-notify/src/doeff_notify/handlers/testing.py)
- [秘密情報のAPI](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-secret/src/doeff_secret/effects/secrets.py)
- [Google Cloudのハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-google-secret-manager/src/doeff_google_secret_manager/handlers/production.py)

この草稿は上記の開発版を参照しています。ハンドラの合成そのものは[ハンドラの合成可能性](doeff-handlers.md)でも扱います。

[メイン記事へ戻る](doeff-main.md)
