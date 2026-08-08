import { AuthForm } from "@/components/AuthForm";

export const metadata = { title: "Daftar — JernihAI" };

export default function RegisterPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-100">
      <AuthForm mode="register" />
    </main>
  );
}
