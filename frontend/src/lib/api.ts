const API_BASE = "";
const TOKEN_KEY = "sql_oj_token";

export type User = {
  id: number;
  username: string;
  role: "admin" | "user";
  created_at: string;
};

export type Problem = {
  id: number;
  slug: string;
  title: string;
  statement_md?: string;
  schema_md?: string;
  difficulty: string;
  tags: string[];
  checker: string;
  ordered: boolean;
  time_limit_ms: number;
  is_active: boolean;
  created_at: string;
  accepted: boolean;
  public_cases?: PublicTestCase[];
};

export type PublicTestCase = {
  id: number;
  name: string;
  score: number;
  is_hidden: boolean;
  expected_output?: string | null;
};

export type TestResult = {
  id: number;
  name: string;
  status: string;
  score: number;
  max_score: number;
  runtime_ms: number;
  message?: string | null;
  actual_output?: string | null;
  expected_output?: string | null;
  is_hidden: boolean;
  created_at: string;
};

export type Submission = {
  id: number;
  user_id: number;
  problem_id: number;
  problem_slug: string;
  problem_title: string;
  username: string;
  code: string;
  mode: "run" | "submit";
  status: string;
  score: number;
  max_score: number;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
  results: TestResult[];
};

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body.detail || body.message || message;
    } catch {
      // Ignore non-json errors.
    }
    throw new Error(message);
  }
  return response.json();
}

export const api = {
  register: (username: string, password: string) =>
    apiFetch<{ access_token: string }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  login: (username: string, password: string) =>
    apiFetch<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  me: () => apiFetch<User>("/api/me"),
  problems: () => apiFetch<Problem[]>("/api/problems"),
  problem: (slug: string) => apiFetch<Problem>(`/api/problems/${slug}`),
  run: (slug: string, code: string) =>
    apiFetch<Submission>(`/api/problems/${slug}/run`, {
      method: "POST",
      body: JSON.stringify({ code })
    }),
  submit: (slug: string, code: string) =>
    apiFetch<Submission>(`/api/problems/${slug}/submit`, {
      method: "POST",
      body: JSON.stringify({ code })
    }),
  submission: (id: number) => apiFetch<Submission>(`/api/submissions/${id}`),
  submissions: (problemSlug?: string) =>
    apiFetch<Submission[]>(`/api/submissions${problemSlug ? `?problem_slug=${problemSlug}` : ""}`),
  adminImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<{ imported_cases: number; problem: Problem }>("/api/admin/problems/import", {
      method: "POST",
      body: form
    });
  },
  adminSubmissions: () => apiFetch<Submission[]>("/api/admin/submissions")
};
