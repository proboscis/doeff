# OOM の印を container か Pod の上限の OOM にだけ付ける runc の shim(oom-scope-shim)

## 何を直すか

worker は container の cgroup(scope)の下に層を作り、限る層の `memory.max` で kernel に OOM を起こさせる(`../k8s/cgroup-layers`)。
containerd の runc の shim(v2.2.7 の `cmd/containerd-shim-runc-v2/runc/container.go` の `OOMWatch`)は、scope の `memory.events` の
`oom_kill` が増えると TaskOOM を出す。`memory.events` は子の cgroup の数を足した値なので、層の上限で 1 本が殺されても TaskOOM が出て、
CRI は container の status の reason を `OOMKilled` にし、container が後で別の訳で終わってもその印を残す。後の追い出しや配備の止めも `OOMKilled` と出て、本物の container の OOM と見分けられなかった。

kernel は `oom` を上限に当たった cgroup に、`oom_kill` を殺した process の cgroup に数える(mm/memcontrol.c)。この shim は、`oom_kill`
が増えた時に scope とその祖先(Pod の slice・QoS の slice・kubepods.slice)の `memory.events.local` の `oom` の和が前の kill の時から
増えていれば TaskOOM を出し、増えていなければ(上限に当たったのが scope の下の層)出さずに log へ 1 行書く。直しは `oom-limit.patch`
(判定と、container の cgroup の dir を覚える所だけ)。

この判定が TaskOOM を出さない物:

- 層の上限での kill(直したい物)。
- 機体の全体の OOM(どの cgroup の上限にも当たらない — kernel はどこにも `oom` を数えない)。container は `Error` で終わる。
- Pod の slice の上限で同じ Pod の別の container が殺された後に、この container の層の上限で kill が起きた時は、slice の `oom` の増えが
  前の kill の時から残っているので TaskOOM を出す(誤って印が付く側)。slice の上限は container の上限の和なので、まず起きない。

## 部品

| 部品 | 場所 |
|---|---|
| 直し | `oom-limit.patch`(k3s-io/containerd `v2.2.7-k3s1` = `ce1445e6f684` に当てる) |
| image | `Dockerfile`(source を取って patch を当て、判定の test を通してから静的な shim を組む)・`install.sh`・`runtime.toml` |
| 機体へ置く係 | `../k8s/oom-scope-shim/`(DaemonSet `oom-scope-shim` — initContainer が `install.sh` を走らせる)と RuntimeClass `runc-oomscope` |
| 使う側 | worker の Deployment の `runtimeClassName: runc-oomscope`(使う機体の dir が足す) |

機体に置く物: `/opt/doeff-cluster/bin/containerd-shim-runc-oomscope-v2` と
`/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/runc-oomscope.toml`(k3s の containerd の設定が imports で読む dir)。

## 機体を足す手順

1. `../k8s/oom-scope-shim/installer.yaml` の nodeAffinity の名に機体を足して main に入れる(Flux が DaemonSet を当て、Pod が 2 つの file を置く)。
2. 機体で 1 度 k3s を再起動する(agent の機体は `systemctl restart k3s-agent`・server の機体は `systemctl restart k3s`)。containerd は設定を
   起動の時だけ読む。走っている container は shim ごと残る。
3. handler が読めたことを確かめる: 機体で `k3s crictl info | grep -A3 runc-oomscope`。
4. その機体の worker の dir に `runtimeClassName: runc-oomscope` を足して main に入れる。2 の前に足すと、Pod の sandbox が
   `no runtime for "runc-oomscope" is configured` で作れず worker が起きない。

shim だけを替える時(patch の直し・containerd の版上げ)は image の tag を替えて main に入れれば、DaemonSet の Pod が置き直す。
差し込みの中身を替えない限り k3s の再起動は要らない。

## image を組む

```sh
image-build build --repo-dir <worktree> --rev <commit> --context packages/doeff-cluster/deploy/oom-scope-shim \
  --dockerfile packages/doeff-cluster/deploy/oom-scope-shim/Dockerfile --image containerd-shim-runc-oomscope:<tag>
```
