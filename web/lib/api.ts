import type { User } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = "Terjadi kesalahan";
    try {
      const data = await res.json();
      detail = data.detail ?? detail;
    } catch {
      // body bukan JSON
    }
    throw new ApiError(res.status, detail);
  }

  return (await res.json()) as T;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface AuthResponse {
  user: User;
}

export async function getMe(): Promise<User> {
  return apiFetch<User>("/api/v1/auth/me");
}

export async function registerUser(input: {
  email: string;
  password: string;
  name: string;
  privacyConsent: boolean;
}): Promise<User> {
  return apiFetch<User>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({
      email: input.email,
      password: input.password,
      name: input.name,
      privacy_consent: input.privacyConsent,
    }),
  });
}

export async function loginUser(input: {
  email: string;
  password: string;
}): Promise<User> {
  return apiFetch<User>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function logoutUser(): Promise<void> {
  await apiFetch<{ status: string }>("/api/v1/auth/logout", {
    method: "POST",
  });
}

// FR-07 (UU PDP): catat persetujuan kebijakan privasi.
export async function grantPrivacyConsent(): Promise<User> {
  return apiFetch<User>("/api/v1/auth/consent", {
    method: "POST",
  });
}

export function googleLoginUrl(): string {
  return `${API_URL}/api/v1/auth/google`;
}

// --- Kuota gratis (FR-06) ---

export interface QuotaInfo {
  limit: number;
  used: number;
  remaining: number;
  reset_date: string;
  credit_balance: number; // FR-11: saldo kredit berbayar
  total_slots: number; // remaining + credit_balance
}

export async function getQuota(): Promise<QuotaInfo> {
  return apiFetch<QuotaInfo>("/api/v1/quota");
}

// --- Jobs (FR-02/03/05) ---

export type JobStatus = "queued" | "processing" | "completed" | "failed";

export interface Job {
  id: string;
  status: JobStatus;
  scale: number;
  output_format: "webp" | "jpeg" | "png";
  face_enhance: boolean;
  denoise: boolean;
  color_enhance: boolean;
  original_name: string;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  result_deleted_at: string | null;
}

export interface JobList {
  items: Job[];
  total: number;
}

export async function createJob(input: {
  file: File;
  scale: number;
  outputFormat: Job["output_format"];
  faceEnhance: boolean;
  denoise: boolean;
  colorEnhance: boolean;
}): Promise<Job> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("scale", String(input.scale));
  form.append("output_format", input.outputFormat);
  form.append("face_enhance", String(input.faceEnhance));
  form.append("denoise", String(input.denoise));
  form.append("color_enhance", String(input.colorEnhance));

  const res = await fetch(`${API_URL}/api/v1/jobs`, {
    method: "POST",
    credentials: "include",
    body: form,
  });

  if (!res.ok) {
    let detail = "Upload gagal";
    try {
      const data = await res.json();
      detail = data.detail ?? detail;
    } catch {
      // body bukan JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as Job;
}

export async function getJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/api/v1/jobs/${jobId}`);
}

// FR-12: batch processing — upload hingga 10 gambar sekaligus.
export async function createBatchJobs(input: {
  files: File[];
  scale: number;
  outputFormat: Job["output_format"];
  faceEnhance: boolean;
  denoise: boolean;
  colorEnhance: boolean;
}): Promise<JobList> {
  const form = new FormData();
  for (const f of input.files) form.append("files", f);
  form.append("scale", String(input.scale));
  form.append("output_format", input.outputFormat);
  form.append("face_enhance", String(input.faceEnhance));
  form.append("denoise", String(input.denoise));
  form.append("color_enhance", String(input.colorEnhance));

  const res = await fetch(`${API_URL}/api/v1/jobs/batch`, {
    method: "POST",
    credentials: "include",
    body: form,
  });

  if (!res.ok) {
    let detail = "Upload batch gagal";
    try {
      const data = await res.json();
      detail = data.detail ?? detail;
    } catch {
      // body bukan JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as JobList;
}

export async function listJobs(limit = 20, offset = 0): Promise<JobList> {
  return apiFetch<JobList>(
    `/api/v1/jobs?limit=${limit}&offset=${offset}`,
  );
}

export async function fetchJobResult(jobId: string): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/v1/jobs/${jobId}/download`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new ApiError(res.status, "Gagal mengunduh hasil");
  }
  return res.blob();
}

// --- Admin (FR-13) ---

export interface AdminStats {
  total_users: number;
  users_today: number;
  total_jobs: number;
  jobs_by_status: Record<string, number>;
  jobs_today: number;
  free_quota_limit: number;
  revenue_idr: number;
}

export interface AdminJob {
  id: string;
  user_email: string | null;
  status: JobStatus;
  scale: number;
  output_format: string;
  original_name: string;
  created_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export async function getAdminStats(): Promise<AdminStats> {
  return apiFetch<AdminStats>("/api/v1/admin/stats");
}

export async function listAdminJobs(
  limit = 20,
  offset = 0,
  email?: string,
): Promise<{ items: AdminJob[]; total: number }> {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (email) q.set("email", email);
  return apiFetch<{ items: AdminJob[]; total: number }>(
    `/api/v1/admin/jobs?${q.toString()}`,
  );
}

// Direktori user untuk admin (FR-13): email, kuota gratis, kredit, consent,
// dan jumlah riwayat job.
export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  provider: string;
  is_active: boolean;
  created_at: string | null;
  privacy_consent_at: string | null;
  quota_used: number;
  quota_limit: number;
  quota_remaining: number;
  credit_balance: number;
  job_count: number;
}

export async function listAdminUsers(
  email?: string,
  limit = 20,
  offset = 0,
): Promise<{ items: AdminUser[]; total: number }> {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (email) q.set("email", email);
  return apiFetch<{ items: AdminUser[]; total: number }>(
    `/api/v1/admin/users?${q.toString()}`,
  );
}

// Detail satu user (profil + kuota/kredit) — halaman detail admin.
export async function getAdminUser(userId: string): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/api/v1/admin/users/${userId}`);
}

// Transaksi kredit (FR-11) milik satu user — halaman detail admin.
export interface AdminTransaction {
  id: string;
  order_id: string;
  provider: string;
  package_slug: string;
  amount_idr: number;
  credits: number;
  status: "pending" | "paid" | "failed" | "expired";
  created_at: string | null;
  paid_at: string | null;
}

export async function listAdminUserTransactions(
  userId: string,
  limit = 20,
  offset = 0,
): Promise<{ items: AdminTransaction[]; total: number }> {
  const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch<{ items: AdminTransaction[]; total: number }>(
    `/api/v1/admin/users/${userId}/transactions?${q.toString()}`,
  );
}

// Alat admin (pengelola/pengembang): reset kuota gratis & hapus job uji.
export interface AdminQuotaResetResult {
  reset: number;
  email: string | null;
}

export async function resetAdminQuota(input: {
  email?: string;
  all?: boolean;
}): Promise<AdminQuotaResetResult> {
  return apiFetch<AdminQuotaResetResult>("/api/v1/admin/quota/reset", {
    method: "POST",
    body: JSON.stringify({ email: input.email ?? null, all: input.all ?? false }),
  });
}

export async function deleteAdminJob(
  jobId: string,
): Promise<{ deleted: boolean; id: string; files_deleted: number }> {
  return apiFetch<{ deleted: boolean; id: string; files_deleted: number }>(
    `/api/v1/admin/jobs/${jobId}`,
    { method: "DELETE" },
  );
}

// Hapus SEMUA job milik satu user + file-nya (halaman detail admin).
export async function deleteAdminUserJobs(
  userId: string,
): Promise<{ deleted: number; files_deleted: number; user_id: string }> {
  return apiFetch<{ deleted: number; files_deleted: number; user_id: string }>(
    `/api/v1/admin/users/${userId}/jobs`,
    { method: "DELETE" },
  );
}

// --- Billing & kredit (FR-11 — Midtrans) ---

export interface BillingPackage {
  slug: string;
  credits: number;
  price_idr: number;
}

export interface BillingPackages {
  credit_balance: number;
  packages: BillingPackage[];
}

export interface CheckoutResult {
  order_id: string;
  snap_token: string;
  redirect_url: string | null;
  credits: number;
  amount_idr: number;
}

export interface BillingTransaction {
  id: string;
  order_id: string;
  package_slug: string;
  amount_idr: number;
  credits: number;
  status: string;
  created_at: string | null;
  paid_at: string | null;
}

export async function getBillingPackages(): Promise<BillingPackages> {
  return apiFetch<BillingPackages>("/api/v1/billing/packages");
}

export async function createCheckout(
  packageSlug: string,
): Promise<CheckoutResult> {
  return apiFetch<CheckoutResult>("/api/v1/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ package_slug: packageSlug }),
  });
}

export async function listBillingTransactions(): Promise<{
  items: BillingTransaction[];
  total: number;
}> {
  return apiFetch<{ items: BillingTransaction[]; total: number }>(
    "/api/v1/billing/transactions",
  );
}

// --- Hak subjek data (NFR-05 / UU PDP) ---

// Ekspor data pribadi: download JSON attachment (profil + riwayat).
export async function exportAccountData(): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/v1/account/export`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new ApiError(res.status, "Gagal mengekspor data");
  }
  return res.blob();
}

// Hapus akun beserta seluruh data (permanen, tidak bisa dibatalkan).
export async function deleteAccount(): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/account`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    throw new ApiError(res.status, "Gagal menghapus akun");
  }
}

