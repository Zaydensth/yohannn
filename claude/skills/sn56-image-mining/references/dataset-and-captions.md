# Dataset and captions — what the validator hands you

> Validator-side facts only, re-derived from `gradients-ai/G.O.D@b49ba86` (main, 2026-10-07) on
> 2026-10-09. How you prepare, filter, caption, bucket or split the data **for training** is your
> own design — measure it yourself.

All of this is CPU work, which makes it the cheapest place to find wins and the cheapest place to
catch bugs.

## How a task's data is made (VERIFIED by reading)

| Fact | Cite |
|---|---|
| Each task is a synthetic set of 10–50 image/caption pairs; the count is drawn uniformly up to a cap (R1 caps it at 30) | `validator/tasks/synthetics/diffusion.py:704-717`; `validator/tasks/synthetics/constants.py:61-62`; `validator/tournament/constants.py:154` |
| Category drawn by weight: style 0.10, person 0.10, logo / social / design / product 0.20 each | `validator/tasks/synthetics/constants.py:38-51`; `validator/tasks/synthetics/diffusion.py:646-685` |
| Each caption is the exact prompt its image was generated from | `validator/tasks/synthetics/diffusion.py:447-462`; `validator/infrastructure/fal_client.py:163-174` |
| Every non-style category invents a trigger (a name) that must appear in every prompt; style tasks have none | `validator/tasks/synthetics/diffusion.py:182-198`, `:465-479`, `:632` |
| The trigger is stored as the task's `trigger_word` and reaches your container as `--trigger-word` | `validator/tasks/synthetics/diffusion.py:749`; `trainer/runtime.py:232-233`, `:1028` |
| **ideogram4 only:** every caption is rewritten into Ideogram's "magic prompt" JSON, keys in canonical order, written compactly with `json.dumps(…, separators=(",", ":"))` | `validator/tasks/synthetics/diffusion.py:493-547`, `:550-572`, `:724-725` |
| Train/test split: unseeded shuffle, test = `ceil(10 % of pairs)` → 1 to 5 test images; a pair is an image plus a same-stem `.txt` | `validator/tasks/datasets/preparation.py:104-153`; `validator/tasks/datasets/constants.py:4` |
| The training zip is staged at `/cache/datasets/<task_id>_tourn.zip`, read-only inside your container | `trainer/training_paths.py:42-43`; `trainer/runtime.py:264-267` |
| The reference entrypoint extracts the zip and flattens every image and `.txt` into `/dataset/images` (a name clash becomes `<parent>_<name>`) | `ops/docker/scripts/image_toolkit_entrypoint.py:48-77` |

## How the evaluator reads captions (VERIFIED by reading)

- Every test image must have a non-empty caption, or the whole evaluation fails
  (`validator/evaluation/image_test_data.py:105-106`; `validator/evaluation/evaluators/diffusion.py:222-229`).
- Each test image is scored twice — with its caption as the prompt, and with an empty prompt —
  and the two halves are weighted 0.5 / 0.5 (`validator/evaluation/evaluators/diffusion.py:135-149`;
  `validator/evaluation/constants.py:6`; `validator/scoring/tasks.py:309-329`).
- For ideogram4 the "caption" used as the prompt is that JSON string.
- Test images are resized so the long side is 1024 (LANCZOS) and centre-cropped to multiples of
  16 before encoding (`image_test_data.py:59-67`).

Full scoring detail: `validator-scoring.md`.

## The trigger word and the templates

The upstream ideogram4 and krea2 templates ship `trigger_word: null`
(`core/training_templates/base_diffusion_ideogram4.yaml:9`, `base_diffusion_krea2.yaml:9`), but
the reference entrypoint overwrites it with the task's trigger
(`ops/docker/scripts/image_toolkit_entrypoint.py:93`). What ai-toolkit then does with a trigger
word is library behaviour: read the pinned ai-toolkit commit
(`ops/docker/standalone-image-toolkit-trainer.dockerfile:5`) before relying on it, and measure
the effect yourself.

## Offline checks on a real task (CPU, before any GPU spend)

Archive a real task's training zip and held-out set inside the 7-day window (`sn56-core` →
`references/intel-sources.md`), then:

- run your entrypoint's dataset step on the zip: every image keeps its caption, nothing is
  silently dropped, name clashes are handled;
- ideogram4: diff the captions your trainer feeds the model against the files that arrived —
  byte-identical unless you changed them on purpose, knowing the evaluator prompts with the JSON;
- the emitted training config parses, and every path in it exists in the container layout
  (`/cache` read-only, `/app/checkpoints` writable);
- resizing/bucketing produces the resolutions you intend;
- any filtering or splitting you add gives the same result on two runs over the same input.
