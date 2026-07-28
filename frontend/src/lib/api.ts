const API_BASE = "";
export const AUTH_TOKEN_STORAGE_KEY = "sql_oj_token";

export const AUTH_CHANGED_EVENT = "sql-oj:auth-changed";

export type User = {
  id: number;
  username: string;
  role: "admin" | "user";
  created_at: string;
};

export type ProblemSummary = {
  id: number;
  slug: string;
  title: string;
  difficulty: string;
  tags: string[];
  checker: "result" | "string";
  ordered: boolean;
  time_limit_ms: number;
  is_active: boolean;
  created_at: string;
  accepted: boolean;
};

export type Problem = ProblemSummary & {
  statement_md: string;
  schema_md: string;
  public_cases: PublicTestCase[];
};

export type ProblemPage = {
  items: ProblemSummary[];
  total: number;
};

export type PublicTestCase = {
  id: number;
  name: string;
  score: number;
  is_hidden: boolean;
  expected_output: string | null;
};

export type SubmissionStatus = "PENDING" | "RUNNING" | "AC" | "WA" | "CE" | "RE" | "TLE" | "SE";

export type TestResult = {
  id: number;
  name: string;
  status: SubmissionStatus;
  score: number;
  max_score: number;
  runtime_ms: number;
  message: string | null;
  actual_output: string | null;
  expected_output: string | null;
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
  status: SubmissionStatus;
  score: number;
  max_score: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  results: TestResult[];
};

export type SubmissionSummary = Omit<Submission, "code" | "results" | "error_message">;

export class ApiError extends Error {
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

// `undefined` means storage is authoritative. A string or null is an explicit
// in-memory override used when a browser allows reads but rejects writes.
let tokenOverride: string | null | undefined;

function notifyAuthChanged() {
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function getToken(): string | null {
  if (tokenOverride !== undefined) return tokenOverride;
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string) {
  tokenOverride = token;
  try {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
    tokenOverride = undefined;
  } catch {
    // Keep the token in memory when storage is unavailable.
  }
  notifyAuthChanged();
}

export function clearToken() {
  const hadToken = getToken() !== null;
  tokenOverride = null;
  try {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    tokenOverride = undefined;
  } catch {
    // The in-memory token was still cleared.
  }
  if (hadToken) notifyAuthChanged();
}

function errorMessage(body: unknown, fallback: string): string {
  if (typeof body === "string" && body.trim()) return body;
  if (!body || typeof body !== "object") return fallback;

  const value = body as { detail?: unknown; message?: unknown };
  if (typeof value.detail === "string" && value.detail.trim()) return value.detail;
  if (typeof value.message === "string" && value.message.trim()) return value.message;
  if (Array.isArray(value.detail)) {
    const messages = value.detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const detail = item as { loc?: unknown; msg?: unknown };
        if (typeof detail.msg !== "string") return null;
        const location = Array.isArray(detail.loc) ? detail.loc.slice(1).join(".") : "";
        return location ? `${location}: ${detail.msg}` : detail.msg;
      })
      .filter((item): item is string => Boolean(item));
    if (messages.length > 0) return messages.join("；");
  }
  return fallback;
}

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  inspectResponse?: (response: Response) => void
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  headers.set("Accept", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("无法连接服务器，请确认后端服务正在运行", 0, error);
  }

  // A request issued with an old token may finish after another tab has logged
  // in. Never let that stale 401 erase the newer session.
  if (response.status === 401 && getToken() === token) clearToken();

  if (!response.ok) {
    const fallback = response.statusText || `请求失败（${response.status}）`;
    let body: unknown;
    try {
      const rawBody = await response.text();
      try {
        body = rawBody ? JSON.parse(rawBody) : undefined;
      } catch {
        body = rawBody;
      }
    } catch {
      body = undefined;
    }
    throw new ApiError(errorMessage(body, fallback), response.status, body);
  }

  inspectResponse?.(response);

  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new ApiError("服务器返回了无效响应", response.status, error);
  }
}

function problemPath(slug: string, suffix = "") {
  return `/api/problems/${encodeURIComponent(slug)}${suffix}`;
}

export const api = {
  register: (username: string, password: string) =>
    apiFetch<{ access_token: string; token_type: string }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  login: (username: string, password: string) =>
    apiFetch<{ access_token: string; token_type: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  me: (signal?: AbortSignal) => apiFetch<User>("/api/me", { signal }),
  problems: async (
    { limit, offset, search }: { limit: number; offset: number; search?: string },
    signal?: AbortSignal
  ): Promise<ProblemPage> => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (search) query.set("search", search);
    let total: number | null = null;
    const items = await apiFetch<ProblemSummary[]>(
      `/api/problems?${query.toString()}`,
      { signal },
      (response) => {
        const rawTotal = response.headers.get("X-Total-Count");
        if (rawTotal === null || !/^\d+$/.test(rawTotal)) {
          throw new ApiError("服务器返回了无效的题目总数", response.status, rawTotal);
        }
        const parsedTotal = Number(rawTotal);
        if (!Number.isSafeInteger(parsedTotal)) {
          throw new ApiError("服务器返回的题目总数过大", response.status, rawTotal);
        }
        total = parsedTotal;
      }
    );
    if (total === null) {
      throw new ApiError("服务器响应缺少题目总数", 200);
    }
    return { items, total };
  },
  problem: (slug: string, signal?: AbortSignal) => apiFetch<Problem>(problemPath(slug), { signal }),
  run: (slug: string, code: string) =>
    apiFetch<Submission>(problemPath(slug, "/run"), {
      method: "POST",
      body: JSON.stringify({ code })
    }),
  submit: (slug: string, code: string) =>
    apiFetch<Submission>(problemPath(slug, "/submit"), {
      method: "POST",
      body: JSON.stringify({ code })
    }),
  submission: (id: number, signal?: AbortSignal) =>
    apiFetch<Submission>(`/api/submissions/${id}`, { signal }),
  submissions: (problemSlug?: string, signal?: AbortSignal) => {
    const search = new URLSearchParams();
    if (problemSlug) search.set("problem_slug", problemSlug);
    const query = search.size > 0 ? `?${search.toString()}` : "";
    return apiFetch<SubmissionSummary[]>(`/api/submissions${query}`, { signal });
  },
  adminImport: (file: File) => {
    return apiFetch<{ imported_cases: number; problem: ProblemSummary }>("/api/admin/problems/import", {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file
    });
  },
  adminSubmissions: (signal?: AbortSignal) =>
    apiFetch<SubmissionSummary[]>("/api/admin/submissions", { signal })
};
