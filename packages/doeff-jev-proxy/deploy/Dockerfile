# Jev の呼び出しを覚える代理(doeff-jev-proxy)の image。
# 土台(BASE)= doeff の venv を持つ業務の image(PATH に hy・venv に doeff・doeff-hy・doeff-core-effects・doeff-jev・doeff-records・
# httpx が入っている物 — doeff を依存に持つ業務の repo の image)。ここで足すのは、この package の source と検だけ(build も install も
# しない — PYTHONPATH に載せる)。doeff-cluster の deploy/Dockerfile と同じ形。
#   git archive HEAD packages/doeff-jev-proxy | docker build --build-arg BASE=<業務の image> -t <名> \
#       -f packages/doeff-jev-proxy/deploy/Dockerfile -
# 焼いた image の中で検を撃つ(土台の doeff の版とこの source の組が合うことを配る前に確かめる):
#   docker run --rm --entrypoint pytest <名> -q -W ignore /opt/doeff-jev-proxy/tests
ARG BASE
FROM ${BASE}
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/proboscis/doeff" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.description="Jev の呼び出しを覚える代理(doeff-jev-proxy)"
USER 1000:1000
COPY --chown=1000:1000 packages/doeff-jev-proxy/src   /opt/doeff-jev-proxy/src
COPY --chown=1000:1000 packages/doeff-jev-proxy/tests /opt/doeff-jev-proxy/tests
ENV PYTHONPATH=/opt/doeff-jev-proxy/src:/opt/doeff-jev-proxy
WORKDIR /opt/doeff-jev-proxy
CMD ["hy", "-m", "doeff_jev_proxy.main"]
