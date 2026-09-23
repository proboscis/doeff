"""Dockerfile・環境構成・Programの移送を、外部接続なしで検証する。"""

from pathlib import Path  # ビルド用ソースの場所をPathとして扱う。
from tempfile import TemporaryDirectory  # 検証専用の空プロジェクトを一時的に作る。

import hy  # noqa: F401  # Hyで実装されたDocker・ml-nexusモジュールを読み込めるようにする。
from doeff_core_effects import state, writer  # Dockerfile収集が使うTellと内部状態を処理する。
from doeff_core_effects.handlers import slog_discard_handler  # 構成処理のログを検証中だけ破棄する。
from doeff_docker.effects import (  # Dockerfile・ビルド・実行・公開・シェルの依頼型を使う。
    Copy,  # ビルドコンテキスト内のファイルをイメージへ置く指示。
    DockerBuild,  # Dockerfileとコンテキストからイメージを作る依頼。
    DockerRun,  # イメージ内でProgramを実行する依頼。
    Expose,  # 公開予定のポートをDockerfileへ記す指示。
    From,  # ベースイメージを選ぶ指示。
    ImagePush,  # ローカルのイメージを指定タグで公開する依頼。
    Run,  # イメージ作成時のコマンドをDockerfileへ記す指示。
    SetEnv,  # コンテナの環境変数をDockerfileへ記す指示。
    ShellRun,  # 実際の外部プロセス起動に対応する依頼。
    ShellRunResult,  # シェル実行の終了コードと標準出力・標準エラーを表す値。
    Workdir,  # コンテナ内の作業ディレクトリを指定する指示。
)
from doeff_docker.handlers.docker import (  # 依頼をShellRunへ変換する現行ハンドラを使う。
    docker_build_handler,  # DockerBuildをdocker buildコマンドへ変換する。
    image_push_handler,  # ImagePushをdocker tagとdocker pushへ変換する。
)
from doeff_docker.handlers.dockerfile import collect_dockerfile  # 指示をDockerfile文字列へ集める。
from doeff_ml_nexus.docker import uv_gpu_image, uv_image  # CPU/GPU用のDockerfileを組み立てる。
from doeff_ml_nexus.effects import Resolve, RsyncTo, WriteFile  # 解決・配置・書き込みの依頼型。
from doeff_ml_nexus.serializer import default_serializer  # 実際のcloudpickle実装で往復を確かめる。
from external_workflows import prepare_remote_files  # 解決・転送・書き込みの3依頼を検証する。

from doeff import Pass, Pure, Resume, do, handler, run  # 依頼の委譲・再開とテスト用実行を使う。


@do  # Dockerfileの各指示をyieldするProgramを作る。
def image_definition():  # 依存を含まない最小のPython用イメージを記述する。
    yield From(image="python:3.13-slim")  # FROMの1行を収集する。
    yield Workdir(path="/app")  # WORKDIR /appの1行を収集する。
    yield Copy(src=".", dst="/app/")  # コンテキスト全体を配置するCOPYの1行を収集する。
    yield SetEnv(key="PYTHONUNBUFFERED", value="1")  # 出力をバッファしないENVの1行を収集する。
    yield Run(command="python -m compileall /app")  # ビルド時の構文確認コマンドを1行にする。
    yield Expose(port=8000)  # ポートのメタデータを記す。サーバーを起動する指示ではない。


@do  # 移送する対象を、未実行のProgramとして作る。
def calculate():  # この本体には外部サービスや追加ハンドラを必要とする依頼がない。
    return sum([1, 2, 3])  # 実行された環境で計算して6を返す。


@do  # Dockerfile収集・ビルド・実行を順番に合成する。
def build_and_execute(context: Path):  # ビルドに使うファイル群の場所を受け取る。
    dockerfile = yield collect_dockerfile(image_definition())  # 6行のDockerfile文字列を得る。
    yield DockerBuild(  # 文字列とソースを渡し、イメージの構築が終わるまで待つ。
        dockerfile=dockerfile, tag="article-example:local", context_path=context,  # ビルドの入力。
    )
    return (yield DockerRun(image="article-example:local", program=calculate()))  # 実行結果6を返す。


@do  # 公開はビルド・検証とは別のProgramとして定義する。
def publish_image(remote_tag: str):  # 公開先のタグを明示的に受け取る。
    return (yield ImagePush(local_tag="article-example:local", remote_tag=remote_tag))  # 公開先を返す。


@do  # シェル実行も、ハンドラへ依頼するProgramにする。
def inspect_docker_version():  # Dockerのバージョン文字列を取得する手順。
    result = yield ShellRun(args=("docker", "--version"))  # 終了コードと出力を受け取る。
    if result.returncode != 0:  # コマンドが失敗した結果を成功扱いしない。
        raise RuntimeError(result.stderr.decode())  # 標準エラーを原因として呼び出し元へ伝える。
    return result.stdout.decode()  # 成功時は標準出力を文字列として返す。


@do  # コンテナの実行先を、計算本体の外側で選ぶ。
def run_in_container(image: str, host: str, program):  # 構築済みイメージと移送対象を受け取る。
    return (yield DockerRun(image=image, host=host, program=program))  # 指定先の計算結果を返す。


@do  # GPUの指定も計算本体の外側に置く。
def run_on_gpu(image: str, host: str, program):  # GPU対応の構築済みイメージを受け取る。
    return (yield DockerRun(image=image, host=host, gpu=True, program=program))  # GPU付きの実行依頼。


def verify_serialization() -> None:  # 移送境界の前提を、Dockerを起動せず検査する。
    restored_pure = default_serializer.loads(default_serializer.dumps(Pure(42)))  # 値のProgramを復元。
    assert run(restored_pure) == 42  # VMのPureを往復させても値が変わらないことを確認する。
    restored_program = default_serializer.loads(default_serializer.dumps(calculate()))  # @doも復元。
    assert run(restored_program) == 6  # 未実行Programの復元後に本体を実行できることを確認する。
    value = {"total": 6, "items": [1, 2, 3]}  # 結果側も構造を持つ値で往復させる。
    assert default_serializer.loads(default_serializer.dumps(value)) == value  # 結果の値を維持する。


def verify() -> None:  # テスト境界でrunを呼び、依頼の解釈と順序を確認する。
    instructions = run(state()(writer(collect_dockerfile(image_definition()))))  # 指示を収集する。
    assert instructions.splitlines() == [  # 6種類の依頼が順番を保って文字列になったことを検査する。
        "FROM python:3.13-slim",  # ベースイメージは指定どおり。
        "WORKDIR /app",  # 作業ディレクトリを維持する。
        "COPY . /app/",  # コピー元とコピー先を維持する。
        "ENV PYTHONUNBUFFERED=1",  # 環境変数のキーと値を維持する。
        "RUN python -m compileall /app",  # 実行せず、Dockerfileの文字列へ変換する。
        "EXPOSE 8000",  # ポート番号を維持する。
    ]
    verify_serialization()  # Programと結果のcloudpickle往復も、独立した検証で確かめる。

    with TemporaryDirectory() as directory:  # 検証専用の作業パスだけを一時的に用意する。
        source = Path(directory)  # Resolveへ返すソースのPathを決める。
        observed = []  # 実I/Oへ進むはずだった依頼を、テスト内だけで保存する。

        @handler  # 外部I/Oを依頼単位で置き換える固定ハンドラを作る。
        @do  # 各依頼への応答をResumeで本体へ戻す。
        def fixed_environment(effect, k):  # 実Docker・SSH・ファイル転送を設置しない。
            if isinstance(effect, Resolve):  # 配置手順が要求したソースの解決を受ける。
                observed.append(effect)  # 後で解決対象と順序を検査する。
                assert effect.target == "article-example"  # 解決対象が固定したプロジェクト名である。
                assert effect.kind is Path  # 求める解決結果の型がPathである。
                return (yield Resume(k, source))  # 検証専用プロジェクトの場所を返す。
            if isinstance(effect, (RsyncTo, WriteFile)):  # 転送・書き込みは依頼の記録だけにする。
                observed.append(effect)  # ホスト・転送先・本文を後で検査できるように残す。
                return (yield Resume(k, None))  # 実I/Oなしで、完了した応答を返す。
            if isinstance(effect, DockerRun):  # 実コンテナへ渡す直前の依頼を受け取る。
                observed.append(effect)  # イメージ名・ホスト・GPUフラグを記録する。
                result = yield effect.program  # テストでは渡された本体を同じプロセスで評価する。
                return (yield Resume(k, result))  # 本体の結果をコンテナ実行の応答として返す。
            if isinstance(effect, ShellRun):  # DockerBuild/ImagePushが出すコマンドも実行しない。
                observed.append(effect)  # docker build/tag/pushの引数を後で確認する。
                response = ShellRunResult(returncode=0, stdout=b"fixture-version\n", stderr=b"")  # 固定値。
                return (yield Resume(k, response))  # シェル成功時の値だけを返す。
            return (yield Pass(effect, k))  # Tell・ログ・状態などはそれぞれの外側ハンドラへ渡す。

        def execute(program):  # テスト用に本物の変換ハンドラと固定I/Oを合成する。
            wrapped = docker_build_handler(image_push_handler(program))  # ShellRunへの変換は実装を使う。
            wrapped = fixed_environment(wrapped)  # 外へ出る操作だけを記録と固定応答へ差し替える。
            return run(state()(writer(slog_discard_handler(wrapped))))  # 収集・ログの依頼を処理する。

        assert execute(build_and_execute(source)) == 6  # ビルド完了後の計算結果が戻ることを確認。
        assert observed[0].args[:3] == ("docker", "build", "-t")  # 実装がビルドの依頼を出した。
        assert observed[0].stdin_data.decode() == instructions  # Dockerfileが標準入力へ渡る。
        assert isinstance(observed[1], DockerRun)  # ビルドの後にコンテナ実行が依頼される。
        observed.clear()  # 次は直接のファイル配置だけを観測する。
        assert execute(prepare_remote_files("article-example", "worker.invalid", "/work/src")) == "/work/src"  # 配置先。
        assert [type(item) for item in observed] == [Resolve, RsyncTo, WriteFile]  # 3操作の順序。
        assert observed[1].excludes == (".git", ".venv")  # 直接転送の除外対象を維持する。
        assert observed[2].content == "文書処理の例"  # 追加ファイルの本文を維持する。

        for compose, host, gpu in (  # 同じ計算を3通りのコンテナ実行依頼へ渡す。
            (run_in_container, "localhost", False),  # ローカルCPUはGPUを要求しない。
            (run_in_container, "worker.invalid", False),  # リモートCPUもGPUを要求しない。
            (run_on_gpu, "worker.invalid", True),  # GPU用だけDockerRun.gpuを有効にする。
        ):
            observed.clear()  # 前の構成の依頼を混ぜずに検査する。
            program = compose("article-example:local", host, calculate())  # 構築済みイメージを指定。
            assert execute(program) == 6  # どの依頼でも、本体の結果を呼び出し元へ戻す。
            assert [type(item) for item in observed] == [DockerRun]  # この例はビルドせず実行を依頼。
            assert observed[0].host == host  # 選択したホストが実行先に残る。
            assert observed[0].gpu is gpu  # 選択したGPU指定が実行依頼に残る。

        cpu = execute(collect_dockerfile(uv_image("python:3.13-slim")))  # CPU用の指示だけを収集する。
        gpu = execute(collect_dockerfile(uv_gpu_image("cuda-example:local")))  # GPU用も文字列だけ作る。
        assert "NVIDIA_VISIBLE_DEVICES=all" not in cpu  # CPU用にはGPU環境変数が入らない。
        assert "NVIDIA_VISIBLE_DEVICES=all" in gpu  # GPU用はコンテナへGPUを見せる設定を含む。
        assert "uv sync --frozen" in cpu  # CPU用が依存固定の指示を持つ。
        assert "uv sync --frozen" in gpu  # GPU用も依存固定の指示を持つ。

        observed.clear()  # 公開の依頼をビルドの記録から分ける。
        assert execute(publish_image("registry.invalid/article:review")) == "registry.invalid/article:review"  # 公開先。
        assert [item.args[1] for item in observed] == ["tag", "push"]  # 実公開せず2コマンドの順序を検査。
        assert execute(inspect_docker_version()) == "fixture-version\n"  # ShellRunの応答を文字列へ戻す。


if __name__ == "__main__":  # ファイルの直接実行時だけオフライン検証を動かす。
    verify()  # 指示収集・依頼構築・Programシリアライズの意味を検査する。
    print("Dockerfile・3環境の依頼・Programのpickle往復: OK(実Docker・SSH・GPUなし)")  # 成功時の表示。
