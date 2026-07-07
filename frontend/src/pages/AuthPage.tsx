import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Database, LogIn, UserPlus } from "lucide-react";
import { api, setToken } from "../lib/api";

export default function AuthPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = mode === "login" ? await api.login(username, password) : await api.register(username, password);
      setToken(result.access_token);
      navigate("/problems");
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="auth-page">
      <form className="auth-panel" onSubmit={submit}>
        <div className="auth-title">
          <Database size={28} />
          <div>
            <h1>SQL Judge</h1>
            <p>本地 SQL 在线评测</p>
          </div>
        </div>
        <div className="segmented">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
            登录
          </button>
          <button type="button" className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
            注册
          </button>
        </div>
        <label>
          用户名
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label>
          密码
          <input
            value={password}
            type="password"
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </label>
        {error && <div className="error-box">{error}</div>}
        <button className="primary-button" disabled={loading}>
          {mode === "login" ? <LogIn size={18} /> : <UserPlus size={18} />}
          {loading ? "处理中" : mode === "login" ? "登录" : "注册"}
        </button>
      </form>
    </main>
  );
}

