import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, LogOut, Shield, TerminalSquare } from "lucide-react";
import { api, clearToken, getToken } from "./lib/api";
import AuthPage from "./pages/AuthPage";
import ProblemsPage from "./pages/ProblemsPage";
import ProblemPage from "./pages/ProblemPage";
import AdminPage from "./pages/AdminPage";

function App() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const hasToken = Boolean(getToken());
  const { data: user, isError: userError, isLoading: userLoading } = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    enabled: hasToken
  });

  const logout = () => {
    clearToken();
    queryClient.clear();
    navigate("/auth");
  };

  if (!hasToken) {
    return (
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="*" element={<Navigate to="/auth" replace />} />
      </Routes>
    );
  }

  if (userError) {
    clearToken();
    return <Navigate to="/auth" replace />;
  }

  if (userLoading || !user) {
    return <main className="loading-page">加载中</main>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Database size={22} />
          <span>SQL Judge</span>
        </div>
        <nav className="nav">
          <Link to="/problems">
            <TerminalSquare size={18} />
            题库
          </Link>
          {user.role === "admin" && (
            <Link to="/admin">
              <Shield size={18} />
              管理
            </Link>
          )}
        </nav>
        <div className="account">
          <div>
            <strong>{user.username}</strong>
            <span>{user.role === "admin" ? "管理员" : "练习者"}</span>
          </div>
          <button className="icon-button" onClick={logout} title="退出登录">
            <LogOut size={18} />
          </button>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/problems" element={<ProblemsPage />} />
          <Route path="/problems/:slug" element={<ProblemPage />} />
          <Route path="/admin" element={user.role === "admin" ? <AdminPage /> : <Navigate to="/problems" replace />} />
          <Route path="*" element={<Navigate to="/problems" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
