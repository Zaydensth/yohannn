# FABLE_LEDGER — <track> (append-only)

Satu baris per konsultasi strategist (`~/.claude/CLAUDE.md` §0.9 dan §4). Format:

    FABLE <yyyymmdd-hhmm> PLAN=<slug> POINT=<lock|rescope|gonogo|postmortem> VERDICT=<GO|NO-GO|REVISI|UNAVAILABLE|BYPASS-USER> BASIS=<file:line / angka>

Aturan:
- **Append-only.** Tambah baris dengan `cat >> FABLE_LEDGER.md <<'EOF'`; jangan pernah menyunting
  atau menghapus baris lama. Ledger yang diam-diam kehilangan satu baris lebih buruk daripada
  tanpa ledger.
- Baris yang sama disalin sebagai `FABLE-PLAN: FABLE <...>` ke berkas prereg rencana itu,
  **sebelum** LOCK. Setelah LOCK, prereg hanya boleh disunting di bagian `## RESULTS`.
- `VERDICT=UNAVAILABLE` — strategist tak terjangkau: hanya sel yang sudah tercakup GO sebelumnya
  yang boleh jalan; lapor NEEDS USER.
- `VERDICT=BYPASS-USER` — perintah langsung user: kutip kata-katanya di `BASIS`.
- Gerbang G0 (`gate-auditor`) mencari baris yang dimulai dengan `FABLE ` di berkas ini.
  Contoh format di atas sengaja diindentasi supaya tidak pernah terbaca sebagai baris sungguhan.

Baris pertama di bawah garis ini.

---
