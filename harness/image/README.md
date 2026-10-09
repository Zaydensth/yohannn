# image — SN56 image-track harness

| dir | what | status |
|---|---|---|
| `replica/` | the box kit: train / upload / verify / score-through-HF / fetchcheck / delete exactly like the validator, calibration against official boards, a lock-gated queue, staging + integrity checks, CPU tests | CPU-tested (stubs + loopback mock hub); not yet run on a GPU box in this form |
| `archive/` | keep every tournament's datasets in private HF dataset repos before the 7-day presigned URLs expire; HF account cleanup | CPU-tested (regression tests, no network) |
| `runpod/` | a two-pod design (TRAIN H100 + EVAL A100) for when you have no long-lived box | design only |

Start with `archive/` (data you do not archive this week is gone next week), then `replica/README.md`.
The mechanics behind every step (what the validator does, file:line) are in `../../knowledge/image-track.md`.
