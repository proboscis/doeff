# OOM の印の判定だけを替えた containerd の runc の shim(containerd-shim-runc-oomscope-v2)と、それを機体へ置く script の image。
# 訳と置き方は README.md。機体の containerd(k3s の v2.2.7-k3s1)と同じ source に oom-limit.patch を当てて組む。
ARG GO_VERSION=1.25.7
FROM golang:${GO_VERSION}-bookworm AS build
ARG CONTAINERD_REPO=https://github.com/k3s-io/containerd
ARG CONTAINERD_TAG=v2.2.7-k3s1
ARG CONTAINERD_COMMIT=ce1445e6f684b7ac1e215b2d4d2312adb34043a1
RUN git clone --branch "${CONTAINERD_TAG}" "${CONTAINERD_REPO}" /src && \
    test "$(git -C /src rev-parse HEAD)" = "${CONTAINERD_COMMIT}"
WORKDIR /src
COPY oom-limit.patch /oom-limit.patch
ENV GOFLAGS=-mod=vendor CGO_ENABLED=0
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    git apply /oom-limit.patch && \
    go test ./cmd/containerd-shim-runc-v2/runc/ -run LimitOOM && \
    go build -trimpath -o /out/containerd-shim-runc-oomscope-v2 \
      -ldflags "-s -w -X github.com/containerd/containerd/v2/version.Version=${CONTAINERD_TAG}+oomscope \
        -X github.com/containerd/containerd/v2/version.Revision=${CONTAINERD_COMMIT} -extldflags -static" \
      ./cmd/containerd-shim-runc-v2

FROM busybox:1.37
COPY --from=build /out/containerd-shim-runc-oomscope-v2 /shim/containerd-shim-runc-oomscope-v2
COPY install.sh runtime.toml /shim/
ENTRYPOINT ["/bin/sh", "/shim/install.sh"]
