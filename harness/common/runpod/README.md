# Pod-side self-stop with evidence upload (`pod_selfstop.sh`)

A rented pod must never depend on an agent (or a laptop) being alive to stop billing — but stopping without saving
the evidence makes the run worthless. This script is the third safety layer, after the operator-side idle watcher
(copy evidence → STOP at queue end) and the deadline guard:

1. at a **deadline** (epoch UTC; set it to "expected queue end + 15–20 min" — a hard deadline cuts a late cell, choose
   the margin consciously), it packs rows/logs/run dirs **without weights or secrets** into one `.tgz`,
2. uploads it to a **public** Hugging Face dataset (`pod_evidence/<pod>/…`; keep private repos for task datasets —
   their quota is small), using the HF token already on the pod (`$I5/secrets/.hf_env`, never printed),
3. **stops the pod** (not terminate) through the GraphQL `podStop` mutation with the **pod-scoped `RUNPOD_API_KEY`**
   that RunPod injects into every pod (read from PID 1's environment), falling back to `runpodctl`.

`STOP` keeps the pod's volume disk (`/workspace`): trained weights stay retrievable by starting the pod again later.
Only the container disk is lost — copy anything you need from it into the work root before the deadline.

VERIFIED 2026-10-21 by the kit's author: a throw-away COMMUNITY pod ($0.13/h) ran `sleep 300` then `podStop` with its own
pod-scoped key and reached `EXITED` six minutes after creation; a 950 KB evidence bundle uploaded from a working pod was
downloaded and read on the operator machine.

```bash
# on the pod, detached from the ssh session:
POD_WORK_ROOT=/workspace/i5 nohup setsid bash pod_selfstop.sh arm <deadline_epoch> <you>/<public-evidence-dataset> >/dev/null 2>&1 &
# verify the evidence path once, without stopping:
bash pod_selfstop.sh test-upload <you>/<public-evidence-dataset>
```

Never put your account-level RunPod API key on a pod; the injected pod-scoped key is enough to stop that pod.
