# doeff-cluster の土台だけの image(設計 worker-runtime-env.md 節 3.5・E13)。
#
# 何を持つか: coordinator と worker が「宣言した doeff の commit から自分を起こす」ための道具だけ。doeff・doeff-cluster・Python の
# package・Python の interpreter は持たない。起動の script(../boot.sh)が WORKER_DOEFF_COMMIT の木を展開して `uv sync --locked
# --package doeff-cluster` し、その venv から coordinator / worker を起こす。業務の code は worker が job ごとに実行環境の root へ
# 準備する(E10)。資格(deploy key・借り手札)は Pod の Secret から読む。
#
# 作り直す理由(この 2 種類だけ — それ以外の変更は tests/test_base_image_contract.hy が赤にする):
#   (1) 土台の道具(uv・git・ssh・Rust の toolchain・tini・起動の script)の版を変える時
#   (2) 業務の Python の package が新しい OS の library を要する時
# doeff や業務の code を変えた時・Python の package を足した時・Python の版を変えた時は作り直さない(宣言と root が持つ)。
#
# OS の package(名・理由の種類・何に使うか — 検査はこの表に無い package を赤にする):
#   ca-certificates   (1) HTTPS(uv の Python と package・GitHub)
#   git               (1) mirror の clone と fetch・uv の git の依存
#   openssh-client    (1) 非公開の repo を読み取り専用の deploy key で読む
#   tini              (1) pid 1 — job の子が残した孫 process を刈る(#697)
#   build-essential   (1) doeff-vm の native の build(cc・ld)
#   pkg-config        (1) native の build が OS の library を探す
#   libssl-dev        (1) native の build が openssl を結ぶ
#
#   docker build -t <名> -f packages/doeff-cluster/deploy/base/Dockerfile packages/doeff-cluster/deploy
ARG UV_VERSION=0.12.19
ARG RUST_VERSION=1.96.1
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM rust:${RUST_VERSION}-slim-bookworm AS rust

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git openssh-client tini build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
# Rust の toolchain は読むだけ(RUSTUP_HOME)。crate の取得先(CARGO_HOME)は起動の script が node の dir へ向ける。
COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo/bin /usr/local/cargo/bin
COPY boot.sh /opt/worker-boot.sh
ENV RUSTUP_HOME=/usr/local/rustup \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin
# ubuntu:24.04 の uid 1000(ubuntu)を worker に使う。家は起動の script が書ける dir へ向ける。
USER 1000:1000
# 土台の確かめ: 道具が在り、Python と doeff は無い(uv が入れる前の姿)。
RUN uv --version && git --version && ssh -V && cargo --version && rustc --version \
    && ! command -v python3 && ! command -v python && ! command -v hy
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/worker-boot.sh"]
