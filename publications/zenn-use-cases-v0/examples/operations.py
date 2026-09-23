"""Git・通知・秘密情報を、外部接続のないハンドラで検証する。"""

from hashlib import sha256  # 通知に添える署名をSHA-256で計算する。
from hmac import new as make_hmac  # 秘密の値を本文へ出さず、PR URLの署名を作る。
from pathlib import Path  # Git操作の対象を表す。テストではメモリ上のキーになる。

from doeff_core_effects.handlers import state, writer, writer_log  # Tellを状態へ集め、読み出す。
from doeff_git.effects import (  # Gitの依頼を使う。
    CreatePR,  # PRを作成し、URLと番号を受け取る。
    GitCommit,  # stage済みの内容をcommitする依頼を作る。
    GitDiff,  # 差分を文字列として受け取る。
    GitPull,  # 作業開始前のbranch更新を依頼する。
    GitPush,  # 現在のbranchのpushを依頼する。
    MergePR,  # 承認後のPRのmergeを依頼する。
)
from doeff_git.handlers.testing import MockGitRuntime, mock_handlers  # Git操作をメモリへ記録する。
from doeff_git.types import PRHandle  # 作成したPRの番号・URLを受け渡す。
from doeff_notify.effects import Acknowledge, Notify, NotifyThread  # 通知と確認の依頼を使う。
from doeff_notify.handlers.log import log_handler  # 通知をTellへ翻訳するハンドラを使う。
from doeff_notify.handlers.testing import testing_handler  # 通知を配送せず収集する。
from doeff_secret.effects import (  # 秘密情報の操作を使う。
    DeleteSecret,  # 例で作成した秘密情報を削除する。
    GetSecret,  # 値の保管先を指定せず取得を依頼する。
    ListSecrets,  # 保存されている項目名の一覧を受け取る。
    SetSecret,  # 説明用の値の保存を依頼する。
)
from doeff_secret.handlers import env_var_handler  # 環境変数形式の値を優先するハンドラを使う。
from doeff_secret.testing import in_memory_handlers  # 秘密情報をメモリ内だけで扱う。

from doeff import Pass, do, handler, run  # 手順をProgramへ組み立て、テストで評価する。


def notifications_only(raw_handler):  # 通知以外を現在のPass APIで委譲するハンドラを組み立てる。
    @do  # effectの選別と継続の受け渡しをProgramにする。
    def dispatch(effect, k):  # 依頼と、その結果を待つ継続を受け取る。
        if isinstance(effect, (Notify, NotifyThread, Acknowledge)):  # 通知の3種類だけを渡す。
            return (yield raw_handler(effect, k))  # 既存ハンドラが通知結果を返して継続を再開する。
        return (yield Pass(effect, k))  # 通知以外は引数を明示して外側のハンドラへ渡す。
    return handler(dispatch)  # Programを実行せず、取り付け用の関数を返す。


@do  # 更新操作を、まだ実行しないProgramにする。
def refresh_checkout(work_dir: Path):  # 変更を加える前の作業用branchを受け取る。
    yield GitPull(work_dir=work_dir)  # 現在のbranchを更新する。テストではpullの記録だけ残る。


@do  # 差分確認からPR作成までの順番をProgramにする。
def propose_change(work_dir: Path):  # 作業用branchで必要な変更をstage済みの場所を受け取る。
    diff = yield GitDiff(work_dir=work_dir, staged=True)  # commit予定の差分を文字列で受け取る。
    if not diff:  # stageされた変更がなければ、書き込み操作へ進まない。
        return None  # 変更なしを正常な結果として返す。
    yield GitCommit(work_dir=work_dir, message="説明を更新する", all=False)  # stage済みの内容をcommitする。
    yield GitPush(work_dir=work_dir)  # 現在のbranchをpushする。別名のbranchへは切り替えない。
    return (yield CreatePR(work_dir=work_dir, title="説明の更新", draft=True))  # 同じbranchのPRを返す。


@do  # PR作成とmergeを、呼び出す時点が異なるProgramに分ける。
def merge_approved(pr: PRHandle):  # 呼び出し側がレビュー・承認を確認したPRを受け取る。
    yield MergePR(pr=pr)  # 指定したPRをmergeする。承認の自動判定は行わない。


@do  # 開始・完了・確認の順番をProgramにする。
def notify_progress():  # 外部の配送先を関数内に固定せず通知する。
    sent = yield Notify(message="変換を開始しました", title="文書処理")  # 通知とスレッドのIDを受け取る。
    yield NotifyThread(thread_id=sent.thread_id, message="変換が完了しました")  # 同じスレッドへ追加する。
    return (yield Acknowledge(notification_id=sent.notification_id, timeout=0))  # 確認済みかを返す。


@do  # 秘密情報の作成・取得・一覧・削除を同じProgramにする。
def secret_lifecycle():  # 実行側が選んだストアに対して操作する。
    yield SetSecret(secret_id="demo", value="example-only")  # 説明用の値を保存する。
    value = yield GetSecret(secret_id="demo")  # メモリハンドラではbytesが返る。
    names = yield ListSecrets()  # 保存直後なのでdemoを含む一覧が返る。
    yield DeleteSecret(secret_id="demo")  # この例で作った項目を削除する。
    remaining = yield ListSecrets()  # 空のストアで開始した場合は空リストになる。
    return value, names, remaining  # 取得値と削除前後の一覧を検証側へ返す。


@do  # 取得先を固定せず、署名に使うバイト列を準備する。
def signing_key():  # 環境変数とメモリで異なる戻り値の型をここでそろえる。
    value = yield GetSecret(secret_id="notice-signing-key")  # ハンドラからstrまたはbytesを受け取る。
    return value.encode("utf-8") if isinstance(value, str) else value  # HMACへ渡せるbytesを返す。


@do  # Git・秘密情報・通知のProgramをyieldでつなぐ。
def propose_and_notify(work_dir: Path):  # stage済みの変更についてPRを提案する。
    pr = yield propose_change(work_dir)  # 差分があればPRHandle、なければNoneを受け取る。
    if pr is None:  # 提案がない場合は署名や通知を依頼しない。
        return None  # 変更なしを呼び出し側へ返す。
    key = yield signing_key()  # 秘密の値そのものを通知に含めず、署名計算だけに使う。
    signature = make_hmac(key, pr.url.encode("utf-8"), sha256).hexdigest()  # PR URLの署名を作る。
    yield Notify(message="PRを確認してください", link=pr.url, metadata={"signature": signature})  # URLと署名を送る。
    return pr  # 後続のレビュー処理へPRを渡す。ここではmergeしない。


@do  # 通知を処理してから、変換先のTellの記録を読み出す。
def collect_notification_log():  # ログ用ハンドラの結果を検証側へ返す。
    acknowledged = yield notify_progress()  # log版では確認操作の結果はFalseになる。
    entries = yield writer_log()  # notify・notify_thread・acknowledgeの3件を受け取る。
    return acknowledged, entries  # 確認結果と通知の記録を返す。


def verify() -> None:  # テストの境界でProgramを評価し、結果と依頼を確認する。
    work_dir = Path("/example/document-project")  # ディレクトリを作らずGit操作のキーとして使う。
    runtime = MockGitRuntime(diff_by_work_dir={str(work_dir): "+説明"})  # 差分がある応答を設定する。
    run(mock_handlers(runtime=runtime)(refresh_checkout(work_dir)))  # pullの依頼をメモリへ記録する。
    assert len(runtime.pulls) == 1  # pullがちょうど1回依頼されたことを確認する。
    pr = run(mock_handlers(runtime=runtime)(propose_change(work_dir)))  # PR作成まで評価する。
    assert pr is not None  # 差分があればPRが返ることを確認する。
    assert runtime.commits == [(str(work_dir), "説明を更新する", False)]  # all=Falseを含めて確認する。
    assert len(runtime.pushes) == 1  # pushは1回だけ依頼される。
    assert runtime.pushes[0][2] == pr.branch == "mock-branch"  # pushとPRのbranchが一致する。
    assert not runtime.merged_pr_numbers  # PRを作っただけではmergeされない。
    run(mock_handlers(runtime=runtime)(merge_approved(pr)))  # 承認後の段階を別に評価する。
    assert runtime.merged_pr_numbers == [pr.number]  # 指定したPRだけがmerge対象になる。

    unchanged = MockGitRuntime(diff_by_work_dir={str(work_dir): ""})  # 差分なしの応答を用意する。
    assert run(mock_handlers(runtime=unchanged)(propose_change(work_dir))) is None  # 変更なしを返す。
    assert not unchanged.commits  # 差分なしならcommitしない。
    assert not unchanged.pushes  # 差分なしならpushしない。
    assert not unchanged.prs  # 差分なしならPRを作らない。

    notify, capture = testing_handler(auto_acknowledge=True)  # 既知の通知IDを確認済みにする。
    assert run(handler(notify)(notify_progress())) is True  # テスト用ハンドラの確認結果はTrueになる。
    assert len(capture.notifications) == len(capture.thread_updates) == 1  # 開始・追加を1件ずつ集める。
    assert len(capture.acknowledgements) == 1  # 確認の依頼も1件集める。
    assert capture.thread_updates[0].thread_id == capture.acknowledgements[0].notification_id  # IDを引き継ぐ。
    assert run(in_memory_handlers()(secret_lifecycle())) == (b"example-only", ["demo"], [])  # CRUDを確認する。

    combined_git = MockGitRuntime(diff_by_work_dir={str(work_dir): "+説明"})  # 合成した処理用のGit記録を作る。
    combined_notify, notices = testing_handler()  # 合成した処理用の通知記録を作る。
    p_combined = mock_handlers(runtime=combined_git)(propose_and_notify(work_dir))  # Gitだけを解釈する。
    p_combined = notifications_only(combined_notify)(p_combined)  # 通知だけを解釈するハンドラを外側へ置く。
    p_combined = in_memory_handlers(seed_data={"notice-signing-key": b"demo-key"})(p_combined)  # 説明用の鍵を渡す。
    proposal = run(p_combined)  # 3種のハンドラでひとつのProgramを評価する。
    assert proposal is not None  # 作成したPRが後続へ返ることを確認する。
    assert len(notices.notifications) == 1  # 提案の通知は1件だけ発生する。
    notice = notices.notifications[0]  # 記録された通知の内容を取り出す。
    assert notice.link == proposal.url  # 通知が作成したPRを指すことを確認する。
    expected = make_hmac(b"demo-key", proposal.url.encode("utf-8"), sha256).hexdigest()  # 同じ入力の署名を求める。
    assert notice.metadata == {"signature": expected}  # 鍵ではなく署名が通知へ渡ることを確認する。
    assert not combined_git.merged_pr_numbers  # 合成してもmergeは別の段階のままになる。

    p_empty = mock_handlers(runtime=unchanged)(propose_and_notify(work_dir))  # 差分なしで合成処理を評価する。
    assert run(p_empty) is None  # Secret・通知のハンドラがなくても、依頼しないため成功する。
    p_env = env_var_handler(environ={"ARTICLE_NOTICE_SIGNING_KEY": "from-env"}, prefix="ARTICLE_")(signing_key())  # strを返す取得先を選ぶ。
    assert run(p_env) == b"from-env"  # @do側の変換によりメモリ版と同じbytesになる。

    p_log = notifications_only(log_handler)(collect_notification_log())  # 通知をTellへ翻訳する。
    acknowledged, entries = run(state()(writer(p_log)))  # Tellを状態へ保存し、ログを読み出す。
    assert acknowledged is False  # log版は確認完了を待たずFalseを返す。
    assert [entry["event"] for entry in entries] == ["notify", "notify_thread", "acknowledge"]  # 順番を確認する。


if __name__ == "__main__":  # このファイルを直接実行した時だけオフライン検証を始める。
    verify()  # 結果、分岐、ハンドラ合成、通知の翻訳を確認する。
    print("Git・通知・秘密情報の合成、branch一致、差分なし、取得値の型変換、Tellへの翻訳: OK")  # 成功を表示する。
