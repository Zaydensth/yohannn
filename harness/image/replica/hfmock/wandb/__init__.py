"""A FAKE `wandb` (hfmock - DRY=1 and tests/ only). The vendored G.O.D uploader imports wandb at module top
(uploader.py:9); image tasks never pass WANDB_TOKEN (trainer/runtime.py:1141), so login() is never reached there. It
raises if it is, so a W&B path that suddenly runs is noticed instead of faked."""


def login(*_a, **_kw):
    raise RuntimeError("hfmock wandb.login called: image tasks run the uploader without WANDB_TOKEN")
