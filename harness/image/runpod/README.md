# runpod — a two-pod design (TRAIN H100 + EVAL A100) for when you have no long-lived box

`DESIGN.md` maps the validator's image pipeline onto rented RunPod pods: one TRAIN pod on an H100 (downloader ->
trainer -> uploader as three processes in one container), one EVAL pod on an A100 80 GB (the public evaluator image),
and Hugging Face as the bridge between them - as in production.

- **Status:** design only. Nothing here was run on RunPod by this package.
- **Prerequisites:** your RunPod account, payment method and API key (kept in a `0600` file outside every repo); a
  machine with a docker daemon to build and push the images (pods cannot build); a registry.
- **Run:** read `DESIGN.md` §C (order of calls). Every create / stop / delete is billable or changes billing: an agent
  prepares it, you approve each one.
- **Not included:** prices (they change: read the RunPod console at rent time), cost plans, measured box speeds.
