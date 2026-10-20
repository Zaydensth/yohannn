# The working direction for one cycle — full train, HF-style eval, compare with the latest winner, archive the data

This is the loop the kit's author runs every week. It is a *method*: the task ids, scores and winners are yours
to pull. Everything below is executed by the agents; you approve the outbound steps (push to `main`, registration).

## 0. Three principles

1. **Measure like the validator or do not measure.** A candidate's number counts only when it comes from a full
   training run at the task's own wall-clock budget, an artifact pushed to Hugging Face, and an evaluation that
   runs the public evaluator image on the validator's real held-out set. CPU smoke tests and shortened runs are
   gates, never evidence.
2. **One change at a time, paired.** Every arm is compared with a control that differs in exactly one thing, on the
   same task, same seed, same evaluation path. Noise is measured (repeat the control) before a delta is believed.
3. **Generalise before shipping.** A win on the latest tournament's task is one sample. The candidate is re-run on
   archived tasks of the same family (other tournaments, other sizes/budgets) before it goes into `main`.

## 1. The data: archive the latest tournament's **task datasets** to Hugging Face, privately

What is archived is the task data itself — every task's `training_data.zip` and `test_data.zip` plus its `api.json` —
so that you can **download it again** for a retest or a retrain whenever you need it. The official links are
presigned and die after 7 days; after that the real held-out set is gone for good. This is not about storing
results or scores.

- Right after a tournament: pull every task of your track from the public task API (`hotkey_details`, `test_data`,
  `training_data`) with `harness/image/archive/hf_archive_tournament.py fetch <tournament_id>`; the presigned
  URLs **expire after 7 days** — fetch inside the window, and fetch again after the boss round completes (its tasks
  get URLs late).
- Upload the archive to a **private** dataset repo (`upload --token-file …`): `tasks/<round>_<family>_<id>/{training_data.zip,test_data.zip,api.json}` + manifest. Record the repo name, HEAD commit and task table in
  the vault. This is the only honest held-out data you will ever get; keep every tournament.
- Use the archives for calibration cells, for the generalisation axis (§4) and for post-mortems.

## 2. The candidate: full train → push to Hugging Face (public test repos)

- Train the candidate **exactly as the validator would**: the production downloader → your trainer image → the
  production uploader, at the task's `hours_to_complete`, with the task's real training set. The harness
  (`harness/image/replica/`) runs these three as separate processes, as production does.
- The artifact goes to a Hugging Face **public** model repo (the evaluator resolves public repos; use throwaway
  names, delete after scoring if you like — the harness has `delete_after_score`). Keep the sha256 and size of
  the uploaded file: the evaluation must score *those* bytes.
- Your private material (datasets, evidence, adapters you keep) goes to **private** repos. Never the other way
  round.

## 3. The evaluation: Hugging Face / tournament style

- Run the **public evaluator image** pinned to the G.O.D commit you audited, on the task's real `test_data`, with
  the validator's settings (image track: latent velocity-MSE, 16 sigma × 16 noise, text and no-text halves). The
  harness `score_latent.sh` / `run_latent_eval.py` do this and keep the raw JSON.
- Label the hardware honestly: if your eval GPU class or batch size differs from the validator's (A100 80 GB,
  batch 2 for image at the audited commit), the absolute number is **INFO**; paired deltas on the same path remain
  valid.
- Check the three forfeit paths before reading a score: a real non-empty artifact, container exit 0 inside the
  wall clock, the promoted checkpoint is the one you meant.

## 4. The comparison: control, shipped recipe, and the latest winner — in one table

For every task you train on, the read-out is one paired table, never a single number:

| Cell | Tree | Seed | Score (↓) | Δ vs control (ln %) | Steps / budget used | Note |
|---|---|---|---|---|---|---|
| Official winner (TOP1) and TOP2 of that tournament | — | — | from the task API | — | — | the bar to clear |
| Your official entry (if any) | shipped SHA | — | from the task API | — | official log | what the validator saw |
| Replay of the shipped recipe (full run) | shipped SHA | preset | measured | — | measured | "does the recipe reproduce?" |
| Control (frozen measurement tree) | measure SHA | s | measured | 0 | | one per seed |
| Arm A, B, … | measure SHA | s | measured | d_s | | one row per seed |

- Δ = 100·ln(arm/control) per seed; summarise with mean and an upper confidence bound using a locked formula and a
  noise floor measured from repeated controls. The decision rule (how many seeds must be negative, what bound
  counts as ADOPT) is written in the pre-registration **before** the first GPU hour.
- **Compare with the latest tournament's winner at the end**: where would the candidate have placed on that
  board (INFO if the eval hardware differs), and how far is it from TOP1/TOP2 in ln %?
- **Generalisation axis**: the same paired cells on ≥ 2 archived tasks of the same family (different tournament,
  different size/budget). A candidate that wins on one task and loses on another is not shipped; report per task.

## 5. Ship, then learn

- `main` is the tournament entry: it is pushed only after the paired table says ADOPT on the locked rule, the
  generalisation axis agrees, and the user approves (CLAUDE.md §0.3). Branches for experiments are pushed freely.
- After the tournament: post-mortem with the official board, the official logs and your own replay — the chain of
  causes traced in the validator's code — saved to the vault as the most valuable memory of the cycle.

## 6. Hugging Face write token — set it up at first use

The whole loop needs a Hugging Face **write** token (fine-grained: write access to repos you own). On first use the
agent asks you to create it and to store it where the tools read it; it never asks you to paste it into chat:

```bash
umask 077; cat > ~/.hf_env <<'EOF'      # workstation: the archive tools read this file
HF_TOKEN=hf_...
HF_USERNAME=<your-hf-user>
EOF
# the GPU box / pod gets a 0600 copy as /root/.hf_env (or the harness's secrets path) over ssh — never in a URL
```

Check it without printing it: `python3 -c "import os;print(os.path.getsize(os.path.expanduser('~/.hf_env')))"` and an
authenticated `GET https://huggingface.co/api/whoami-v2` (header from the file) must return your user. Keep both
copies `0600`, out of every repository, and record only the *path* in your vault.
