# RunPod operations — the method (no prices, no ids, no secrets)

How an agent runs rented GPU pods for one tournament cycle without burning money or losing evidence.
Everything here is a *method*; every number that depends on your account (balance, prices, pod ids,
volume ids) lives in your private vault, never in a repo. Read `harness/image/runpod/DESIGN.md` for
the two-pod validator replica itself; this file is about operating pods safely.

## 0. Iron rules (they cannot be overridden by any agent)

1. **Power is the user's.** An agent never deletes/terminates a pod. A pod **created by this session** may be
   **STOPPED** by the agent as soon as it is idle and its results are copied (STOP keeps the network volume,
   deletes nothing). Pods the session did not create — including pods of people who share the account — are
   never touched; ask.
2. **Idle GPU = tell the user, then stop it.** "Running only to idle" is the one state that is always wrong.
3. **No pod before the chain is ready.** Create a pod only when the whole chain (provision → archives → prefetch
   → queue) and its gates exist and were dry-run on CPU; a pod created "first" idles 20–50 min at full price.
4. **Money never blocks, but it is always visible.** Read the balance before every start, log it every 15 min
   (money guard), and park the queue at a floor you agreed with the user. Topping up is the user's job; the
   agent reports the number, it does not wait for it.
5. **Every pod step is logged** in an append-only ops log with UTC timestamps: created / provisioned / started /
   each result row / stopped, with the cost rate. That log is what the next session (or the next account) reads.

## 1. Credentials — read into a variable, never print

- API key in a `0600` file outside every repo (e.g. `~/.runpod_env`, raw key or `KEY=value`). Read it with
  `key="$(grep -Eo '[A-Za-z0-9_]{20,}' ~/.runpod_env | head -1)"`, pass it as a curl header from stdin
  (`printf 'header = "Authorization: Bearer %s"\n' "$key" | curl -K - …`), then `unset key`. **Never `cat` the
  file** in a transcript — a printed key is a leaked key (it happened once; it goes in your vault as a lesson).
- Hugging Face write token likewise (`~/.config/sn56/hf_write_token`, `key=value` lines): parse the one line
  you need, copy it to the pod over ssh as a `0600` file, compare sha256 of the two copies — never echo it.
- SSH key per provider; host keys verified through the provider's proxy *before* the first direct connection
  (read the pod's boot log for the fingerprint, then trust that exact fingerprint).

## 2. Creating a pod (REST v1, or the provider's MCP tools)

- `GET /gpu-types`/capacity first: pick a **data center that has stock now**; a body with an unavailable region
  or an unsupported field (e.g. a CUDA version the enum does not know) fails with 400/500 — retry on a
  bounded schedule (e.g. 16 × 45 s), never in a tight loop.
- Body = GPU type + count, container disk, volume (network volume id + mount path, or none), image, env, start
  command that installs sshd and writes a boot status file (including the sshd host-key fingerprint) so the
  Mac can verify it.
- Record immediately: pod id, name, region, cost/h, createdAt, proxy user, public ip:port — into a state file
  the chain reads (`state/pod_<X>.env`) and into the ops log. Append the id to `state/our_pods.txt`: that list
  is the only authority for "pods this session may stop".
- Balance: GraphQL `query { myself { clientBalance currentSpendPerHr } }` works where REST has no balance
  endpoint. Log `usd`, account `rate/h`, and the share that is ours.

## 3. Network volumes — create after a tournament, delete when the next one starts

- A volume is a cache across pods (base weights, text encoders, built images, kit, task archives, trained
  adapters). Without it every pod re-downloads 30–50 GB and rebuilds — the user's time, at GPU price.
- **Lifecycle:** tournament ends → create the volume in the region that has GPU stock *today* (volumes are
  region-bound; the region you used last week may be empty) → every pod of the cycle mounts it → when the next
  tournament **starts**, the agent **reminds the user to delete it** (deletion is the user's action) after the
  unique contents (trained adapters, eval JSON, witnesses) are copied to the Mac/HF and listed in the vault.
  Base weights/images are re-downloadable: list name + sha256 + size, do not copy.
- Default-tier volumes may be NFSv4: `cp -p` volume→volume fails on `system.nfs4_dacl` (data copied, rc 1) —
  use `cp --preserve=timestamps` or rsync without `-p`.
- A reused volume keeps old clones: the chain must re-check `git rev-parse HEAD == pin` for **every** repo on
  **every** run, not only at first clone — otherwise you silently train last week's SHA.

## 4. The chain pattern (what runs where)

```
Mac (operator, no GPU)                 Pod (GPU)
  info/fp/trust/token  ──ssh──▶  boot status, host key, token file
  relay (archives, kit, bundles, MANIFEST.sha256; every push sha256-compared)
  prestart (disk, docker, base inventory, volume exclusivity)  ◀── read-only checks
  start  ──────────────────────▶  tmux: provision → repos → plan → prebuild → prefetch → inventory → WAIT_GO
  guard (deadline STOP), moneyguard (floor), autogo (writes QUEUE_GO when Mac gates PASS),
  brakewatch (sanity brake between control cells), idlewatch (copy evidence → STOP at queue end/idle)
```

- **Watchers must not depend on the Claude session.** Launch them with `nohup caffeinate … & disown` (Mac) so a
  session that hits its usage limit, crashes or compacts leaves the pod supervised: deadline → STOP, queue end
  → copy + STOP, idle > N min → STOP, balance < floor → park.
- **Watchers read the same environment as the chain.** If you needed a DNS/proxy workaround for one call
  (`harness/common/doh/`), the watchers need it too — restart them with it.
- **Never edit a script that is running** (bash reads it from the current file offset); never let two runs
  write the same directory; names with quotes break `docker create`.
- A pod waiting for a "go" that an agent was supposed to write idles at full price — the go must be a
  mechanical gate (money START_OK + guard RUNNING + prestart PASS), never "an agent's next turn".

## 5. Evidence before stop

- `copy`: rows (`results_*.tsv`), queue log, run dirs (run.log, score.log, meta, witnesses), plan copies —
  **excluding** model weights and anything that looks like a token (mock hubs included). Then STOP.
- On a **volumeless** pod, STOP keeps `/workspace` and loses the container disk (`/app/checkpoints`) — copy or
  move what you need first.
- Write the final line in the ops log: stopped at, total hours × rate ≈ cost.

## 6. Account usage limits and handoff (applies to every workflow)

When the user reports the Claude usage limit at **≥ 96 %**: stop parallel agents/workflows (they share the
quota), write the **handoff** (`templates/jobs/HANDOFF.md`) and push the vault, make sure every session pod is
either STOPPED or under session-independent watchers with a one-line stop command recorded, then halt. The pod
must never depend on the agent being alive. The same handoff is written whenever the user switches Claude
accounts (see `claude/commands/handoff.md`).
