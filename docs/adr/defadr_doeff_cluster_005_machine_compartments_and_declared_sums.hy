;;; Executable ADR: a machine's memory and CPU are split by use into declared compartments (tasks, resident services, the worker itself,
;;; the version-preparation layer, layers hosted for the system above, and what lies outside doeff). Each compartment is enforced by its own
;;; cgroup layer, and the coordinator places a task when the declarations of the tasks already placed in a worker's tasks compartment, plus
;;; the new task's declaration, fit that compartment. Placement stops reading the machine's measured free room.
;;;
;;; Situation (one read of the coordinator state, 2026-11-12 16:48 JST): 337 tasks were queued, every one with the wait reason that no
;;; machine's measured room had space left, and each of the 7 in-cluster workers whose capabilities matched could take 0 more tasks. On the
;;; largest shared machine the memory was taken first by k8s Pods outside doeff, the CLIs and commands the system above runs in hosted layers
;;; (RAM disks included), and resident services; the tasks layer itself held about 3 GiB, and the measured room the coordinator saw was
;;; 0 GiB. Several mechanisms hand out the same memory independently and the coordinator can only measure what is left. The two placement
;;; files (cluster_policy.hy, measured_room.hy) received 39 fixes from 2026-11-01 to 2026-11-12, and each fix added a condition.
;;;
;;; Decided 2026-11-12 (the first milestone of the work that moves tasks and turns into Pods): first remove the measured-room
;;; conditions and place by declared sums inside compartments, then move heavy tasks, turns of the system above and resident jobs to Pods in
;;; later milestones. This record fixes the data shapes and the destination of every current waiting condition, so the cards that implement
;;; it (the declaration and the cgroups; declarations from history; the placement switch)
;;; follow one design.
;;;
;;; Rollback: revert the commit that added this ADR. The implementing cards are each reverted by their own commits. Until the placement
;;; switch lands, the coordinator still places by measured room and this record changes no behavior.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import doeff_cluster._coordinator.intent.cluster_model [TaskUnplacedKind])


;; The 3 destinations of a current condition (R10): remove = the condition goes and nothing takes its place; replace = a declared-sum or
;; declared-compartment condition takes its place; keep = it stays (recounted in declarations where it counted memory).
(val DESTINATIONS (frozenset #("remove" "replace" "keep")))

;; Every condition that today makes a task or a resident service wait, or refuses it, because of room — coordinator side, then worker side.
;; Row: #(condition introduced destination what-takes-its-place).
(val CONDITION-DESTINATIONS
  #(#("RESERVED: a negative-priority task does not use the half of the worker's reserve kept for priority >= 0" "2026-10-11" "remove"
      "nothing; background work is bounded by declared group limits (GroupLimit) and gives way through preemption (R6)")
    #("BACKGROUND-RESERVED: a task of priority >= 0 leaves the background half of the reserve while background work waits" "2026-10-29"
      "remove" "nothing; the same as RESERVED")
    #("UNMEASURED: a worker that reports no measured load takes no task" "2026-11-01" "replace"
      "TASK-NO-COMPARTMENT: a worker that holds no tasks compartment takes no gated task; ungated tasks still go there (R6, R8)")
    #("NO-ROOM: count room = min(free CPU, free memory / 1 GiB, CPU count); 0 when CPU pressure >= 40 % or memory pressure >= 10 %; the task-layer floor; tasks not yet seen by the measurement"
      "2026-10-05 (word), 2026-11-01 (meaning), 2026-11-05 (floor)" "replace"
      "the CPU sum: CPU declarations of placed tasks + this task <= the compartment's cpuMillis (TASK-COMPARTMENT-FULL, R5)")
    #("Floors: 1,536 MiB below the measured room and 512 MiB below the jobs-layer room" "2026-11-01, 2026-11-09" "remove"
      "nothing; the margin is in each declaration (R4) and the compartment is a fixed size")
    #("NO-MEMORY: measured room - floor - sum of (need - current use) of placed tasks < need" "2026-11-02, jobs-layer term 2026-11-09"
      "replace" "the memory sum: claims of placed tasks + this task's declaration <= the compartment's memoryMiB (TASK-COMPARTMENT-FULL, R5)")
    #("Refusal at submit: need > every eligible worker's measured line - floor" "2026-11-02" "replace"
      "refusal at submit: a declaration > the tasks compartment of every eligible worker that holds one; ungated tasks excepted (R8)")
    #("Order among workers that can take the task: smallest shortfall, then the prepared environment, then the largest measured room"
      "2026-09-26, 2026-11-11" "replace" "smallest shortfall, then the prepared environment, then the largest declared remainder (R5)")
    #("Learned need: the larger of the task's own peak and the shape's peak x 1.25 + 256 MiB once the shape has a finished row; before that the larger of the sender's number and those peaks"
      "2026-11-09, 2026-11-10, 2026-11-11" "keep" "it is the declaration (R4); the clamp changes from the measured line to the largest tasks compartment")
    #("Ungated: deploy tasks skip the measured gate and are bounded only by group limits" "2026-11-10, 2026-11-11" "keep"
      "it means never waits for room: fits, else preempts when urgent, else is placed over the sum (R6)")
    #("Preemption: a task of priority >= 1 that does not fit stops preemptible lower-priority tasks on one worker" "2026-11-11" "keep"
      "recounted in claims: a stop releases the stopped task's claim (R6)")
    #("Keep for a long-waiting task: after 30 min a task keeps its need on one worker" "2026-11-10" "keep"
      "recounted in claims; ungated tasks do not see keeps (R6)")
    #("Keep for an urgent task: a task of priority >= 1 keeps its need from its first tick" "2026-11-10" "keep"
      "recounted in claims; ungated tasks do not see keeps (R6)")
    #("Urgent room: priority >= 1 is counted against the room below the turn keep line" "2026-11-10" "remove"
      "nothing; the keep line itself is removed")
    #("Lending under the keep line and reclaiming the borrowed tasks" "2026-11-12" "remove"
      "nothing; the hosted layers hold their own compartments (R3)")
    #("Resident services: the sum of settled declarations + the new one <= the room file's memoryMiB" "2026-10-26" "keep"
      "the budget is the services compartment's memoryMiB (R9)")
    #("Resident services: none is placed while the worker reports a reserve for a waiting version preparation" "2026-11-10" "remove"
      "nothing; preparation has its own compartment")
    #("Worker: the keep line for turns of the system above narrows the reported room" "2026-11-08" "remove"
      "nothing; hosted compartments separate those turns from tasks")
    #("Worker: the jobs layer's memory.max is recomputed every round from the room to eviction, margins, the prepare reserve and the CLI loan"
      "2026-11-01 onward" "replace" "memory.max = the tasks compartment, written when the declaration changes (R3)")
    #("Worker: the CLI layer's ceiling, kept margin and turn room in one ledger with the jobs layer" "2026-11-09 onward" "replace"
      "memory.max of the hosted compartment of the CLI layer (R3)")
    #("Worker: the command layer's line and floor" "2026-11-09 onward" "replace" "memory.max of the hosted compartment of the command layer (R3)")
    #("Worker: the prepare reserve taken from the jobs layer, the reserve while preparation waits, and the keeper waiting for room to eviction"
      "2026-11-02, 2026-11-10" "replace" "the prepare compartment: the keeper starts when its own layer has room; nothing is taken from tasks (R3)")
    #("Worker: stop tasks over their need when the keep line breaks" "2026-11-10" "keep"
      "triggered by the tasks compartment nearing its memory.max, in kubelet's eviction order (R7)")))

;; The destination of every member of the closed wait-reason type TaskUnplacedKind (R8, R10) — new = added by the switch.
(val WAIT-REASON-DESTINATIONS
  {"TASK_NO_ROOM" "replace" "TASK_NO_MEMORY" "replace" "TASK_UNMEASURED" "replace"
   "TASK_RESERVED" "remove" "TASK_BACKGROUND_RESERVED" "remove"
   "TASK_LIMITED" "keep" "TASK_UNDECLARED" "keep"
   "TASK_NO_COMPARTMENT" "new" "TASK_COMPARTMENT_FULL" "new"})

;; The work that must not wait for room, and how it avoids the wait (R6, R11). Row: #(work its-form how-it-does-not-wait).
(val MUST-NOT-WAIT
  #(#("deploy tasks (the landed-declare task, the web app build)" "ungated and priority >= 1, declared by the sender today"
      "fits, else stops preemptible lower-priority tasks, else is placed over the sum; also on workers without a compartment (R6)")
    #("version roll-out commands" "ungated and priority 1, declared by the sender today" "the same as deploy tasks (R6)")
    #("conversation-opening steps of the system above" "priority 1 today; the sender adds ungated with the switch"
      "the same as deploy tasks once ungated (R6)")
    #("the top-priority turn of the system above" "not a doeff task; runs in a hosted compartment"
      "doeff places no room gate on turns; the host that admits turns starts the top priority without a room check (R11)")
    #("delivery of user messages" "a resident service in the services compartment"
      "placed once as a service, not per message; memory.min of the services compartment protects it (R3, R11)")))


(defadr ADR-DOE-CLUSTER-005
  :title "A machine's memory and CPU are split by use into compartments declared in one place (resource MachineCompartments: tasks, services, worker, prepare, hosted, outside) and enforced as cgroup limits; the coordinator places a task when the claims of the tasks placed in a worker's tasks compartment plus the new declaration fit it, with declarations derived from the measured peaks of the same shape and fixed at placement; measured room, pressure, floors and reserves leave placement; ungated tasks never wait for room"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/measured_room.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/resource_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/intent/cluster_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/eviction_rules.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/job_cgroup.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/load_rules.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/room_rules.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/memory_watch.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/eviction_line.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/coordinator_link.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/intent/compartment_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/core/compartment_rules.hy"
          "packages/doeff-cluster/tests/test_machine_compartments.hy"
          "packages/doeff-cluster/deploy/boot.sh"
          "docs/adr/defadr_doeff_cluster_005_machine_compartments_and_declared_sums.hy"]
  :problem
    [(fact
       "Every queued task waited on room: 337 tasks queued with the reason that no machine's measured room had space (the oldest command of the system above 3,549 minutes, pre-merge builds 135 tasks with the oldest at 891 minutes, conversation-opening steps 120 tasks with the oldest at 49 minutes). Each of the 7 in-cluster workers with matching capabilities could take 0 more tasks; only native workers without the needed capabilities had room."
       :evidence "One read of the coordinator's GET /state, 2026-11-12 16:48 JST (1,831 task rows), recorded in the design material of 2026-11-12")
     (fact
       "The room the coordinator measures is what other mechanisms leave. On the largest shared machine the measured uses were: k8s Pods outside doeff 40.6 GiB, CLIs of the system above 16.8 GiB, its commands 20.8 GiB of which RAM disks 13.7 GiB, resident services 6.9 GiB, the tasks layer 3.3 GiB, the worker 1.8 GiB, the machine's system and user slices 9.2 GiB. Tasks are counted by declaration; everything else enters only as measured use after the fact. The coordinator's memory room for that worker was 0 GiB while a native worker on the same machine measured 35 GiB. On a second machine 33 GiB was free, but CPU pressure of 51 % crossed the 40 % stop and the count room was 0."
       :evidence "cgroup memory.current and memory.stat, /proc/meminfo, kubectl top on the two machines, 2026-11-12 16:48 and 16:59 JST")
     (fact
       "Senders' declarations miss the measured peaks in both directions: image builds declare 4,096 MiB and peak at a median of 615 MiB (6x over); pre-merge builds declare 2,560 MiB and peak at a median of 1,214 and at most 1,952 MiB; commands of the system above default to 3,584 MiB and peak at a median of 683 MiB, but one peaked at 5,596 MiB (the declaration a third of the use); daily verification has rows at 1.35 times its declaration."
       :evidence "peak-mib of the task rows of one day, read with the state above")
     (fact
       "The waiting conditions grew one per incident: RESERVED (10-11), BACKGROUND-RESERVED (10-29), UNMEASURED and the count room with floors (11-01), NO-MEMORY and the refusal at submit (11-02), the task-layer floor (11-05), the jobs-layer term (11-09), learned needs, keeps for long-waiting and urgent tasks, urgent room and ungated (11-09 to 11-11), preemption (11-11), lending and reclaiming (11-12). On the worker side the jobs layer's memory.max, the CLI layer's ceiling, the command layer's line and the prepare reserve are recomputed every round from one shared ledger."
       :evidence "git log of cluster_policy.hy and measured_room.hy since 2026-11-01; the header comments of measured_room.hy, eviction_rules.hy and load_rules.hy")]
  :context
    [(interpretation
       "Six shapes were compared on the same items in the design material of 2026-11-12: c keep fixing the measured gate; f only count caps; e compartments and declared sums; b move heavy tasks to k8s Jobs or spot machines; d run tasks and turns as Pods from one image with the code mounted; a put everything, resident jobs included, on k8s. The order taken is e first and then d (milestones 2 to 5 of the same work). f was not taken: the tasks layer of the largest shared machine is about 4 GiB, so waits would turn into kills. c keeps the cause — a gate that reads what other mechanisms leave.")
     (interpretation
       "The compartment declaration is a new resource kind, not a field of an existing table. How a machine is split by use is one fact owned by whoever declares the machines; it is not a property of a System (a set of services), of a Worker row (reported by the worker), of the room file (whose rows already gained optional fields one incident at a time) or of WORKER_CGROUP_LAYOUT (cgroup knobs of one Pod template). The resident-service budget in the room file (memoryMiB) and its copy on the Worker row (memory-mib) are the same fact as the services compartment and move to the new kind.")
     (interpretation
       "A task's declaration is derived from facts already kept (the sender's estimate memory-mib and shape on the task row, the measured peak-mib of rows of the same shape) and is fixed once, when the task is placed, as a claim in a new table keyed by task id. Fixing it is what a Pod's requests do; a claim recomputed every tick would move under a running task (when old rows leave retention or a compartment changes) and make it suddenly over or under. The claim is not stored on the task row, which already holds the sender's number for the same quantity.")
     (interpretation
       "CPU is declared as a fixed TASK-CORES per task because no per-task CPU use is recorded; learning CPU needs a new measurement and its own record. The CPU sum replaces the count room that stopped on pressure: CPU contention slows tasks and is shared by cgroup weights, it does not kill them. A tasks compartment declared with fewer cores than the machine takes fewer tasks than the old cap of one per core; that is the site's choice in the declaration.")
     (interpretation
       "Each compartment maps to exactly one cgroup layer, because the worker's layers are flat siblings under its own cgroup and a running CLI or task cannot be moved under a new parent without restarting the Pod. The CLI layer and the command layer of the system above are therefore two hosted compartments: a command filling its RAM disk fills only the command layer and never the room left for new CLIs.")
     (interpretation
       "Placing an ungated task over the sum costs real memory once declarations follow the measured peaks. It is bounded: the coordinator first stops preemptible lower-priority tasks when the ungated task is urgent; the peaks of the placed tasks seldom coincide; R7 stops other tasks, over-declaration first, before the kernel picks a victim; and the overrun shows per worker in the compartment view. A wrong compartment size makes tasks wait or be stopped inside that compartment only; hosted layers and resident services are unaffected.")
     (interpretation
       "Mapping to Pods later (milestones 2 to 5). A task's claim becomes its Pod's resources.requests for memory and CPU, and its memory limit equals the tasks compartment's role in R7 (unset per task, bounded by the node). PriorityClasses keep the order the system needs: hosted turns and resident services in the highest classes; ungated tasks in the highest task class, below them, with preemptionPolicy PreemptLowerPriority; urgent tasks (priority >= KEEP-NOW-PRIORITY) in a preempting class below that; non-urgent tasks in classes with preemptionPolicy Never (they do not preempt); preemptible tasks in the lowest classes. k8s preempts any lower-priority Pod, not only those marked preemptible, so a non-preemptible task must sit in a class no preempting class is above — the doeff priority order among non-preemptible tasks collapses there. kubelet's eviction order under memory pressure (usage above requests first, then priority, then the amount above requests) is R7's order. A compartment becomes the sum of the requests of that use's Pods on the node. Steps without a k8s counterpart: R6's placement over the sum, the keeps for long-waiting and urgent tasks, and the doeff preemptible mark itself; they are dropped or rewritten for a task when it moves to a Pod, and the declared share and the classes must cover them by then. The declaration, the claim, the compartment sizes and the priority bands of this milestone carry over unchanged.")
     (interpretation
       "Amendment of R3 (2026-11-12, the work owner's decision on the question the site declaration raised). Writing the first site declaration showed two faults in the first R3. 1. A Pod worker's requests equal to the sum of all its compartments do not fit: three workers roll out with two generations of their Pod side by side, and on the largest shared machine (allocatable 118.5 GiB, other Pods requesting 33.7 GiB) two generations allow about 42 GiB each while the measured uses of its compartments sum to about 50 GiB; the small machines (allocatable 2.6 to 2.8 GiB) cannot hold the sum of their layers at all. 2. Two layers of work that must not wait — the CLIs of the system above and the commands of the merge gate — hold memory.min today, which the first R3 kept for services and worker only. Taken: requests = the guarded compartments' memoryMiB (and their cpuMillis), limits = all compartments' memoryLimitMiB, rolling kept; a hosted compartment may be guarded only with memoryLimitMiB above memoryMiB; where the guarded sum does not fit, the guarded memoryMiB is lowered, not the limits. Not taken: going back to Recreate for the rolling workers (every change of a declared value would stop the CLIs and resident jobs of the machine, against the rule that top-priority turns and message delivery never wait for memory), and shrinking the compartments to fit two generations (the CLIs and commands of the largest machine to about 30 GiB, which adds the waits this milestone removes). kubelet's eviction order for the worker Pod is the same as before the amendment. Per-Pod requests replace this when tasks move to Pods (R12).")]
  :decision
    [(rule R1 "Declaration: a new coordinator resource kind MachineCompartments holds how one machine's memory and CPU are split by use. One row per machine; the name is the machine's label chosen by the declaring site (the k8s node name for in-cluster machines, the host name for native ones). spec = {compartments: [{use, worker, layer, memoryMiB, memoryLimitMiB, cpuMillis}]}. use is one of tasks, services, worker, prepare, hosted, outside (closed): tasks = where the coordinator places tasks; services = where it places resident services; worker = the worker body and its init layer; prepare = the version-preparation layer; hosted = a layer the worker keeps for processes the system above runs (its CLIs, its commands); outside = the operating system, kubelet and every process or Pod doeff does not run. worker and layer name the doeff worker and its cgroup layer; outside names neither. memoryMiB is the amount the compartment is planned and protected for; memoryLimitMiB (only for services and worker, default memoryMiB, never below it) is the most it may use; cpuMillis is its CPU declaration. Checked at write: integers >= 0; at most one outside per machine; every other use names a worker and a layer; (worker, layer) appears once across all rows; a worker appears under one machine only and holds at most one tasks and one services compartment. The row is written through the resource API with resourceVersion like the other kinds, by the site's infrastructure declaration step. doeff carries the form only; the numbers live with the site. Native workers on macOS and Windows are declared in the same form. The sum against the machine's size is checked by the site's declaration test against the node or host it describes; the coordinator shows the sum next to the memory total its workers report (R2) and does not refuse on it.")
     (rule R2 "Delivery and view: the coordinator puts in each heartbeat reply the compartments the worker holds (compartments: [{use, layer, memoryMiB, memoryLimitMiB, cpuMillis}] — a projection of R1, not a new fact). An empty list means no row names the worker: it holds none, the coordinator places only ungated tasks and no resident service there, and the worker writes no new limit (values already in the kernel stay until a non-empty list). The field is left out only for a worker that has not named itself or a retired generation. GET /state gains a compartment view per worker: the declared compartments, the sums of the claims of its placed tasks (memory and CPU), the part placed over the sum by R6, the working set of each layer, the layers of the worker that no compartment names, and whether the worker enforces them (R3). The view is a new record computed from R1, the claims and the measured load; it adds no field to WorkerInfo or TaskRecord.")
     (rule R3 "cgroups: a worker writes each held compartment to its layer when the value changes and does not recompute it from measured room. Every enforced compartment: memory.max = memoryLimitMiB (= memoryMiB unless services, worker or a guarded hosted compartment set more). A guarded compartment also gets memory.min = memoryMiB: services and worker always, and a hosted compartment when it writes memoryLimitMiB greater than memoryMiB — allowed only for the layers of work that must not wait (the CLIs of the system above and the commands of the merge gate), and never without that higher limit. Every layer the worker creates belongs to exactly one held compartment; a layer no compartment names is shown in the view. The CPU amount is cpuMillis only: it drives the tasks sums (R5) and, for guarded compartments, a Pod worker's CPU requests; layers get no cpu.max (a Pod worker runs without a CPU limit, because the container's cpu.max stops every layer at once). WORKER_CGROUP_LAYOUT keeps the layer names, the init layer and the knobs that are not amounts (cpu.weight, cpu.idle, memory.swap.max). Boot runs before the worker hears from the coordinator and cannot know the compartments, so a memory knob the layout still sets on a held layer does not decide: the worker writes the compartment's values over it at its first round, and the memory watch no longer recomputes that layer (its room arithmetic reads the compartment's memory.max as the layer's limit, and it lends nothing from a held CLI layer); the site's declaration test refuses a layout that sets memory.min, memory.max or cpu.max on a layer its declaration names. A Pod worker's memory requests equal the sum of the memoryMiB of its guarded compartments and its memory limit the sum of the memoryLimitMiB of all its compartments, so the container's limit never binds before a layer's own; its CPU requests equal the sum of the cpuMillis of its guarded compartments. Both are derived by the site from the same declaration (amended 2026-11-12 — the last interpretation of :context). Where the guarded sum does not fit (two generations of a rolling worker beside the other Pods of its node, or the node's allocatable), the site lowers the guarded memoryMiB, never the limits. Linux workers enforce with cgroup v2; Windows workers may enforce with a Job Object; macOS workers do not enforce: their compartments drive the sums only and the view marks them unenforced.")
     (rule R4 "Declaration of a task: memory = when the task's shape has a row that ran to finished within retention, ceil(max(the task's own peak, the largest peak of that shape) x SHAPE-PEAK-MARGIN) + PEAK-STEP-MIB, without reading the sender's memoryMiB; otherwise max(the sender's memoryMiB, or UNDECLARED-TASK-MIB when it names none; the task's own peak; the largest peak of its shape so far). The clamp is the largest tasks compartment memoryMiB among the workers whose needs match (replacing the measured line minus the floor). CPU = TASK-CORES cores for every task. The kind of a task is its shape; a task without a shape learns only from its own earlier attempt. The declaration is derived from rows the coordinator already keeps and becomes the task's claim when it is placed: a new coordinator table of claims keyed by task id (memory, CPU, the worker, whether R6 placed it over the sum), removed when the task leaves the placed phases. The sums of R5 read only the claims. The memoryMiB of the task row in the heartbeat reply keeps its meaning (the amount the worker compares the task's use against) and carries the claim.")
     (rule R5 "Placement: for each queued task in the existing order (priority, then id) the coordinator considers the live workers whose capabilities, version, drain and disk match and that hold a tasks compartment. A worker fits when the memory claims of its placed tasks plus this task's declaration are at most the compartment's memoryMiB, the same holds for CPU and cpuMillis, the declared group limits (GroupLimit) allow it, and the keeps of R6 leave room. Among fitting workers the order is: the environment already prepared, then the largest declared remainder (memoryMiB minus claims), then the name. No field of the measured load (CPU use, memory available, pressure, per-task use), no floor and no reserve enters the decision. A task that fits nowhere waits with TASK-COMPARTMENT-FULL, whose text names memory or CPU and each worker's sum and compartment.")
     (rule R6 "Work that must not wait (MUST-NOT-WAIT in this file): an ungated task never waits for room. It considers every live worker whose capabilities, version, drain and disk match, with or without a compartment, and ignores keeps. If it fits a compartment it is placed. Otherwise, when its priority is >= KEEP-NOW-PRIORITY, the coordinator stops preemptible tasks of strictly lower priority on the worker that needs the fewest stops (a stop releases the stopped task's claim) and places it there. Otherwise it is placed on the worker with the smallest shortfall (a worker without a compartment counts its whole declaration as shortfall), over the sum, and the overrun shows in the view (R2). Only declared group limits can hold an ungated task (TASK-LIMITED, as today); the site does not declare tight limits on must-not-wait groups. Deploy tasks and version roll-out commands declare ungated today; conversation-opening steps declare priority 1 only and their sender adds ungated with the switch. Priority keeps two roles: the order of consideration, and the right of a task of priority >= KEEP-NOW-PRIORITY that does not fit to stop preemptible lower-priority tasks (gated tasks too). A gated task of priority >= KEEP-NOW-PRIORITY that does not fit, and any gated task queued longer than STARVE-WAIT-MS, keeps its declaration on one worker whose claims free enough when their tasks end, so smaller gated tasks behind it do not take the room returned by ending tasks.")
     (rule R7 "Stop inside the tasks compartment: on an enforcing worker, when the layer's working set (memory.current minus inactive_file) comes within PEAK-STEP-MIB of its memory.max, the worker stops tasks in kubelet's eviction order until it is below: first tasks whose working set exceeds their claim (lower priority first, then larger excess), then the others by priority, lowest first, preemptible before non-preemptible at the same priority. Ungated tasks are never stopped by the worker. A stopped preemptible task returns to the queue as its next attempt (the existing rerun path); a stopped non-preemptible task fails with a reason naming the compartment. Only when no task is left to stop does the kernel's memory.max decide, inside the layer only. A non-enforcing worker (macOS) does not run R7.")
     (rule R8 "Wait reasons and refusal: the closed wait reasons become TASK-NO-COMPARTMENT (no eligible worker holds a tasks compartment — gated tasks only), TASK-COMPARTMENT-FULL (R5), TASK-LIMITED and TASK-UNDECLARED (group limits, unchanged). TASK-NO-ROOM, TASK-NO-MEMORY, TASK-RESERVED, TASK-BACKGROUND-RESERVED and TASK-UNMEASURED leave with the switch. At submit, a gated task is refused only when at least one eligible worker holds a tasks compartment and its memory or CPU declaration exceeds every such compartment (it would wait forever); while no eligible worker holds one, it waits. An ungated task is never refused for its size.")
     (rule R9 "Resident services: the coordinator keeps placing a service when the sum of the settled declarations on the worker plus the new one is at most the services compartment's memoryMiB (the rule of today, with the budget read from R1 instead of the room file). The room file's memoryMiB, the worker's --memory start argument and the Worker row's memory-mib are retired with it (one fact in one place); the room file keeps pools, warmServices and pinnedToNode. The block on placing services while the worker reports a reserve for a waiting version preparation is removed. A service re-placed after its worker is lost waits on that sum like any service; moving the services' room to their own Pods is milestone 5's.")
     (rule R10 "Destinations: every current condition goes where CONDITION-DESTINATIONS in this file says, and every member of TaskUnplacedKind has an entry in WAIT-REASON-DESTINATIONS. The worker-side lines that let hosted layers and tasks share one pool — the keep line for turns, lending under it and reclaiming, the command layer's line and floor, the CLI layer's ceiling and kept margin, recomputing the jobs layer's memory.max from the room to eviction, and the prepare reserves — are removed, because the compartments separate those uses.")
     (rule R11 "Turns, delivery and measurement: doeff places no room gate on turns of the system above or on message delivery. The host that admits turns reads the CLI layer's memory.max as it does today, now a fixed value; its rule that the top-priority turn starts without a room check belongs to that host and is recorded there. The worker keeps reporting its measured load and GET /state keeps showing it for observation and for counting waits; placement does not read it.")
     (rule R12 "Pods later: the forms above carry over when tasks, turns and resident jobs move to Pods, as the last interpretation of :context maps them. What has no k8s counterpart (R6's placement over the sum, the keeps, the preemptible mark) is dropped or rewritten for a task when it moves to a Pod.")]
  :laws
    [(law one-budget-has-one-declaration
       :statement "for_all worker w and layer l: the memory limits of l on w and the placement budgets that read l are taken from exactly one MachineCompartments row ∧ no other declaration (the room file's memoryMiB, the --memory start argument, a memory knob or cpu.max of WORKER_CGROUP_LAYOUT on a named layer, a keep, lend or command line file) changes them"
       :counterexamples
         [(counterexample "Adding compartment fields to the room file rows — the services budget is then written twice, in memoryMiB and in the new field, and the two drift")
          (counterexample "Keeping jobs memory.max in WORKER_CGROUP_LAYOUT as well — the Pod template and the declaration disagree, and whichever was written last wins until the next restart")
          (counterexample "One hosted compartment over the CLI layer and the command layer — a command's RAM disk takes the room new CLIs were promised")]
       :enforced-by ["packages/doeff-cluster/tests/test_machine_compartments.hy::test-a-held-layer-limit-has-one-declaration"]
       :wiring "Wired (2026-11-12) — the test declares a tasks compartment over a layout that sets jobs memory.max and runs the memory watch: the layer gets the compartment's value once and is not rewritten. The services side of the law (the room file's memoryMiB, the --memory start argument) is retired with R9 in the placement switch")
     (law placement-reads-claims-not-measurements
       :statement "for_all tick and gated task t placed on worker w: Σ memory claims on w after placing t ≤ memoryMiB(tasks compartment of w) ∧ Σ CPU claims ≤ cpuMillis ∧ the decision is the same for every value of the measured load of every worker"
       :counterexamples
         [(counterexample "Keeping the pressure stop next to the sums — a machine with 33 GiB free and 51 % CPU pressure takes 0 tasks again")
          (counterexample "Ordering fitting workers by measured room — the measured load returns through the choice")
          (counterexample "Subtracting a floor from the compartment — the floor returns as a second, hidden compartment size")]
       :enforced-by ["planned with the placement switch: a placement test that varies the measured load and expects the same placements"]
       :wiring "Unwired (2026-11-12) — wired with the placement switch")
     (law ungated-work-never-waits-for-room
       :statement "for_all ungated task t with at least one live worker matching its capabilities, version, drain and disk, whose declared group limits are not reached: t is placed in the first tick that considers it, whatever the sums, keeps and compartments"
       :counterexamples
         [(counterexample "Requiring a compartment for ungated tasks — while a machine's row is missing or misspelled, the deploy that would write it waits with TASK-NO-COMPARTMENT")
          (counterexample "Letting an ungated task wait for the preempted tasks to stop — a deploy waits as long as the slowest stop")
          (counterexample "Treating priority >= KEEP-NOW-PRIORITY as ungated — two flags for one fact; a sender that raises priority for order also skips the sums")]
       :enforced-by ["planned with the placement switch: a placement test with every compartment full and one worker without a compartment"]
       :wiring "Unwired (2026-11-12) — wired with the placement switch")
     (law the-claim-follows-the-measured-peaks-and-stays
       :statement "for_all task t whose shape s has a row that ran to finished within retention: the memory declaration of t = min(ceil(max(peak(t), max peak(s)) x SHAPE-PEAK-MARGIN) + PEAK-STEP-MIB, the largest tasks compartment of the workers matching t), independent of t's memoryMiB ∧ for_all placed task t: claim(t) does not change while t stays placed"
       :counterexamples
         [(counterexample "Taking the sender's number as a lower bound after the shape has finished rows — image builds keep reserving 6 times their use")
          (counterexample "Recomputing the claim of a running task every tick — when old rows leave retention its claim drops and R7 stops it as over")
          (counterexample "Clamping by the measured line — the declaration moves with other mechanisms' use, which this ADR removes from placement")]
       :enforced-by ["packages/doeff-cluster/tests/test_task_claims.hy::test-the-declaration-follows-the-settled-peak-of-the-shape-not-the-senders-number"
                     "packages/doeff-cluster/tests/test_task_claims.hy::test-the-claim-is-fixed-when-placed-and-dropped-when-the-task-leaves"
                     "packages/doeff-cluster/tests/test_task_claims.hy::test-a-claim-survives-the-state-file-and-the-durable-keys"]
       :wiring "Wired (2026-11-12) for the derivation and the claim kept while placed (coordinator/core/task_claims); the clamp is still the placing worker's measured line minus the floor until placement reads the tasks compartments")
     (law every-wait-reason-has-a-destination
       :statement "for_all member k of TaskUnplacedKind: k ∈ WAIT-REASON-DESTINATIONS ∧ every destination in CONDITION-DESTINATIONS ∈ DESTINATIONS"
       :counterexamples
         [(counterexample "Adding a new room condition as one more TaskUnplacedKind member without deciding here whether it stays — the gate grows back one incident at a time")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_005_machine_compartments_and_declared_sums.hy::test-adr-doe-cluster-005-every-wait-reason-has-a-destination"]
       :wiring "Wired (2026-11-12)")]
  :enforcement
    [(deftest test-adr-doe-cluster-005-every-wait-reason-has-a-destination
       ;; Every member of the closed wait-reason type has a destination (R8, R10); a new member without one is red.
       (val members (frozenset (gfor k TaskUnplacedKind k.name)))
       (val missing (sorted (- members (frozenset WAIT-REASON-DESTINATIONS))))
       (assert (= missing []) (+ "wait reasons without a destination in ADR-DOE-CLUSTER-005: " (str missing)))
       (assert (<= (frozenset (.values WAIT-REASON-DESTINATIONS)) (| DESTINATIONS (frozenset #("new")))))
       ;; The condition table uses only the 3 destinations and names each condition once; each must-not-wait row says how it avoids the wait.
       (assert (<= (frozenset (gfor row CONDITION-DESTINATIONS (get row 2))) DESTINATIONS))
       (assert (= (len CONDITION-DESTINATIONS) (len (frozenset (gfor row CONDITION-DESTINATIONS (get row 0))))))
       (assert (all (gfor row MUST-NOT-WAIT (and (= (len row) 3) (all row))))))]
  :plans [])
