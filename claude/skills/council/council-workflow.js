// Council — 5 penasihat, review anonim, chairman synthesis.
// Isi QUESTION dan CONTEXT, lalu jalankan lewat tool Workflow.
export const meta = {
  name: 'council',
  description: 'Lima penasihat independen menjawab satu keputusan, saling review anonim, chairman menyintesis',
  phases: [
    { title: 'Advise',    detail: '5 penasihat paralel di model berbeda' },
    { title: 'Review',    detail: 'review anonim silang — barrier disengaja' },
    { title: 'Chairman',  detail: 'sintesis jadi satu verdict' },
  ],
}

// ── ISI DUA INI ──────────────────────────────────────────────────────────────
const QUESTION = args?.question ?? `<keputusan yang harus diambil, satu kalimat>`
const CONTEXT  = args?.context  ?? `<konteks selengkap mungkin: apa yang sudah dicoba,
angka yang sudah terukur, kendala, tenggat, apa yang membuatmu ragu>`
// ─────────────────────────────────────────────────────────────────────────────

const EVIDENCE_RULE = `
DISIPLIN BUKTI — wajib, reviewer akan menghukum pelanggarannya:
- Setiap klaim mekanik ditandai VERIFIED (kamu baca di kode / jalankan / ada angkanya)
  atau INFERRED (penalaran masuk akal yang belum diuji).
- VERIFIED butuh jejak: file:line, output perintah, angka terukur, atau URL.
- Jangan pernah mengarang angka, rank, atau baris log. Kalau tidak tahu, tulis tidak tahu.
- Sumber kebenaran mekanika turnamen adalah gradients-ai/G.O.D branch main. Dokumen
  mengajarkan; kode yang menang.
- Baca memory proyek yang relevan (indeks MEMORY.md di direktori memory proyek) sebelum menjawab.`

const ADVISORS = [
  { key: 'contrarian', model: 'fable', persona: `Kamu THE CONTRARIAN.
Asumsikan rencana ini punya cacat fatal dan tugasmu menemukannya. Kalau semuanya tampak
kokoh, kamu belum menggali cukup dalam. Serang asumsi yang tidak diucapkan, bukan yang
diucapkan. Cari cara ini gagal secara diam-diam — bukan gagal berisik, itu mudah ketahuan.
Kamu TIDAK bertugas menyeimbangkan; peran lain yang melakukan itu.` },

  { key: 'first-principles', model: 'fable', persona: `Kamu THE FIRST-PRINCIPLES THINKER.
Abaikan pertanyaan seperti yang diajukan. Tanya: apa sebenarnya yang sedang dipecahkan?
Bongkar asumsi yang tertanam di dalam framing-nya. Sering kali variabel yang dioptimasi
memang variabel yang salah. Bangun ulang persoalannya dari nol, lalu baru jawab —
dan katakan terus terang kalau pertanyaannya sendiri yang keliru.` },

  { key: 'forfeit-hunter', model: 'opus', persona: `Kamu THE FORFEIT-HUNTER.
Kamu hanya peduli satu hal: bagaimana keputusan ini berujung skor NOL. Exit code non-nol,
wall-clock overrun, artefak hilang atau salah dipilih, kontrak eval tidak cocok, parser
tidak ter-mapping, container tidak melapor sukses. Kualitas model tidak menarik bagimu —
run yang forfeit skornya nol berapa pun bagusnya. Telusuri setiap jalur mati yang dibuka
atau ditutup keputusan ini, dan urutkan menurut biaya.` },

  { key: 'dedup-adversary', model: 'opus', persona: `Kamu THE DEDUP ADVERSARY.
Berperanlah sebagai juri T2 yang tidak punya konteks apa pun tentang tim ini: kamu hanya
melihat kode yang dikirim. Pertanyaanmu: apakah delta ini benar-benar milik mereka, dan
apakah ia BENAR-BENAR MENYALA saat runtime — atau cuma ada di kepala mereka? Ukur dengan
aturan juri T2 publik (gradients-ai/G.O.D, validator/infrastructure/repo_dedup_config.json):
ganti nilai knob, re-parametrisasi, rename, dan cabang yang tak pernah menyala saat training =
kosmetik; satu mekanisme baru yang terkonfirmasi berjalan saat training = distinct;
padding/obfuscation = drop_evasion.` },

  { key: 'executor', model: 'sonnet', persona: `Kamu THE EXECUTOR.
Kamu hanya peduli: Senin pagi kerjakan apa? Di hardware apa, berapa lama, diukur dengan
metrik apa, dan bagaimana kita tahu ia berhasil? Rencana tanpa langkah pertama yang konkret
kamu tolak, sebagus apa pun kedengarannya. Bedakan tegas apa yang bisa diverifikasi di CPU
hari ini dari apa yang benar-benar butuh GPU. Kalau perlu GPU, sebutkan SATU eksperimen
paling bernilai untuk sesi itu.` },
]

phase('Advise')
const advice = await parallel(ADVISORS.map(a => () =>
  agent(`${a.persona}

${EVIDENCE_RULE}

KEPUTUSAN YANG HARUS DIAMBIL:
${QUESTION}

KONTEKS:
${CONTEXT}

Jawab dari peranmu saja — jangan menyeimbangkan, jangan menulis esai netral. Maksimal ~500
kata. Akhiri dengan satu baris: "POSISI: <ya/tidak/tergantung> — <satu kalimat alasan>".`,
    { label: `advise:${a.key}`, phase: 'Advise', model: a.model, effort: 'high' })
    .then(text => text && { key: a.key, text })
))

const got = advice.filter(Boolean)
log(`${got.length}/5 penasihat menjawab`)
if (got.length < 3) return { error: 'terlalu sedikit penasihat menjawab', got }

// Anonimkan + acak pemetaan huruf (deterministik: pakai urutan hasil, bukan RNG)
const LETTERS = ['A','B','C','D','E']
const shuffled = got.map((g, i) => ({ ...g, letter: LETTERS[(i * 3 + 1) % got.length] }))
const anonymized = shuffled
  .slice().sort((x, y) => x.letter.localeCompare(y.letter))
  .map(g => `### Response ${g.letter}\n\n${g.text}`).join('\n\n---\n\n')

phase('Review')
const REVIEW = {
  type: 'object',
  required: ['strongest', 'biggest_blind_spot', 'what_all_missed'],
  properties: {
    strongest:          { type: 'string', description: 'huruf + kenapa' },
    biggest_blind_spot: { type: 'string', description: 'huruf + apa yang dilewatkan' },
    what_all_missed:    { type: 'string', description: 'yang TIDAK disebut satu pun — bagian paling berharga' },
    unsupported_claims: { type: 'array', items: { type: 'string' },
                          description: 'klaim yang ditandai VERIFIED tapi tanpa jejak, atau angka yang tampak dikarang' },
  },
}
const reviews = await parallel(ADVISORS.map((a, i) => () =>
  agent(`Kamu reviewer independen. Kamu TIDAK tahu siapa menulis apa — jangan menebak.

KEPUTUSAN: ${QUESTION}

${anonymized}

Jawab tiga pertanyaan:
1. Mana yang paling kuat, dan kenapa?
2. Mana yang punya blind spot terbesar, dan apa blind spot-nya?
3. Apa yang dilewatkan SEMUANYA? Ini pertanyaan paling berharga — celah antar-jawaban
   biasanya memuat hal yang tidak terpikir oleh siapa pun sendirian. Jangan jawab "tidak ada".

Selain itu, daftarkan setiap klaim yang ditandai VERIFIED tapi tidak membawa jejak
(file:line, output, angka, URL), atau angka yang tampak dikarang.`,
    { label: `review:${i + 1}`, phase: 'Review', model: i % 2 ? 'opus' : 'sonnet',
      schema: REVIEW, effort: 'high' })
))

phase('Chairman')
const verdict = await agent(`Kamu CHAIRMAN dari council ini. Lima penasihat menjawab satu
keputusan dari sudut berbeda, lalu saling me-review secara anonim. Tugasmu menyintesis —
bukan merata-ratakan.

KEPUTUSAN: ${QUESTION}

KONTEKS:
${CONTEXT}

TAHAP 1 — jawaban penasihat (dengan peran aslinya):
${got.map(g => `## ${g.key}\n${g.text}`).join('\n\n---\n\n')}

TAHAP 2 — review anonim:
${JSON.stringify(reviews.filter(Boolean), null, 1)}

Aturan sintesis:
- Bobot argumen menurut BUKTInya, bukan menurut percaya dirinya. Klaim VERIFIED bertrace
  mengalahkan INFERRED, sekuat apa pun bahasanya.
- Kalau reviewer menandai klaim tanpa jejak, buang klaim itu dan katakan kamu membuangnya.
- Cari di mana dua penasihat saling menguatkan tanpa menyadarinya — itu sering argumen
  terkuat yang tidak dilihat siapa pun sendirian.
- Ketidaksepakatan yang tulus jangan dihaluskan. Sebutkan, lalu putuskan, lalu jelaskan.
- Putuskan. Jangan mengembalikan survei opsi.

Keluarkan TEPAT bagian-bagian ini, dalam urutan ini, bahasa Indonesia:

1. **VERDICT** — satu kalimat, keputusan sebenarnya, tanpa pagar-pagaran.
2. **Keyakinan** — tinggi/sedang/rendah + kenapa.
3. **Alasan utama** — 2-3, masing-masing berjejak ke bukti.
4. **Apa yang akan membalikkan verdict ini** — konkret dan bisa diuji.
5. **Langkah Senin pagi** — satu tindakan, bisa dikerjakan hari itu.
6. **Yang tidak bisa diputuskan council** — eksplisit.

Bagian 4 dan 6 wajib. Verdict tanpa kondisi pembatalan adalah tebakan berpakaian rapi.`,
  { label: 'chairman', phase: 'Chairman', model: 'fable', effort: 'high' })

return { verdict, advisors: got.map(g => g.key), reviews: reviews.filter(Boolean) }
