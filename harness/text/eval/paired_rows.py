"""Paired per-row comparison of two held-out scorings of the same task (validator_parity_eval.py rows).

Usage: python3 paired_rows.py <control rows.jsonl> <arm rows.jsonl> [field losses comma-separated]

Prints: mean of each, relative delta, a 2000-resample bootstrap 95% interval of the relative delta over rows, the count
of rows each side wins by more than 0.01 nats (the validator's per-example tie dead zone, BOSS_ROUND_TIE_DEADZONE_NATS,
G.O.D validator/tournament/constants.py:277 at b49ba8644a0d), and the arm's rank against the field. Rows are paired by
their index in the held-out file; rows scored in only one file are dropped and counted.

This is a diagnostic, not the boss-round gate: that gate (validator/tournament/thresholds.py compare_paired_losses,
constants.py:262-319) uses a ONE-sided bootstrap at 0.99 with 10,000 resamples, a minimum win rate of decided examples
and a minimum mean gap - read it before you predict a boss round. The bootstrap here measures row sampling only, not
run-to-run noise: measure that yourself (score one artefact twice; train one config with two seeds) - an arm needs both.
"""
import json
import random
import sys


def load(path):
    rows = {}
    for i, line in enumerate(open(path)):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ce") is None:
            continue
        key = r.get("i", r.get("idx", r.get("row", i)))  # validator_parity_eval.py writes the held-out index as "i"
        rows[key] = float(r["ce"])
    return rows


ctl, arm = load(sys.argv[1]), load(sys.argv[2])
keys = sorted(set(ctl) & set(arm))
dropped = len(set(ctl) ^ set(arm))
a = [ctl[k] for k in keys]
b = [arm[k] for k in keys]
n = len(keys)
ma, mb = sum(a) / n, sum(b) / n
rel = (mb - ma) / ma * 100
rng = random.Random(1337)
boots = []
for _ in range(2000):
    idx = [rng.randrange(n) for _ in range(n)]
    sa = sum(a[i] for i in idx) / n
    sb = sum(b[i] for i in idx) / n
    boots.append((sb - sa) / sa * 100)
boots.sort()
lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]
wins = sum(1 for x, y in zip(a, b) if x - y > 0.01)
losses = sum(1 for x, y in zip(a, b) if y - x > 0.01)
print("paired rows=%d dropped=%d control=%.6f arm=%.6f delta=%+.2f%% boot95=[%+.2f%%, %+.2f%%] arm_wins=%d arm_losses=%d (dead zone 0.01)"
      % (n, dropped, ma, mb, rel, lo, hi, wins, losses))
if len(sys.argv) > 3:
    field = sorted(float(x) for x in sys.argv[3].split(","))
    print("rank control=%d/%d arm=%d/%d" % (1 + sum(f < ma for f in field), len(field) + 1,
                                            1 + sum(f < mb for f in field), len(field) + 1))
