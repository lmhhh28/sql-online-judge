import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronsLeft, ChevronsRight, Database, LogOut, Shield, TerminalSquare } from "lucide-react";
import { api, clearToken, getToken } from "./lib/api";
import AuthPage from "./pages/AuthPage";
import ProblemsPage from "./pages/ProblemsPage";
import ProblemPage from "./pages/ProblemPage";
import AdminPage from "./pages/AdminPage";

function App() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const hasToken = Boolean(getToken());
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("sidebar_collapsed") === "1"
  );
  useEffect(() => {
    localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
  }, [collapsed]);
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
    <div className={`app-shell${collapsed ? " collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <button
            className="icon-button"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
          >
            {collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
          </button>
          <Database size={22} className="brand-icon" />
          <span className="brand-text">SQL Judge</span>
        </div>
        <nav className="nav">
          <Link to="/problems">
            <TerminalSquare size={18} />
            <span className="nav-link-text">题库</span>
          </Link>
          {user.role === "admin" && (
            <Link to="/admin">
              <Shield size={18} />
              <span className="nav-link-text">管理</span>
            </Link>
          )}
        </nav>
        <div className="account">
          <div className="account-info">
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
