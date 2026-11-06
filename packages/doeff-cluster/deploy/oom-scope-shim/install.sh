#!/bin/sh
# 機体へ shim と containerd の差し込みを置く(DaemonSet oom-scope-shim の initContainer が走らせる — ../k8s/oom-scope-shim)。
# 置き先は host の dir を mount した所: /host/bin = 機体の /opt/doeff-cluster/bin・/host/containerd-conf = 機体の
# /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d。同じ中身なら何もしない。替える時は同じ dir の別の名へ書いてから rename する
# (走っている shim は前の inode のまま走り続け、containerd が次に起こす shim から新しい物になる)。containerd の再起動はしない —
# 差し込みは containerd の次の起動から効く(README.md)。
set -eu

bin=/host/bin/containerd-shim-runc-oomscope-v2
if cmp -s /shim/containerd-shim-runc-oomscope-v2 "$bin"; then
  echo "oom-scope-shim: $bin は同じ中身(置き直さない)"
else
  cp /shim/containerd-shim-runc-oomscope-v2 "$bin.new"
  chmod 0755 "$bin.new"
  mv -f "$bin.new" "$bin"
  echo "oom-scope-shim: $bin を置いた"
fi

conf=/host/containerd-conf/runc-oomscope.toml
if cmp -s /shim/runtime.toml "$conf"; then
  echo "oom-scope-shim: $conf は同じ中身(置き直さない)"
else
  cp /shim/runtime.toml "$conf.new"
  chmod 0644 "$conf.new"
  mv -f "$conf.new" "$conf"
  echo "oom-scope-shim: $conf を置いた — containerd の次の起動(k3s の再起動)から handler runc-oomscope が使える"
fi
