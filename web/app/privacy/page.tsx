import Link from "next/link";

export const metadata = { title: "Kebijakan Privasi — JernihAI" };

const sections = [
  {
    title: "Data yang kami proses",
    body: "Kami memproses gambar yang kamu unggah (original dan hasil) serta data akun (email, nama, dan waktu persetujuan privasi) untuk menjalankan layanan peningkatan kualitas gambar.",
  },
  {
    title: "Retensi otomatis (UU PDP)",
    body: "Gambar asli dihapus otomatis dari server setelah 24 jam sejak diunggah. Hasil proses disimpan maksimal 7 hari untuk akun gratis, lalu dihapus otomatis. Kamu bisa mengunduh hasil sebelum masa simpan berakhir.",
  },
  {
    title: "Tidak ada penjualan data",
    body: "Kami tidak menjual, menyewakan, atau membagikan data pribadi dan gambar kamu kepada pihak ketiga tanpa persetujuan.",
  },
  {
    title: "Hak subjek data",
    body: "Sesuai UU No. 27/2022 (PDP), kamu berhak mengakses, mengoreksi, dan meminta penghapusan data kamu. Ajukan permintaan lewat email dukungan JernihAI.",
  },
  {
    title: "Persetujuan",
    body: "Dengan mencentang persetujuan saat mendaftar, kamu menyatakan memahami kebijakan ini. Persetujuan dapat ditarik kapan saja, namun penarikan tidak memengaruhi pemrosesan yang telah dilakukan sebelumnya.",
  },
];

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-100">
      <div className="mx-auto max-w-2xl">
        <nav className="mb-8 text-sm text-slate-400">
          <Link href="/" className="text-indigo-300 transition-colors hover:underline">
            ← Beranda
          </Link>
        </nav>
        <h1 className="text-3xl font-bold">Kebijakan Privasi</h1>
        <p className="mt-2 text-sm text-slate-400">
          Terakhir diperbarui: Agustus 2026
        </p>

        <div className="mt-8 space-y-5">
          {sections.map((s) => (
            <section
              key={s.title}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-5"
            >
              <h2 className="font-semibold text-slate-100">{s.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {s.body}
              </p>
            </section>
          ))}
        </div>

        <p className="mt-8 text-xs text-slate-500">
          Dokumen ini adalah ringkasan kebijakan privasi JernihAI untuk tujuan
          kepatuhan UU No. 27/2022 (PDP).
        </p>
      </div>
    </main>
  );
}
