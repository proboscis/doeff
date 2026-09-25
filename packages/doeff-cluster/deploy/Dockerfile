# doeff-cluster の coordinator と worker の image。
# 土台(BASE)= 業務の image(doeff の venv を持ち、その venv に doeff-cluster が入っている物・PATH に hy)。ここで足すのは、
# worker が業務のコードの版を取るための git と ssh と、起動の script だけ。
#   docker build --build-arg BASE=<業務の image> -t <名> packages/doeff-cluster/deploy
ARG BASE
FROM ${BASE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000
COPY --chown=1000:1000 boot.sh /opt/worker-boot.sh
CMD ["/opt/worker-boot.sh"]
