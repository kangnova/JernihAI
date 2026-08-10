import type { JobStatus } from "@/lib/api";

// Label & gaya badge status job — dipakai halaman /admin dan detail user.
export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "Antre",
  processing: "Memproses",
  completed: "Selesai",
  failed: "Gagal",
};

export const STATUS_STYLE: Record<JobStatus, string> = {
  queued: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  processing: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
};

// Status transaksi kredit (FR-11).
export type TxnStatus = "pending" | "paid" | "failed" | "expired";

export const TXN_STATUS_LABEL: Record<TxnStatus, string> = {
  pending: "Menunggu",
  paid: "Dibayar",
  failed: "Gagal",
  expired: "Kedaluwarsa",
};

export const TXN_STATUS_STYLE: Record<TxnStatus, string> = {
  pending: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  paid: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  expired: "border-slate-400/30 bg-slate-400/10 text-slate-300",
};

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
