#!/usr/bin/env python3
"""Cek kesehatan graf vault — READ-ONLY, tanpa dependensi, tanpa LLM.

Vault ini SUDAH graf: konvensi kita memakai [[wikilink]] antar-memory. Yang hilang
bukan grafnya, melainkan pemeriksaannya. Tiga hal yang dicari:

  menggantung : [[x]] yang berkasnya belum ada. Konvensi kita membolehkan ini
                ("menandai yang layak ditulis nanti") — tapi tanpa daftar, ia jadi
                niat yang terlupakan, bukan penanda.
  yatim       : memory yang tidak ditaut siapa pun. Ia hanya bisa ditemukan lewat
                MEMORY.md; kalau recall meleset, pengetahuannya efektif hilang.
  drift indeks: berkas yang tidak terdaftar di MEMORY.md -> tidak akan pernah dimuat.

Pakai:  python3 tools/graph_check.py [subdir ...]     (default: semua)
"""
import re, sys, pathlib, collections

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUIET = "--quiet" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
dirs = args or [p.name for p in ROOT.iterdir()
                if p.is_dir() and not p.name.startswith((".", "tools"))]

def scan(base):
    out = {}
    for p in base.rglob("*.md"):
        if p.stem == "MEMORY" or "/.git/" in str(p):
            continue
        out[p.stem] = p
    return out

allm = {}
for d in dirs:
    allm.update(scan(ROOT / d))

links, inbound = collections.defaultdict(set), collections.Counter()
for name, p in allm.items():
    for tgt in re.findall(r"\[\[([^\]|#]+)", p.read_text(errors="replace")):
        tgt = tgt.strip().split("/")[-1]
        links[name].add(tgt)
        inbound[tgt] += 1

dangling = collections.Counter()
for src, tgts in links.items():
    for t in tgts:
        if t not in allm:
            dangling[t] += 1

if QUIET:
    # Satu baris untuk hook akhir-sesi. Sengaja TIDAK mencetak apa pun kalau bersih —
    # keluaran rutin yang selalu muncul akan berhenti dibaca dalam seminggu.
    orph_n = sum(1 for n in allm if inbound[n] == 0)
    drift_n = 0
    for d in dirs:
        idx = ROOT / d / "MEMORY.md"
        if idx.exists():
            txt = idx.read_text(errors="replace")
            drift_n += sum(1 for p in (ROOT / d).glob("*.md")
                           if p.stem != "MEMORY" and p.stem not in txt)
    if dangling or drift_n:
        bits = []
        if dangling: bits.append(f"{len(dangling)} tautan menggantung")
        if drift_n:  bits.append(f"{drift_n} tak terdaftar di MEMORY.md")
        print(f"[vault] graf: {', '.join(bits)} — python3 tools/graph_check.py")
    sys.exit(0)

print(f"vault: {len(allm)} memory di {len(dirs)} direktori\n")
print(f"-- MENGGANTUNG ({len(dangling)}) — ditunjuk tapi belum ditulis")
for t, c in dangling.most_common():
    print(f"   {c}x  [[{t}]]")
orph = sorted(n for n in allm if inbound[n] == 0)
print(f"\n-- YATIM ({len(orph)}) — nol tautan masuk")
for n in orph:
    print(f"   {n}")
print("\n-- DRIFT INDEKS — ada berkasnya tapi tak terdaftar di MEMORY.md")
drift = 0
for d in dirs:
    idx = ROOT / d / "MEMORY.md"
    if not idx.exists():
        continue
    txt = idx.read_text(errors="replace")
    for p in (ROOT / d).glob("*.md"):
        if p.stem != "MEMORY" and p.stem not in txt:
            print(f"   {d}/{p.stem}")
            drift += 1
print("   (tidak ada)" if not drift else "")
print(f"\n-- SIMPUL PUSAT (paling banyak ditaut)")
for n, c in inbound.most_common(8):
    if n in allm:
        print(f"   {c}x  {n}")
