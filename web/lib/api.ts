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

