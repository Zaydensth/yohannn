#!/usr/bin/env python3
"""tests/make_fixture.py OUT_ROOT - build a SYNTHETIC tournament archive for the CPU tests (no real task, no real
hotkey, no real repo: every id below is obviously fake). Layout = what ../../archive/hf_archive_tournament.py fetch
writes (tasks/<round>_<family>_<task8>/ + api/ + manifest.json + ARCHIVE_DONE), plus pinned anchors in
artifacts/lora/ (MANIFEST.tsv + tiny valid .safetensors) and recorded HF file lists for the bases (cache_manifests/).
Prints the archive path."""
import hashlib
import json
import os
import struct
import sys
import zipfile

OUT = sys.argv[1]
TOURN = "tourn_00000000deadbeef_20260101"
ROOT = os.path.join(OUT, TOURN)
GIB = 1024 ** 3
TASKS = [  # (task_id, round, family, base)
    ("11111111-0000-4000-8000-000000000001", "R1", "flux", "example-org/base-flux-one-root"),
    ("22222222-0000-4000-8000-000000000002", "R1", "ideogram4", "example-org/base-ideogram"),
    ("33333333-0000-4000-8000-000000000003", "Boss", "z-image", "example-org/base-three-roots"),
]
BOARD = [("hkAAAAAA-test-hotkey", "example-org/lora-a"), ("hkBBBBBB-test-hotkey", "example-org/lora-b")]
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def zip_with(path, n, captions=True):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(n):
            z.writestr(f"img_{i}.png", PNG + bytes([i]))
            if captions:
                z.writestr(f"img_{i}.txt", f"a test caption number {i}")


def lora(path, seed):
    data = hashlib.sha256(seed.encode()).digest() * 128
    hdr = json.dumps({"t.lora_down.weight": {"dtype": "F32", "shape": [32, 32], "data_offsets": [0, 4096]}},
                     separators=(",", ":")).encode()
    hdr += b" " * ((8 - len(hdr) % 8) % 8)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(hdr)) + hdr + data)
    return hashlib.sha256(open(path, "rb").read()).hexdigest(), os.path.getsize(path)


os.makedirs(os.path.join(ROOT, "api"))
os.makedirs(os.path.join(ROOT, "artifacts", "lora"))
man_rows, tasks_meta = [], []
for i, (tid, rnd, fam, base) in enumerate(TASKS):
    d = os.path.join(ROOT, "tasks", f"{rnd}_{fam}_{tid[:8]}")
    os.makedirs(d)
    zip_with(os.path.join(d, "test_data.zip"), 3)
    zip_with(os.path.join(d, "training_data.zip"), 4)
    board = [{"hotkey": hk, "rank": r + 1, "test_loss": 0.08 + 0.01 * r + 0.001 * i, "repo": repo, "score_reason": "test"}
             for r, (hk, repo) in enumerate(BOARD)]
    json.dump({"task_id": tid, "model_type": fam, "model_id": base, "hours_to_complete": 0.0005, "trigger_word": "tok",
               "training_data": "https://example.invalid/presigned/training_data.zip?sig=fake",
               "test_data": "https://example.invalid/presigned/test_data.zip?sig=fake",
               "updated_at": "2026-01-01T00:00:00Z", "hotkey_details": board},
              open(os.path.join(ROOT, "api", f"{tid}.json"), "w"), indent=1)
    tasks_meta.append({"task_id": tid, "round": rnd, "model_type": fam})
    for r, (hk, repo) in enumerate(BOARD):
        role = "TOP1" if r == 0 else "BOSS"
        p = os.path.join(ROOT, "artifacts", "lora", f"{tid[:8]}_{hk[:8]}.safetensors")
        sha, size = lora(p, f"{tid}{hk}")
        man_rows.append({"task8": tid[:8], "hk8": hk[:8], "role": role, "round": rnd, "model_type": fam, "task_id": tid,
                         "repo": repo, "file": "checkpoints/last.safetensors", "pinned_commit": "a" * 40,
                         "head_commit": "a" * 40, "head_matches": "True", "bytes": str(size), "sha256": sha,
                         "lfs_sha256": sha, "official_test_loss": str(board[r]["test_loss"]), "status": "OK", "path": p})
cols = ["task8", "hk8", "role", "round", "model_type", "task_id", "repo", "file", "pinned_commit", "head_commit",
        "head_matches", "bytes", "sha256", "lfs_sha256", "official_test_loss", "status", "path"]
with open(os.path.join(ROOT, "artifacts", "lora", "MANIFEST.tsv"), "w") as fh:
    fh.write("\t".join(cols) + "\n")
    for r in man_rows:
        fh.write("\t".join(r[c] for c in cols) + "\n")
json.dump({"tournament_id": TOURN, "tasks": tasks_meta, "failures": []}, open(os.path.join(ROOT, "manifest.json"), "w"))
open(os.path.join(ROOT, "ARCHIVE_DONE"), "w").write("{}")
mdir = os.path.join(OUT, "cache_manifests")
os.makedirs(mdir)
for repo, files, gated in (("example-org/base-flux-one-root", [["flux1.safetensors", 23 * GIB], ["ae.safetensors", GIB // 3]], False),
                           ("example-org/base-three-roots", [["a.safetensors", 6 * GIB], ["b.safetensors", 7 * GIB],
                                                             ["c.safetensors", 8 * GIB]], False),
                           ("example-org/base-gated", [["m.safetensors", 9 * GIB]], "auto")):
    json.dump({"repo": repo, "sha": "b" * 40, "gated": "false" if gated is False else gated, "files": files},
              open(os.path.join(mdir, repo.replace("/", "--") + ".json"), "w"))
print(ROOT)
