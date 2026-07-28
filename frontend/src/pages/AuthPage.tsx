import { FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Database, LogIn, UserPlus } from "lucide-react";
import { useLocation } from "wouter";
import { api, setToken } from "../lib/api";

export default function AuthPage() {
  const [, navigate] = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clientError, setClientError] = useState("");
  const authMutation = useMutation({
    mutationFn: ({ action, name, secret }: { action: "login" | "register"; name: string; secret: string }) =>
      action === "login" ? api.login(name, secret) : api.register(name, secret),
    onSuccess: (result) => {
      setToken(result.access_token);
      navigate("/problems", { replace: true });
    }
  });

  const changeMode = (nextMode: "login" | "register") => {
    setMode(nextMode);
    setClientError("");
    authMutation.reset();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setClientError("");
    // Registration uses the canonical modern username form. Login preserves
    // the exact text first so accounts created by V1 with spaces or other
    // legacy characters remain reachable; the backend may fall back to the
    // canonical form only when no exact account exists.
    const submittedUsername = mode === "register"
      ? username.trim().normalize("NFKC")
      : username;
    const usernameLength = Array.from(submittedUsername).length;
    const passwordLength = Array.from(password).length;
    if (!submittedUsername) {
      setClientError("请输入有效的用户名");
      return;
    }
    if (mode === "register" && usernameLength < 3) {
      setClientError("用户名至少需要 3 个字符");
      return;
    }
    if (usernameLength > 64) {
      setClientError("用户名不能超过 64 个字符");
      return;
    }
    if (mode === "register" && passwordLength < 8) {
      setClientError("密码至少需要 8 个字符");
      return;
    }
    if (passwordLength > 128) {
      setClientError("密码不能超过 128 个字符");
      return;
    }
    authMutation.mutate({ action: mode, name: submittedUsername, secret: password });
  };

  const error = clientError || (authMutation.error instanceof Error ? authMutation.error.message : "");

  return (
    <main className="auth-page" id="main-content" tabIndex={-1}>
      <form className="auth-panel" onSubmit={submit} aria-busy={authMutation.isPending}>
        <div className="auth-title">
          <Database size={28} aria-hidden="true" />
          <div>
            <h1>SQL Judge</h1>
            <p>本地 SQL 在线评测</p>
          </div>
        </div>
        <div className="segmented" aria-label="账户操作">
          <button
            type="button"
            className={mode === "login" ? "active" : ""}
            onClick={() => changeMode("login")}
            disabled={authMutation.isPending}
            aria-pressed={mode === "login"}
          >
            登录
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => changeMode("register")}
            disabled={authMutation.isPending}
            aria-pressed={mode === "register"}
          >
            注册
          </button>
        </div>
        <label>
          用户名
          <input
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              if (clientError) setClientError("");
              if (authMutation.isError) authMutation.reset();
            }}
            autoComplete="username"
            autoFocus
            required
            disabled={authMutation.isPending}
          />
        </label>
        <label>
          密码
          <input
            value={password}
            type="password"
            onChange={(event) => {
              setPassword(event.target.value);
              if (clientError) setClientError("");
              if (authMutation.isError) authMutation.reset();
            }}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            disabled={authMutation.isPending}
          />
        </label>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="primary-button" type="submit" disabled={authMutation.isPending}>
          {mode === "login" ? <LogIn size={18} aria-hidden="true" /> : <UserPlus size={18} aria-hidden="true" />}
          {authMutation.isPending ? "处理中…" : mode === "login" ? "登录" : "注册"}
        </button>
      </form>
    </main>
  );
}
