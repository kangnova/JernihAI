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
}): Promise<User> {
  return apiFetch<User>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
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

export function googleLoginUrl(): string {
  return `${API_URL}/api/v1/auth/google`;
}
