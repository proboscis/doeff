---
title: "計算と実行環境を分ける — Dockerとdoeff-ml-nexus"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

手元で書いた計算を、依存を揃えたコンテナやGPUのあるホストへ渡したい。doeffでは、**計算本体を表すProgram**と、**その実行場所を選ぶ依頼**を分けられます。

Dockerfileの構成、ビルド、ソースの配置、コンテナ実行を別々のエフェクトとして扱います。テストでは、その依頼を受け取るハンドラを交換して、実際にDockerを起動せずに中身を確かめられます。

![計算本体を値として、実行の依頼へ渡す](/images/zenn-use-cases-v0/generated/remote-concept.png)

計算本体をDockerRunへ渡し、実行先は依頼の引数とハンドラで決めます。固定ハンドラなら同じ依頼を受け取り、外部接続せずに検証できます。

## まず、DockerfileをProgramとして組み立てる

```python
from pathlib import Path  # 後続のビルド例で、ソースの場所をPathとして渡す。
import hy  # Hy実装のモジュールをPythonから読み込めるようにする。
from doeff import do, run  # Programの定義と、この例のテスト実行に使う。
from doeff_core_effects import state, writer  # 収集に必要な状態とTellを処理する。
from doeff_docker.effects import (  # Dockerfileの6種類の指示を使う。
    From, Workdir, Copy, SetEnv, Run, Expose,  # ベース・作業場所・コピー・環境・コマンド・ポート。
)
from doeff_docker.handlers.dockerfile import collect_dockerfile  # 指示を文字列へ集める。

@do  # Dockerfileの各指示をyieldするProgramを作る。
def image_definition():  # 依存を含まない最小のPython用イメージを記述する。
    yield From(image="python:3.13-slim")  # FROMの1行を収集する。
    yield Workdir(path="/app")  # WORKDIR /appの1行を収集する。
    yield Copy(src=".", dst="/app/")  # コンテキスト全体を配置するCOPYの1行を収集する。
    yield SetEnv(key="PYTHONUNBUFFERED", value="1")  # 出力をバッファしないENVの1行を収集する。
    yield Run(command="python -m compileall /app")  # ビルド時の構文確認コマンドを1行にする。
    yield Expose(port=8000)  # ポートのメタデータを記す。サーバーを起動する指示ではない。


text = run(state()(writer(collect_dockerfile(image_definition()))))  # テストとして指示を収集する。
assert text.splitlines()[0] == "FROM python:3.13-slim"  # 最初の指示が文字列になる。
assert len(text.splitlines()) == 6  # 6回のyieldが、順序を保った6行になる。
```

`From`や`Run`を解釈するコレクタは、各指示を`Tell`へ変換します。`collect_dockerfile`はそれを収集してDockerfileの文字列を返します。この実行では`Run(command=...)`のコマンド自体は起動しません。

`Expose`もDockerfileのメタデータです。ポート8000でサーバーが動き始めるという意味ではありません。

## ビルド・実行・公開も別々の依頼にする

上の定義に続けて、ビルドと計算をつなぎます。次の`calculate()`は、呼んだ時点では未実行のProgramです。

```python
from doeff_docker.effects import (  # Docker操作とシェル操作の依頼型を追加する。
    DockerBuild, DockerRun, ImagePush, ShellRun,  # ビルド・計算の実行・公開・プロセス起動。
)

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

```

`DockerBuild`はイメージのタグを返し、`ImagePush`は公開先のタグを返します。公開するProgramを定義するだけでは、レジストリへの送信は起きません。

実装の`docker_build_handler`と`image_push_handler`は、これらの依頼を`ShellRun`へ変換します。テストでは**その外側に置く`ShellRun`のハンドラを固定応答へ交換**できます。完全な例では、Dockerfileが`docker build`の標準入力に渡ることと、公開が`docker tag`→`docker push`の順になることを、コマンドを起動せずに確認しています。

## 実行場所とGPU指定を、本体の外側へ置く

構築済みのイメージに対して、実行場所を選ぶ部分だけを書くと次の形です。

```python
@do  # コンテナの実行先を、計算本体の外側で選ぶ。
def run_in_container(image: str, host: str, program):  # 構築済みイメージと移送対象を受け取る。
    return (yield DockerRun(image=image, host=host, program=program))  # 指定先の計算結果を返す。


@do  # GPUの指定も計算本体の外側に置く。
def run_on_gpu(image: str, host: str, program):  # GPU対応の構築済みイメージを受け取る。
    return (yield DockerRun(image=image, host=host, gpu=True, program=program))  # GPU付きの実行依頼。

```

CPU/GPUの選択は`calculate()`の手順を変えません。ただし、GPU実行にはGPU対応のイメージ・ホスト・Docker設定が必要です。`gpu=True`だけで環境が準備されるわけではありません。

`doeff-ml-nexus`には、uvで依存を揃えるDockerfileを構成するヘルパーもあります。

```python
from doeff_ml_nexus.docker import uv_image, uv_gpu_image  # CPU/GPU用DockerfileのProgramを作る。
cpu = run(state()(writer(collect_dockerfile(uv_image("python:3.13-slim")))))  # CPU用の文字列を得る。
gpu = run(state()(writer(collect_dockerfile(uv_gpu_image("cuda-example:local")))))  # GPU用も収集する。
assert "NVIDIA_VISIBLE_DEVICES=all" not in cpu  # CPU用はGPUの環境変数を持たない。
assert "NVIDIA_VISIBLE_DEVICES=all" in gpu  # GPU用はコンテナへGPUを見せる環境変数を持つ。
assert "uv sync --frozen" in cpu and "uv sync --frozen" in gpu  # どちらも依存固定の指示を持つ。
```

`cuda-example:local`は説明用のタグです。この例は文字列を収集するだけなので、イメージの取得やビルドは行いません。

## ハンドラを交換して、渡したProgramを確かめる

次は実コンテナの代わりに、受け取ったProgramを同じPythonプロセスで評価するテストです。**リモート実行の成功を検証するものではなく、依頼に本体・宛先・GPU指定が正しく載ることを確かめます。**

```python
from doeff import Pass, Resume, handler  # 担当外への委譲と、依頼元の再開に使う。


def verify_delivery():  # テスト境界にだけrunを置き、依頼の中身を検査する。
    requests = []  # この固定ハンドラへ届いた実行依頼を保存する。

    @handler  # DockerRunの解釈だけを、テスト用に交換可能にする。
    @do  # 受け取ったProgramもyieldで合成する。
    def fixed_container(effect, k):  # DockerやSSHのクライアントを作らず、依頼そのものを受け取る。
        if not isinstance(effect, DockerRun):  # コンテナ実行以外の依頼には介入しない。
            return (yield Pass(effect, k))  # 担当する外側のハンドラへ引き渡す。
        requests.append(effect)  # 実行場所とGPU指定を後で確認できるようにする。
        value = yield effect.program  # テスト内では本体をこのプロセスで実行し、6を得る。
        return (yield Resume(k, value))  # 実行依頼の応答として6を返す。

    program = run_on_gpu("article-example:local", "worker.invalid", calculate())  # 依頼を構成する。
    assert requests == []  # Programを作っただけでは本体も実行依頼も動かない。
    assert run(fixed_container(program)) == 6  # 固定ハンドラを通して、本体の結果が戻る。
    assert requests[0].host == "worker.invalid" and requests[0].gpu is True  # 宛先とGPU指定を確認。

verify_delivery()  # 上記のオフライン検証だけを実行する。
```

この切り分けは[ハンドラの合成](doeff-handlers.md)と同じです。ビルド、転送、実行の依頼を別のハンドラで解釈できます。

## 解決・転送・ファイル配置も、順番に合成する

`doeff-ml-nexus`の`Resolve`はソース識別子の解決、`RsyncTo`は転送、`WriteFile`はホスト上のファイル作成を表します。次のProgramも、定義しただけでは転送しません。

```python
@do  # 解決・転送・書き込みの順序をProgramとして保持する。
def prepare_remote_files(source_id: str, host: str, destination: str):  # 配置する対象と場所を固定する。
    from doeff_ml_nexus.effects import Resolve, RsyncTo, WriteFile  # 各操作の依頼型を読み込む。

    source = yield Resolve(target=source_id, kind=Path)  # ソース識別子に対応するPathを受け取る。
    yield RsyncTo(  # 転送先ホストへの配置を依頼し、完了を待つ。
        src=source, host=host, dst_path=destination,  # 解決したソースと指定先を結び付ける。
        excludes=(".git", ".venv"),  # この直接転送の例では履歴と仮想環境を含めない。
    )
    yield WriteFile(  # 配置先に追加する文字列も書き込み依頼として表す。
        host=host, path=f"{destination}/example.txt", content="文書処理の例",  # UTF-8本文の内容と場所。
    )
    return destination  # 3つの依頼が完了したら、配置先を呼び出し元へ返す。

```

[完全な例](examples/container_program.py)では、この3種類にも固定ハンドラを取り付けます。`Resolve`→`RsyncTo`→`WriteFile`の順序、除外対象、書き込み本文を実際に検査しています。

## シリアライズで渡すものは、未実行のProgram

実装の`docker_run_handler`は、`Ask("serializer")`で取得したシリアライザでProgramを保存します。コンテナ側のランナーは復元したProgramを`run(program)`で実行し、結果をシリアライズして返します。

この環境では、`Pure`と`@do`のProgramがどちらもcloudpickleで往復し、復元後に実行できることを確認しました。

```python
from doeff import Pure  # 既知の値を返すProgramで、VMの値ノードも確認する。
from doeff_ml_nexus.serializer import default_serializer  # 本番側と同じcloudpickle実装を使う。

payload = default_serializer.dumps(calculate())  # まだ実行していない計算をバイト列へ変換する。
restored = default_serializer.loads(payload)  # バイト列からProgramを復元する。
assert run(restored) == 6  # 復元後に計算を実行して、元と同じ結果を得る。
value_program = default_serializer.loads(default_serializer.dumps(Pure(42)))  # 値ノードも往復させる。
assert run(value_program) == 42  # 復元したPureが42を返すことを確かめる。
result = {"total": 6, "items": [1, 2, 3]}  # 計算結果側も、構造を持つ値で確認する。
assert default_serializer.loads(default_serializer.dumps(result)) == result  # 結果の構造を維持する。
```

ここで移送するのは**未実行の計算の定義**です。動作中のPython generatorや継続のスナップショットを保存する説明ではありません。クラッシュ後の再開については[durable execution](doeff-durable.md)で、完了した処理の記録を使う方法を扱います。

シリアライズの成否はPython・VM・依存の版や、Programが閉じ込めた値に依存します。また、現在のランナーは本体を裸の`run(program)`で実行します。実行元のハンドラが自動的に移送されるわけではないため、本体がエフェクトを出す場合には、移送するProgram側に必要なハンドラを明示的に含める設計が必要です。この例はその条件を単純にするため、純粋な計算を移送しています。

## 上位の環境ファクトリには、現在import時の不整合がある

実装には、ローカル依存の検出、ビルド用の配置、依存パスの書き換え、Dockerfile構成、ビルド、実行をつなぐ`make_remote_uv_interpreter`と`make_local_uv_interpreter`があります。呼び出しの契約は、**ファクトリProgramをyieldして実行関数を得て、その関数が返すProgramをさらにyieldする**形です。

ただし今回の検証環境では、`doeff_ml_nexus.interpreters`のimportが`HyMacroExpansionError`で失敗しました。`defk`が要求する`:pre`契約を、これらのファクトリの実装が持っていないためです。**以下は実装された呼び出し形の説明であり、現在実行できるサンプルとしては扱いません。** この不整合を直す前に、利用可能な入口として勧めることはできません。

```python
@do  # 実行環境の構成と本体の実行を、同じProgramの中で合成する。
def run_in_remote_environment(source_id: str, host: str, base_image: str, program):  # 現版はimport不整合で実行不可。
    from doeff_ml_nexus.interpreters import make_remote_uv_interpreter  # リモート用の構成関数。

    interpreter = yield make_remote_uv_interpreter(  # ファクトリProgramを評価して実行関数を得る。
        source_id, host, base_image, False,  # この構成ではGPUを要求しない。
    )
    return (yield interpreter(program))  # 得た関数もProgramを返すので、yieldして最終結果を受け取る。


@do  # ローカルのDocker環境も、処理本体の外側で構成する。
def run_in_local_container(source_id: str, base_image: str, program):  # 現版はimport不整合で実行不可。
    from doeff_ml_nexus.interpreters import make_local_uv_interpreter  # ローカル用の構成関数。

    interpreter = yield make_local_uv_interpreter(source_id, base_image)  # 実行関数が返るまで待つ。
    return (yield interpreter(program))  # localhost向けの構成・ビルド・実行の結果を返す。

```

したがって、この記事で実行確認した範囲は、Dockerfile収集、各エフェクトの依頼・応答、固定ハンドラによる合成、Programのcloudpickle往復です。上位ファクトリの一連の処理、実Docker・SSH・GPU・イメージ公開は成功を確認していません。

## 処理の流れ

![Programを保存して渡し、実行結果を受け取る](/images/zenn-use-cases-v0/generated/remote-flow.png)

本番ハンドラの実装はProgramをシリアライズしてランナーへ渡し、結果を復元して続きを再開します。この記事ではシリアライズの往復を同じプロセスで確認し、実コンテナでの往復は実行していません。

## 実装・実例を読む

- [Dockerの依頼型](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-docker/src/doeff_docker/effects.hy)
- [Dockerfileの収集](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-docker/src/doeff_docker/handlers/dockerfile.hy)
- [環境を構成するファクトリ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-ml-nexus/src/doeff_ml_nexus/interpreters.hy)
- [Programの移送を担当するハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-ml-nexus/src/doeff_ml_nexus/handlers/docker.hy)
- [コンテナ側のランナー](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-ml-nexus/src/doeff_ml_nexus/runner.hy)
- [シリアライズのテスト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-ml-nexus/tests/test_serializer.py)

この草稿は上記の開発版を参照し、手元の実装で動作を確認しています。

[メイン記事へ戻る](doeff-main.md)
