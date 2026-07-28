import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronsLeft, ChevronsRight, Database, LogOut, Shield, TerminalSquare } from "lucide-react";
import { Link, Redirect, Route, Switch, useLocation } from "wouter";
import { api, AUTH_CHANGED_EVENT, AUTH_TOKEN_STORAGE_KEY, clearToken, getToken } from "./lib/api";

const AuthPage = lazy(() => import("./pages/AuthPage"));
const ProblemsPage = lazy(() => import("./pages/ProblemsPage"));
const ProblemPage = lazy(() => import("./pages/ProblemPage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

function RouteLoading({ page = false }: { page?: boolean }) {
  const content = "加载中…";
  return page
    ? <main className="loading-page" role="status" aria-live="polite">{content}</main>
    : <div className="loading-page" role="status" aria-live="polite">{content}</div>;
}

function readCollapsedPreference() {
  try {
    return localStorage.getItem("sidebar_collapsed") === "1";
  } catch {
    return false;
  }
}

function App() {
  const [location, navigate] = useLocation();
  const normalizedLocation = location.length > 1 ? location.replace(/\/+$/, "") : location;
  const problemsActive = normalizedLocation === "/problems" || normalizedLocation.startsWith("/problems/");
  const adminActive = normalizedLocation === "/admin";
  const queryClient = useQueryClient();
  const [authToken, setAuthToken] = useState(getToken);
  const tokenRef = useRef(authToken);
  const [collapsed, setCollapsed] = useState(readCollapsedPreference);

  useEffect(() => {
    const syncToken = () => {
      const nextToken = getToken();
      if (nextToken !== tokenRef.current) {
        tokenRef.current = nextToken;
        queryClient.clear();
        setAuthToken(nextToken);
      }
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === AUTH_TOKEN_STORAGE_KEY || event.key === null) syncToken();
    };
    window.addEventListener(AUTH_CHANGED_EVENT, syncToken);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, syncToken);
      window.removeEventListener("storage", onStorage);
    };
  }, [queryClient]);

  useEffect(() => {
    try {
      localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
    } catch {
      // The preference is optional when storage is unavailable.
    }
  }, [collapsed]);

  const {
    data: user,
    error: userError,
    isLoading: userLoading,
    refetch: refetchUser
  } = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => api.me(signal),
    enabled: Boolean(authToken)
  });
  const shellReady = Boolean(authToken && !userLoading && user);

  useEffect(() => {
    if (!normalizedLocation.startsWith("/problems/")) {
      const pageName = normalizedLocation === "/auth"
        ? "登录"
        : normalizedLocation === "/admin"
          ? "管理"
          : normalizedLocation === "/problems"
            ? "题库"
            : "SQL Judge";
      document.title = pageName === "SQL Judge" ? pageName : `${pageName} · SQL Judge`;
    }
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      if (shellReady) document.getElementById("main-content")?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [location, normalizedLocation, shellReady]);

  const logout = () => {
    clearToken();
    navigate("/auth", { replace: true });
  };

  if (!authToken) {
    return (
      <Suspense fallback={<RouteLoading page />}>
        <Switch>
          <Route path="/auth"><AuthPage /></Route>
          <Route><Redirect to="/auth" replace /></Route>
        </Switch>
      </Suspense>
    );
  }

  if (userLoading) return <RouteLoading page />;

  if (userError || !user) {
    return (
      <main className="loading-page error-page" role="alert">
        <p>{userError instanceof Error ? userError.message : "无法加载账户信息"}</p>
        <div className="inline-actions">
          <button className="primary-button" type="button" onClick={() => void refetchUser()}>
            重试
          </button>
          <button className="secondary-button" type="button" onClick={logout}>
            退出登录
          </button>
        </div>
      </main>
    );
  }

  return (
    <div className={`app-shell${collapsed ? " collapsed" : ""}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar">
        <div className="brand">
          <button
            className="icon-button sidebar-toggle"
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            aria-expanded={!collapsed}
          >
            {collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
          </button>
          <Database size={22} className="brand-icon" aria-hidden="true" />
          <span className="brand-text">SQL Judge</span>
        </div>
        <nav className="nav" aria-label="主导航">
          <Link
            href="/problems"
            className={problemsActive ? "active" : undefined}
            aria-label="题库"
            aria-current={problemsActive ? "page" : undefined}
            title="题库"
          >
            <TerminalSquare size={18} aria-hidden="true" />
            <span className="nav-link-text">题库</span>
          </Link>
          {user.role === "admin" && (
            <Link
              href="/admin"
              className={adminActive ? "active" : undefined}
              aria-label="管理"
              aria-current={adminActive ? "page" : undefined}
              title="管理"
            >
              <Shield size={18} aria-hidden="true" />
              <span className="nav-link-text">管理</span>
            </Link>
          )}
        </nav>
        <div className="account">
          <div className="account-info">
            <strong title={user.username}>{user.username}</strong>
            <span>{user.role === "admin" ? "管理员" : "练习者"}</span>
          </div>
          <button className="icon-button" type="button" onClick={logout} title="退出登录" aria-label="退出登录">
            <LogOut size={18} aria-hidden="true" />
          </button>
        </div>
      </aside>
      <main className="main" id="main-content" tabIndex={-1}>
        <Suspense fallback={<RouteLoading />}>
          <Switch>
            <Route path="/problems"><ProblemsPage /></Route>
            <Route path="/problems/:slug">
              {(params) => <ProblemPage key={params.slug} slug={params.slug} />}
            </Route>
            {user.role === "admin" && <Route path="/admin"><AdminPage /></Route>}
            <Route><Redirect to="/problems" replace /></Route>
          </Switch>
        </Suspense>
      </main>
    </div>
  );
}

export default App;
