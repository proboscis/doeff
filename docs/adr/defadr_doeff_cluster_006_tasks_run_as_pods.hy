;;; Executable ADR: a worker in a k8s cluster runs a task of a family the site lists as one Pod of its own on the worker's node, instead
;;; of a child process inside the worker Pod. The Pod is built from a template the site declares (one image for every family; the
;;; volumes that hold the worker's work root mounted at the same paths), its requests are the task's claim, and the shim inside it holds
;;; a connection to the worker process in place of the stdin pipe. Placement, claims and wait reasons do not change.
;;;
;;; Situation: after ADR-DOE-CLUSTER-005 the coordinator places tasks by declared sums inside compartments, but every task still runs
;;; inside the worker Pod of its machine, next to the CLIs and commands the system above runs and the resident services. The worker Pod's
;;; memory requests cover only its guarded compartments, so all memory tasks use is use above the worker Pod's requests — the first thing
;;; kubelet ranks when the node runs short, and kubelet evicts whole Pods. A heavy build therefore shares one eviction with every CLI and
;;; service of its machine.
;;;
;;; Decided 2026-11-12 (the second milestone of the work that moves tasks and turns into Pods): heavy tasks — pre-merge builds, image
;;; builds and daily verification — are started one per Pod by the doeff worker, from one common image that is not rebuilt per version,
;;; with their environment passed through mounts. Only the worker creates task Pods. This record fixes how the worker starts them, how
;;; they stop when their owner goes, how their requests, limits and priority classes are chosen, and how they are observed, so that the
;;; cards that implement it (the Pod host and the carrier choice; moving the first families) follow one design.
;;;
;;; Rollback: revert the commit that added this ADR. Until the Pod host lands no worker creates a task Pod; afterwards a family goes back
;;; to child processes by removing it from the template's family list (R1, R9).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])


;; Who sets each field of a task Pod (R3). The form is closed: doeff = the worker writes it over the template (a template that sets it is
;; refused); site = it comes from the template as written; a field in neither list must be absent from the template. Row: #(field owner what).
(val TASK-POD-FIELDS
  #(#("metadata.name" "doeff" "doeff-task- + the first 20 hex digits of sha256(worker Pod uid, process instance, job name) — fixed for the attempt (R7)")
    #("metadata.namespace" "doeff" "the worker's own namespace")
    #("metadata.labels doeff.dev/*" "doeff" "the worker Pod uid, the process instance, the job's subgroup name, the band (R5)")
    #("metadata.annotations doeff.dev/*" "doeff" "the job name and the attempt")
    #("metadata.ownerReferences" "doeff" "the worker Pod (uid from POD_UID; controller true, so a drain treats the Pod as managed; blockOwnerDeletion false) (R6)")
    #("spec.restartPolicy" "doeff" "Never — a second run of a task is the coordinator's next attempt, never a container restart")
    #("spec.affinity" "doeff" "a required node affinity whose matchFields metadata.name is the worker's node (R2)")
    #("spec.nodeName" "doeff" "left empty — the scheduler admits the Pod (R2)")
    #("spec.nodeSelector" "doeff" "left empty — the affinity alone pins the node")
    #("spec.priorityClassName" "doeff" "the class the template names for the task's band (R5)")
    #("spec.terminationGracePeriodSeconds" "doeff" "the shim's grace plus its cleanup margin (the shim spans the worker already derives)")
    #("spec.activeDeadlineSeconds" "doeff" "left empty — a task ends by itself or by the coordinator's stop")
    #("spec.resources" "doeff" "left empty — Pod-level resources would replace the claim of R4")
    #("spec.enableServiceLinks" "doeff" "false — the environment is the narrowed child environment of R3")
    #("spec.containers[0].name" "doeff" "task")
    #("spec.containers[0].command" "doeff" "the shim argv of the child path, with the owner connection in place of the stdin pipe and the log path (R3, R6)")
    #("spec.containers[0].args" "doeff" "left empty — the whole argv is in command")
    #("spec.containers[0].workingDir" "doeff" "the job's work dir, as for a child")
    #("spec.containers[0].env" "doeff" "the child environment of a runtime-env job (R3)")
    #("spec.containers[0].resources" "doeff" "requests = the claim, limits.memory = the tasks compartment, no CPU limit (R4)")
    #("spec.containers[0].image" "site" "the one image of every listed family")
    #("spec.containers[0].imagePullPolicy" "site" "the site's pull policy")
    #("spec.containers[0].volumeMounts" "site" "the work root at the worker Pod's mount path, the owner socket dir and every other path R3 lists")
    #("spec.containers[0].envFrom" "site" "extra variables the site's jobs read")
    #("spec.containers[0].securityContext" "site" "the worker's uid and gid, so the worker can remove what the job leaves (R3)")
    #("spec.volumes" "site" "the volumes holding the work root, the owner socket dir and the other paths of R3")
    #("spec.initContainers" "site" "helpers the site's mounts need")
    #("spec.serviceAccountName" "site" "the account task Pods run as — never the worker's own (R3)")
    #("spec.automountServiceAccountToken" "site" "whether that account's token is mounted")
    #("spec.securityContext" "site" "the site's Pod security settings")
    #("spec.tolerations" "site" "the taints of the worker's node the Pods must tolerate")
    #("spec.runtimeClassName" "site" "the site's runtime class")))

;; The bands of R5, lowest first. Row: #(band when). The site names one class per band in the template; every class has
;; preemptionPolicy Never and their values keep this order.
(val BANDS
  #(#("preemptible" "the task declares preemptible and its priority is below KEEP-NOW-PRIORITY")
    #("plain" "neither preemptible nor urgent")
    #("urgent" "priority >= KEEP-NOW-PRIORITY, preemptible or not")))

;; The mechanisms of R6.
(val OWNER-MECHANISMS (frozenset #("owner-connection" "owner-reference" "sweep" "unchanged")))

;; Every way the owner of a task Pod goes, or stops a Pod whose shim has not connected yet, what stops the Pod, and how long its job may
;; run on (R6). Row: #(case mechanisms bound).
(val OWNER-GONE
  #(#("the worker process ends: crash, kill -9, the worker container killed for memory, exit" #("owner-connection")
      "the shim sees end of file on the connection and stops the job group with its grace and cleanup — the bound the stdin pipe gives today")
    #("the worker Pod object is deleted: roll-out, eviction, drain, by hand" #("owner-connection" "owner-reference")
      "the worker process ends first (the row above); the garbage collector then deletes the task Pod objects that name the worker Pod")
    #("the worker process ends before the task Pod's shim has connected" #("owner-connection" "sweep")
      "the shim cannot connect, or the worker does not know its instance, and exits without starting the job; the next sweep of the same worker Pod or the garbage collector removes the object")
    #("the worker stops a task whose Pod's shim has not connected yet (a coordinator stop, a lapsed start) while the API server does not answer" #("owner-connection" "sweep")
      "the worker answers the shim's greeting with stop, so the job never starts; the object is deleted when the API server answers")
    #("the API server does not answer" #("unchanged")
      "nothing stops: the connection does not pass through the API server; new Pod starts wait in their creating state (R7) until it answers")
    #("the worker process hangs without ending" #("unchanged")
      "as today: the connection stays open as the pipe did, and the coordinator's cut-off rules decide")
    #("the machine is lost" #("unchanged")
      "as today: the coordinator cuts the worker off and re-places; k8s removes the Pods of the lost node")))


(defadr ADR-DOE-CLUSTER-006
  :title "A worker in a k8s cluster runs a task whose family the site lists in its task Pod template as one Pod of its own on the worker's node, unless the task is ungated or borrowed: the carrier is chosen by the worker's decision and carried by StartJob, a Pod host stacked inside the process host answers StartJob and the other job effects for Pod tasks and calls the API outside the round, the Pod is the site's template (one image, the work root mounted at the same paths, a closed set of fields) with the fields doeff owns written over it, requests equal the claim, the memory limit is the tasks compartment, the priority class is one of three non-preempting bands, the shim in the Pod holds a connection to the worker process in place of the stdin pipe and stops the job when it closes, an ownerReference to the worker Pod lets k8s remove the Pods with it, and placement, claims and wait reasons stay as in ADR-DOE-CLUSTER-005"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/_worker/entry/main.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/process_host.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/launch.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/program.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/intent/worker_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/entry/shim.py"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/memory_watch.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/eviction_rules.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/coordinator_link.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/pod_labels.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/replies.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/deploy/boot.sh"
          "docs/adr/defadr_doeff_cluster_005_machine_compartments_and_declared_sums.hy"
          "docs/adr/defadr_doeff_cluster_006_tasks_run_as_pods.hy"]
  :problem
    [(fact
       "Every task runs as a child process of its worker: StartJob starts the shim in a process group of its own (or forks from a warm child), inside the worker Pod's jobs layer. The worker Pod's memory requests are the sum of its guarded compartments (ADR-DOE-CLUSTER-005 R3) and the tasks compartment is not guarded, so all task memory is use above the worker Pod's requests. kubelet's node-pressure eviction ranks Pods by use above requests first and evicts a whole Pod: the CLIs and commands of the system above, the resident services and the tasks of one machine share one eviction."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/process_host.hy (start-job: StartProcess with process-group, hold-stdin, reap-group; ForkFromWarm); ADR-DOE-CLUSTER-005 R3; the k8s node-pressure eviction order")
     (fact
       "The stdin pipe is the only owner link: the worker holds the shim's stdin; when the worker process ends (kill -9 included) the pipe closes and the shim stops the job group with its grace and cleanup. Tasks never outlive the worker process — only handoff services that declare a retired lifetime do (outlives-worker). The same pipe carries the retirement notices (RetireJob, NoticeJob). The shim's stdout and stderr are the job's log file, opened by the worker."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/entry/shim.py (paths a to c of the header); packages/doeff-cluster/src/doeff_cluster/_worker/core/launch.hy (outlives-worker, orphan-seconds-of); process_host.hy (job-log-stem, StartProcess stdout-path)")
     (fact
       "The worker already talks to the API server with its in-cluster account (the service account token and CA of the Pod, the API at kubernetes.default.svc): it lists the Pods of its node to find its own Pod by POD_UID and patches its labels, reads its node and reads ConfigMaps and Secrets. It knows its node from NODE_NAME and its Pod uid from POD_UID; it has no POD_NAME. Its handlers are one list in entry/main.hy, fixed at start, whose innermost part is probe-host, warm-host, process-host, leaving-observer, memory-observer, local-host and tick-pauses; the API client that checks the cluster CA is in the list only on an in-cluster worker."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/pod_labels.hy; packages/doeff-cluster/src/doeff_cluster/_shared/protocol/supplied_values.hy; packages/doeff-cluster/src/doeff_cluster/_worker/entry/main.hy (production-handlers)")
     (fact
       "Per-task memory is measured as the RSS sum of the task's process tree read from /proc (the memory watch walks the roots process-host answers to ObserveJobRoots); the heartbeat carries it as taskMemoryMiB and the coordinator keeps the peak as the row's peak-mib, from which claims are derived (005 R4). The stop of 005 R7 is not wired: the worker's over-claim stop acts only when its keep room breaks, and today the sum of child tasks is bounded by the kernel at the jobs layer's memory.max."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/memory_watch.hy (memory-reading, over-need-stopped); packages/doeff-cluster/src/doeff_cluster/_worker/core/memory_rules.hy; packages/doeff-cluster/src/doeff_cluster/_worker/protocol/eviction_line.hy")
     (fact
       "The offered task row of the heartbeat reply carries memoryMiB, priority and preemptible but not borrowed, family or ungated. The worker reads borrowed from that row for the jobs layer's widening of 005 R13, so the widening never sees a borrowed task in production; only tests build rows that carry it. A JobNotStarted answer is counted by the worker's decision as a failure with a backoff of at most 60 s and no end. The daily verification is submitted without a shape or a family."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/replies.hy (task-offer-json; task-summary carries borrowed for GET /state only); packages/doeff-cluster/src/doeff_cluster/_worker/core/eviction_rules.hy (task-needs-of); packages/doeff-cluster/src/doeff_cluster/_worker/core/policy.hy (the not-started record); the sender of the daily verification in the repository of the system above, read 2026-11-15")
     (fact
       "Start times read on the site's cluster: 14 Pods with a volume of the site's shared storage went from scheduled to Ready in a median of 17.5 s (at most 46 s), 480 Pods without such a volume in a median of 4 s, and the worker Pod of the largest machine in 79 s. A warm fork starts a task in well under a second. The largest machine used 107 of its 110 Pod slots. The cluster runs k8s 1.34 with in-place Pod resize and Pod-level resources on and image volumes off; the shared storage's CSI driver offers persistent volumes only, not inline ephemeral volumes. The worker Pod's priority class does not preempt."
       :evidence "One read of the Pods' PodScheduled and Ready conditions, the node Pod counts and the API server's feature metrics, 2026-11-12; kubectl get csidrivers and priorityclass, 2026-11-15")
     (fact
       "Of 6 CI systems that run one job per Pod, the four whose Pods carry an ownerReference to a job or run object (Actions Runner Controller, Tekton, Argo Workflows, Buildkite) leave no Pods behind when their owner goes; the two without one (GitLab Runner, the Jenkins Kubernetes plugin) needed a separate cleanup. Remote-execution systems learn that a worker died from a lease or a heartbeat (10 s, 1 min). None of the 6 has a default memory request per Pod; each job states its own."
       :evidence "The comparison of 17 systems that run one unit of work per Pod, read from their documentation and sources on 2026-11-12, recorded in the design material of that day")]
  :context
    [(interpretation
       "Where the Pod runs. Taken: on the worker's own node, through a required node affinity on the node's name and the scheduler. The coordinator's sums inside the worker's tasks compartment stay the only placement decision; k8s enforces each task's requests and limits on that node; the mounts stay node-local, so the volume that holds the worker's work root (a ReadWriteOnce volume can be shared by the Pods of one node) serves the task Pod too. The two decisions agree only while the node's allocatable holds the tasks compartment beside every other request, so the site's declaration test checks that sum (R8). Not taken: letting the scheduler choose the node (two placement decisions that disagree, and waits that move out of the coordinator's wait reasons into Pending Pods), and spec.nodeName (it skips the scheduler, so a Pod that does not fit fails at kubelet instead of waiting).")
     (interpretation
       "One compartment, two carriers. A Pod task's claim counts in the same tasks compartment as a child task's, so placement, the claims table and the wait reasons of ADR-DOE-CLUSTER-005 are unchanged and a family can move between carriers without changing which sum it is counted in. The kernel no longer bounds the sum of both carriers (no cgroup parent holds both the jobs layer and the task Pods, and each Pod's limit is the whole compartment), so the stop of 005 R7 over both carriers is what bounds them, and it must be wired before a family is listed (R4, R9). kubelet's eviction is not a fallback that ranks tasks first: the worker Pod is always above its requests (its unguarded layers), so kubelet would evict it before the task Pods that stay within theirs. Not taken: a new compartment use for task Pods (one planned share becomes two numbers the site must keep in step), and lowering the jobs layer's memory.max by the claims of running Pods every round (the recomputation 005 removed).")
     (interpretation
       "The owner link. Taken: the shim inside the Pod connects to a socket of the worker process that created it, is told to run or to stop, and treats the connection as it treats the stdin pipe today — end of file means the owner is gone; the same connection carries the worker's stop and notice lines and the shim's usage and exit lines; plus an ownerReference to the worker Pod, so that k8s deletes the task Pods when the worker Pod object goes; plus a sweep of objects whose shim never ran. The socket lies on a node-local file system both Pods mount. Not taken: a k8s Lease renewed by the worker (only a reader with API access can act on its expiry: the task Pod would need API rights, or a second actor would stop Pods; and an API server that does not answer for minutes — which has happened on this cluster — would stop every running task); attaching to the container's stdin through the API (the stream breaks with the API server in the same way); TCP to the worker Pod's address (reachable from every Pod of the cluster, so the lines would need an authentication of their own); an ownerReference alone (a worker process that restarts inside the same Pod leaves its tasks running while the coordinator re-places them, so two runs of one attempt overlap); wrapping each task in a k8s Job (the Job controller, not the worker, would create the Pod, and its retry and deadline rules duplicate the coordinator's attempts).")
     (interpretation
       "Requests, limits and classes. The claim of 005 R4 is already a fixed, measured declaration per task, which is what a Pod's requests are; the CPU claim is TASK-CORES. The memory limit is the tasks compartment, the most one child task can use under the jobs layer's memory.max today; a limit equal to the claim would kill tasks at the peaks the claim's margin does not cover. No CPU limit (005 R3). The QoS class is Burstable. k8s preemption is not used by tasks: it stops any lower-priority Pod of the node, not only tasks marked preemptible, and the coordinator is the only place that stops and requeues tasks (005 R6, R13). The task classes sit above priority 0, so a Pending Pod without a class cannot preempt a task Pod; only the infrastructure classes above them can. Must-not-wait work (ungated tasks) stays a child process: 005 R6 places it over the sum when nothing fits, which a Pod whose requests do not fit cannot do without waiting. Borrowed tasks (005 R13) stay child processes: the lent room is inside the worker Pod's own limit and requests, not on the node.")
     (interpretation
       "What the claims learn from. A container cgroup's memory.peak and working set include page cache, and with a limit as large as the compartment a build that reads many files keeps that cache up to the limit; claims learned from them would climb toward the compartment and bring the waits back. The learned use of a Pod run is therefore its non-reclaimable memory, anon plus shmem of the container cgroup (RAM disks are shmem), sampled — the nearest cgroup measure to the RSS sum of child runs; the working set and memory.peak are reported for reading only.")
     (interpretation
       "Image and environment. Every path the worker hands a child today (the interpreter and the shim, the code tree, the runtime-env root, the Program file, the work dir, the log, the result file, the held copies of supplied values) lies under the worker's work root, and the few that may lie elsewhere (the jobs root's link target, TMPDIR, HOME, the cache and build dirs the allowed environment names) are mounted at the same paths too; a task Pod then runs the same argv and cwd as the child would. A new doeff version reaches task Pods through the trees and roots under the work root, exactly as it reaches child processes, and the one image is never rebuilt for a version. Not taken: an image per version (an image build on every landing, against fast roll-out without image builds), and image volumes (off on the cluster). Content-keyed mounts per Pod, a later milestone, change only the site's template, not doeff. The Pod's environment is the narrowed child environment of a runtime-env job even for a tree job, whose child gets the worker's whole environment: a Pod must not inherit the worker's environment, which carries the worker's API access.")
     (interpretation
       "Which tasks. A Pod start costs about 17.5 s on the site's shared storage against well under a second for a warm fork, so only heavy families gain: pre-merge builds, image builds and daily verification first. The family is the key because it is the kind the site names for tasks of about the same memory (005 R4); matching the site's list against it does not read meaning into the opaque word, and a family needs its sender to name it. The list belongs to each worker's template, because whether a machine can host task Pods is a property of that machine: native workers have no template and keep child processes. A kind the system above moves to a run path of its own is simply not listed.")
     (interpretation
       "Data. No stored table gains a field. The offered task row of the heartbeat reply gains family, ungated and borrowed, projections of facts the coordinator keeps (the shape-to-family relation, TaskRecord.ungated, TaskRecord.borrowed), as 005 R2 added the held compartments to the reply; borrowed also gives 005 R13's widening the field it reads. They reach the worker's decision as observations of the offered rows, beside the task needs, not in JobSpec, whose compared fields would stop a running task when its claim is lowered. StartJob, an action record that is never stored, gains the Pod plan. The task Pod template is a new site object; the Pod host's table is session state of the worker process, as process-host's table is.")
     (interpretation
       "This record replaces the mapping to Pods that the last interpretations of ADR-DOE-CLUSTER-005 sketched where the two differ: tasks do not preempt through k8s in any band (005 sketched preempting classes for ungated and urgent tasks), ungated tasks stay child processes, and the memory limit of a task Pod is the tasks compartment (005 sketched no limit per task). 005 R12 points here.")]
  :decision
    [(rule R1 "Carrier: a task runs as a Pod exactly when the worker holds a task Pod template (R3), the task's family is in the template's family list, the task is neither ungated nor borrowed (005 R6, R13), and its spec is once; otherwise it runs as a child process, as today. The coordinator puts family, ungated and borrowed in the offered task row (projections, R9); the worker's decision reads them as observations of the offered rows, not from JobSpec. The decision (worker/core — a pure judgment next to job-launch) makes the choice and puts it in StartJob as a Pod plan (requests, limit, band) or none; the handlers never choose and never fall back from one carrier to the other. A refused or lapsed Pod start is retried as a Pod start (R7).")
     (rule R2 "Handlers: a new handler pod-host (worker/protocol/pod_host) sits in the innermost part of entry/main.hy's handler list immediately inside process-host on every in-cluster worker (POD_UID and its node — the same condition as the API client that checks the cluster CA), whether or not it holds a template, so that it keeps answering for its running Pods after a template is refused. It answers StartJob that carries a Pod plan, and SignalJob, ReapJob, RetireJob and NoticeJob for the names in its own table; it performs every other one of these effects again outward, unchanged, so process-host answers it. ObserveProcesses and WorkerWakes: it performs them outward and adds its own views and wakes (the owner connections and the start deadlines). ObserveJobRoots passes through: a Pod task is not a process root. The memory watch reads the Pod tasks' measured uses from pod-host through a new observation and keeps one per-task use map. Every API call (create, get, list, delete, resize) runs outside the round, in tasks pod-host spawns as process-host spawns the clearing of set-aside dirs; the round only reads their outcomes, so an API server that does not answer never holds a round. pod-host takes argv and cwd from the same job-launch as the shim path. A Pod task's view carries a handle in place of a pid: POD-HANDLE-BASE (2^32) plus a serial of the worker process — above every OS pid; the process effects already answer an unknown pid as not their child, and an OS signal given a handle fails instead of signalling a process. The handle shows as the pid of the task's status row. The Pod is pinned to the worker's node by a required node affinity on the node's name (matchFields metadata.name), never by spec.nodeName.")
     (rule R3 "Template and Pod spec: the site declares, per worker, one k8s PodTemplate (core/v1) in the worker's namespace, named by the worker start argument --task-pod-template (boot.sh reads it from the key WORKER_TASK_POD_TEMPLATE). Its annotations carry doeff.dev/task-families (the family words this worker runs as Pods, comma separated), doeff.dev/task-classes (preemptible=, plain=, urgent= the class names of R5) and doeff.dev/task-start-seconds (R7). The worker reads it through the API at start and when its resourceVersion changes, and holds the last good one; an unreadable template keeps the held one. The form is closed (TASK-POD-FIELDS): the worker writes the fields marked doeff over the template's spec and keeps those marked site; a template that sets a doeff field or any field in neither list, has other than one container, lacks an annotation, or names the worker's own service account is refused by name, and the worker holds no template (later tasks run as child processes; the refusal is logged and shown in its status). The template mounts, at the same paths as in the worker Pod, the volumes that hold the worker's work root and every other path the worker hands a child (the jobs root's link target, TMPDIR, HOME, the cache and build dirs the allowed environment names), and the owner socket dir (R6) on a node-local file system both Pods see, at a path of at most 107 bytes. The container runs as the worker's uid and gid. The image is one for every listed family and provides only what lies outside those mounts; doeff never builds an image for a version. The container's env is the child environment of a runtime-env job (launch.child-environment: the allowed names of the worker's environment, the worker context, the declared environ, the DOEFF_ names the worker builds) for every Pod task, tree jobs included; a job that needs more of the worker's environment is not listed. The shim writes the job's output to the same log file as on the child path (an argument names it); the container's own output carries only the shim's lines. The worker's own token is never mounted into a task Pod.")
     (rule R4 "Requests and limits: requests.memory = the memoryMiB of the task's row in the heartbeat reply (the claim of 005 R4, fixed when placed), requests.cpu = TASK-CORES whole cores (the CPU claim), limits.memory = the memoryMiB of the worker's tasks compartment (005 R1, R2), no CPU limit. When the claim of a running Pod task changes (the lowering after placement of 005 R4 and its return), the worker writes the new claim to requests.memory through the Pod's resize subresource and never changes limits; a refused or deferred resize keeps the allocated requests and is logged. The claims of Pod tasks count in the same tasks compartment sum as child tasks (005 R5): placement does not change. The stop of 005 R7 runs over both carriers: the worker adds the measured uses of its Pod tasks (R7 of this record) to the jobs layer's working set against the tasks compartment's memoryMiB, chooses among child and Pod tasks in R7's order, stops a Pod task by the kill line on its owner connection, and deletes its Pod object when the connection is already gone. No family is listed until that stop is wired (R9). kubelet's node-pressure eviction stays below it as the site's last resort.")
     (rule R5 "Priority classes: the band is urgent when the task's priority is >= KEEP-NOW-PRIORITY, else preemptible when the task declares preemptible, else plain (BANDS in this file). The site declares the three classes and names them in the template; each has preemptionPolicy Never, their values order preemptible < plain < urgent, and all three lie above 0 (the priority of Pods without a class, which therefore cannot preempt a task Pod) and below the classes of the Pods tasks give way to (the worker Pod, the storage, the coordinator). Tasks never preempt through k8s; an urgent task stops preemptible lower-priority tasks through the coordinator, as 005 R6 says. A task Pod ended by the infrastructure (the scheduler's preemption, kubelet's eviction, a drain) is a stop the worker did not ask: the task returns to the queue as its next attempt when it declares rerun and otherwise ends naming the cause, as a task of a lost worker does today.")
     (rule R6 "Owner: OWNER-GONE in this file lists every way the owner goes and what stops the Pod. 1. The owner connection: each worker process listens on its own socket in the owner socket dir; the Pod's shim (shim.py with an owner socket and its instance in place of the held stdin) connects first and names its instance, and starts the job only when the worker answers run. The worker answers stop to an instance it does not know or whose stop has begun (a stop asked before the shim connected therefore always wins, also while the API server does not answer), and holds an accepted connection as it held the pipe: worker to shim lines are the stop lines (term, kill) and the retirement notices of RetireJob and NoticeJob; shim to worker lines are usage samples and the exit. End of file on the shim's side is the owner's end and takes the shim's path for a vanished worker (TERM to the group, the grace, the cleanup, exit). A shim that cannot connect, or is answered stop, exits without starting the job. 2. The ownerReference to the worker Pod: when the worker Pod object goes, the garbage collector deletes its task Pods. 3. The sweep: at start and every SWEEP-MS the worker deletes the Pods carrying its Pod uid that have ended, or that are not running past their start deadline, whichever process of that Pod created them; it never deletes a running Pod (a running Pod has a live owner or is already stopping itself). The boot does not remove a previous worker process's generation dir while a task Pod of that process still runs (it waits at most the shim's grace and cleanup). No Lease and no API read from inside a task Pod: a task Pod keeps running while the API server does not answer. A task never outlives the worker process that started it, as today; the worker's limit on how long a stopped job may take (the stop grace of the child path) gains the start seconds for a Pod task.")
     (rule R7 "Start, refusal, observation: a StartJob with a Pod plan is answered at once with JobStarted and the handle; the view starts at that time in its creating state while the create call runs outside the round. The Pod's name is fixed for the attempt (TASK-POD-FIELDS) and pod-host keeps it until the create's outcome is known: a create whose answer is lost (no answer, a timeout, 5xx) is resolved by reading the Pod by that name when the API server answers, AlreadyExists is read as created, and no second create is sent for the attempt while it is unresolved. A create that is definitely refused (4xx, 429), a Pod read as absent, and a Pod whose shim has not connected within the template's start seconds (deleted, its scheduling or container reason — Unschedulable, too many Pods, an image or mount failure — read once) are reported to the decision at the next round as a refused start (JobNotStarted — the existing not-started record and its backoff), never as an exit; the attempt does not end. After START-REFUSALS refused starts in a row whose reason lies in the node, the worker gives the task back: its status row says the start was refused with the reason, and the coordinator queues it as its next attempt without counting a loss (the requeue of 005 R13), so that it can be placed elsewhere. A start that waits on an API server that does not answer is kept, not given back. The exit code comes from the shim's exit line; a connection that closes without one is an exit -1 whose cause (OOMKilled, Evicted, preempted, deleted) is read once from the Pod's status, and lost when that cannot be read, as for a lost warm fork. Usage lines every USAGE-SECONDS carry, from the container's cgroup, anon plus shmem (the learned use: it enters the per-task use the heartbeat reports, taskMemoryMiB and so peak-mib, and the stop of R4), the working set (memory.current minus inactive_file) and memory.peak (both for reading). ReapJob deletes the Pod object; a failed delete is left to the sweep. Each Pod start writes one log line, worker: task-pod-start, with created-ms, scheduled-ms, running-ms, connected-ms, the node, the band and the requests, from which the Pod start seconds are read.")
     (rule R8 "Only the worker creates task Pods, and only for its own node; the coordinator creates none. The worker's account needs create, get, list, delete and the resize subresource on Pods and get on PodTemplates in its namespace; the account of the task Pods needs none of these. Because every child of the worker can read the worker's token, the site binds to the worker's account an admission policy that admits only Pods of R3's form (the template's account, image and volumes, no host namespaces and no privileged container, the node affinity naming the node of the requesting Pod, the owner label equal to the requesting Pod's uid) and lets it delete only Pods carrying that uid. The site's declaration test checks, for every worker it declares: the template against R3 (the closed fields, the one container, the annotations, the account, the mounts, a socket connection between two Pods through the owner socket dir); that the node's allocatable holds the requests of every other Pod of the node, the worker Pod's requests for each of its generations that can run at once, and the tasks compartment's memoryMiB and cpuMillis; and that the network rules let task Pods reach the coordinator as the worker's children do. The number of task Pods a node can hold against its Pod limit is decided apart; until then a Pod refused for it lapses and is retried by R7.")
     (rule R9 "What does not change, and the order: native workers, resident services, warm forks, ungated and borrowed tasks and every task of an unlisted family keep their child processes; the coordinator's placement, claims and wait reasons are those of ADR-DOE-CLUSTER-005. No stored table gains a field: family, ungated and borrowed in the offered task row are projections, the Pod plan lives on StartJob, the template is a new site object. A family is listed only after the Pod host, the projections and the stop over both carriers (R4) are wired and its sender names it; it goes back to child processes when it is removed from the template's list: its later tasks start as children and its running Pods finish.")]
  :laws
    [(law one-attempt-has-at-most-one-pod
       :statement "for_all task t and attempt a started with a Pod plan: |{Pod p : instance(p) = instance(t, a)}| <= 1 ∧ name(p) is a function of (worker Pod uid, instance, job name) ∧ no create for (t, a) is sent while an earlier create for (t, a) is unresolved"
       :counterexamples
         [(counterexample "A random suffix in the Pod name — a create whose answer is lost is repeated and two Pods run the same attempt")
          (counterexample "Answering a lost create as a refused start — the next round starts the attempt again under a new instance while the first Pod may exist")
          (counterexample "Reading AlreadyExists as a refusal — the decision starts the task again while the first Pod runs")]
       :enforced-by ["planned with the Pod host: a test that loses the create answer once, keeps the API silent for some rounds, and expects one Pod object and one view"]
       :wiring "Unwired (2026-11-15) — wired with the Pod host")
     (law a-task-pod-never-outlives-its-owner-process
       :statement "for_all task Pod p created by worker process w: w ends ⇒ the shim of p receives end of file on its owner connection, or cannot connect, and p's job ends within the shim's grace and cleanup ∧ w began to stop the task before p's shim connected ⇒ the job of p never starts ∧ both hold whether the API server answers or not"
       :counterexamples
         [(counterexample "An ownerReference alone — a worker process restarted inside the same Pod leaves the task running while the coordinator re-places it, and two runs of one attempt write the same result")
          (counterexample "A Lease read from the task Pod — an API server silent for minutes stops every running task")
          (counterexample "The container's stdin attached through the API — the stream breaks with the API server in the same way")
          (counterexample "Starting the job when the shim connects — a Pod the worker could not delete while the API server was silent starts the attempt the coordinator has already placed elsewhere")]
       :enforced-by ["planned with the Pod host: a test that ends the worker process of a simulated Pod and expects TERM to the job group and the Pod's end with the API answering and not answering, and that a stop asked before the greeting is answered stop"]
       :wiring "Unwired (2026-11-15) — wired with the Pod host")
     (law the-carrier-is-decided-not-fallen-into
       :statement "for_all StartJob s: s carries a Pod plan ⇔ the judgment of R1 holds for its task ∧ s carries a Pod plan ⇒ no child process is started for s ∧ s carries none ⇒ no Pod is created for s"
       :counterexamples
         [(counterexample "Starting a child process when the API server refuses the Pod — the task's memory moves back into the worker Pod unannounced and is enforced by the other carrier's limit")
          (counterexample "pod-host deciding the carrier from the template it holds at answer time — two rounds of one task choose differently when the template changes")]
       :enforced-by ["planned with the Pod host: a test of the judgment over the template's list, ungated, borrowed and a worker without a template, and a test that a refused create is reported as a refused start without a StartProcess"]
       :wiring "Unwired (2026-11-15) — wired with the Pod host")
     (law must-not-wait-work-stays-a-child
       :statement "for_all task t: ungated(t) ∨ borrowed(t) ⇒ the carrier of t is a child process, whatever the template lists"
       :counterexamples
         [(counterexample "An ungated deploy task as a Pod — it is Pending while the node's requests are full, the wait ADR-DOE-CLUSTER-005 removed")
          (counterexample "A borrowed task as a Pod — the lent room is inside the worker Pod's requests, so the Pod's requests are counted twice on the node")
          (counterexample "Leaving borrowed out of the offered row — the worker cannot tell a borrowed task and runs it as a Pod")]
       :enforced-by ["planned with the Pod host: the carrier judgment test above, fed from offered rows built by the coordinator's own task-offer-json"]
       :wiring "Unwired (2026-11-15) — wired with the Pod host")
     (law requests-are-the-claim
       :statement "for_all running task Pod p of task t: the allocated requests.memory of p = memoryMiB of t's heartbeat row, or a resize to it is pending or was refused and logged ∧ requests.cpu(p) = TASK-CORES ∧ limits.memory(p) = memoryMiB(tasks compartment of the worker) ∧ p has no CPU limit and no Pod-level resources"
       :counterexamples
         [(counterexample "limits.memory = the claim — a task is killed at a peak the jobs layer tolerated")
          (counterexample "requests = the sender's number — image builds reserve 6 times their use on the node")
          (counterexample "A CPU limit per task Pod — CPU contention becomes throttling the compartments do not declare")
          (counterexample "Learning claims from the working set or memory.peak — page cache kept up to the limit raises a family's claim toward the compartment")]
       :enforced-by ["planned with the Pod host: a test of the Pod spec built from a claim, a compartment and a changed claim"]
       :wiring "Unwired (2026-11-15) — wired with the Pod host")
     (law doeff-and-the-site-own-disjoint-fields
       :statement "the fields TASK-POD-FIELDS marks doeff and those it marks site are disjoint ∧ for_all template T: T sets a doeff field or a field in neither list ⇒ the worker refuses T by that field's name and holds no template"
       :counterexamples
         [(counterexample "A template that sets resources, Pod-level or per container — every task gets the template's requests, not its claim")
          (counterexample "A template that sets nodeName, nodeSelector or an affinity — task Pods leave the worker's node and the coordinator's sum no longer describes where they run")
          (counterexample "A template that sets restartPolicy OnFailure — kubelet reruns a failed task inside the attempt the coordinator counts once")
          (counterexample "An open form that passes unlisted fields — hostPID or a probe reaches every task Pod without a decision here")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_006_tasks_run_as_pods.hy::test-adr-doe-cluster-006-tables-are-whole"]
       :wiring "Wired (2026-11-15) for the table; the refusal of a template is planned with the Pod host")
     (law every-way-the-owner-goes-has-a-stop
       :statement "for_all row r of OWNER-GONE: mechanisms(r) ⊆ OWNER-MECHANISMS ∧ mechanisms(r) ≠ ∅ ∧ bound(r) ≠ ''"
       :counterexamples
         [(counterexample "Adding a new owner event (a worker that pauses, a Pod restarted by kubelet) without deciding here what stops its task Pods — leaked Pods are found one incident at a time")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_006_tasks_run_as_pods.hy::test-adr-doe-cluster-006-tables-are-whole"]
       :wiring "Wired (2026-11-15)")]
  :enforcement
    [(deftest test-adr-doe-cluster-006-tables-are-whole
       ;; Every Pod field has one owner, doeff or site, and is named once; the fields R2 to R6 rely on are doeff's.
       (val owners (frozenset (gfor row TASK-POD-FIELDS (get row 1))))
       (assert (<= owners (frozenset #("doeff" "site"))))
       (val names (tuple (gfor row TASK-POD-FIELDS (get row 0))))
       (assert (= (len names) (len (frozenset names))))
       (val doeff-fields (frozenset (gfor row TASK-POD-FIELDS :if (= (get row 1) "doeff") (get row 0))))
       (assert (<= (frozenset #("metadata.name" "metadata.ownerReferences" "spec.restartPolicy" "spec.affinity" "spec.nodeName"
                                "spec.nodeSelector" "spec.priorityClassName" "spec.resources" "spec.containers[0].command"
                                "spec.containers[0].args" "spec.containers[0].env" "spec.containers[0].resources"))
                   doeff-fields))
       (assert (all (gfor row TASK-POD-FIELDS (and (= (len row) 3) (all row)))))
       ;; Three bands in their order, each with its condition.
       (assert (= (tuple (gfor row BANDS (get row 0))) #("preemptible" "plain" "urgent")))
       (assert (all (gfor row BANDS (get row 1))))
       ;; Every owner event names at least one mechanism from the closed set and a bound, and is listed once.
       (assert (all (gfor row OWNER-GONE (and (= (len row) 3) (get row 0) (get row 1) (<= (frozenset (get row 1)) OWNER-MECHANISMS)
                                               (get row 2)))))
       (assert (= (len OWNER-GONE) (len (frozenset (gfor row OWNER-GONE (get row 0)))))))]
  :plans [])
