import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CheckCircle2, LoaderCircle, Play, Send, XCircle } from "lucide-react";
import { api, type Submission, type SubmissionStatus } from "../lib/api";

const SqlEditor = lazy(() => import("../components/SqlEditor"));

const DEFAULT_SQL = "-- 在这里编写 PostgreSQL SQL\nselect 1 as answer;\n";
const MAX_CODE_LENGTH = 50_000;
const ACTIVE_STATUSES = new Set<SubmissionStatus>(["PENDING", "RUNNING"]);

const MARKDOWN_COMPONENTS: Components = {
  h1: ({ node: _node, ...props }) => <h2 {...props} />,
  h2: ({ node: _node, ...props }) => <h3 {...props} />,
  h3: ({ node: _node, ...props }) => <h4 {...props} />,
  h4: ({ node: _node, ...props }) => <h5 {...props} />,
  h5: ({ node: _node, ...props }) => <h6 {...props} />,
  a: ({ node: _node, href, children, ...props }) =>
    href ? <a href={href} rel="noreferrer" {...props}>{children}</a> : <span>{children}</span>,
  // Problem packages do not carry image assets. Rendering arbitrary remote
  // image URLs would turn a statement view into a third-party tracking ping.
  img: ({ node: _node, alt }) => (
    <span className="muted" role="img" aria-label={alt ? `图片：${alt}` : "已禁用的远程图片"}>
      {alt ? `（图片：${alt}）` : "（远程图片已禁用）"}
    </span>
  )
};

function StatusBadge({ status }: { status: SubmissionStatus }) {
  return <span className={`status status-${status.toLowerCase()}`}>{status}</span>;
}

function MarkdownContent({ children }: { children: string }) {
  return (
    <ReactMarkdown components={MARKDOWN_COMPONENTS} remarkPlugins={[remarkGfm]} skipHtml>
      {children}
    </ReactMarkdown>
  );
}

function OutputBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="output-block">
      <h3>{label}</h3>
      <pre>{value === "" ? "（空输出）" : value}</pre>
    </div>
  );
}

function SubmissionIcon({ status }: { status: SubmissionStatus }) {
  if (status === "AC") return <CheckCircle2 size={18} aria-hidden="true" />;
  if (ACTIVE_STATUSES.has(status)) return <LoaderCircle className="spin" size={18} aria-hidden="true" />;
  return <XCircle size={18} aria-hidden="true" />;
}

export default function ProblemPage({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState(DEFAULT_SQL);
  const [activeSubmissionId, setActiveSubmissionId] = useState<number | null>(null);
  const [clientError, setClientError] = useState("");
  const completedSubmissionRef = useRef<number | null>(null);
  const activeHistoryIdsRef = useRef<Set<number>>(new Set());
  const acceptanceSyncRef = useRef(false);
  const codeLength = Array.from(code).length;

  const problemQuery = useQuery({
    queryKey: ["problem", slug],
    queryFn: ({ signal }) => api.problem(slug, signal)
  });
  const historyQuery = useQuery({
    queryKey: ["submissions", slug],
    queryFn: ({ signal }) => api.submissions(slug, signal),
    refetchInterval: (query) => (
      query.state.data?.some((item) => ACTIVE_STATUSES.has(item.status)) ? 1000 : false
    )
  });
  const activeQuery = useQuery({
    queryKey: ["submission", activeSubmissionId],
    queryFn: ({ signal }) => api.submission(activeSubmissionId as number, signal),
    enabled: activeSubmissionId !== null,
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const submission = query.state.data;
      return submission && ACTIVE_STATUSES.has(submission.status) ? 1000 : false;
    }
  });

  const selectSubmission = (submission: Submission) => {
    queryClient.setQueryData(["submission", submission.id], submission);
    void queryClient.invalidateQueries({ queryKey: ["submissions", slug] });
    completedSubmissionRef.current = null;
    setActiveSubmissionId(submission.id);
  };

  const createMutation = useMutation({
    mutationFn: ({ mode, sql }: { mode: "run" | "submit"; sql: string }) =>
      mode === "run" ? api.run(slug, sql) : api.submit(slug, sql),
    onSuccess: selectSubmission
  });

  const activeSubmission = activeQuery.data ?? null;
  const acceptedFromHistory = Boolean(
    historyQuery.data?.some((item) => item.mode === "submit" && item.status === "AC")
  );
  useEffect(() => {
    document.title = problemQuery.data
      ? `${problemQuery.data.title} · SQL Judge`
      : "题目 · SQL Judge";
  }, [problemQuery.data, slug]);

  useEffect(() => {
    if (activeSubmissionId !== null) return;
    const activeHistoryItem = historyQuery.data?.find((item) => ACTIVE_STATUSES.has(item.status));
    if (activeHistoryItem) setActiveSubmissionId(activeHistoryItem.id);
  }, [activeSubmissionId, historyQuery.data]);

  useEffect(() => {
    if (!historyQuery.data) return;
    const nextActiveIds = new Set(
      historyQuery.data
        .filter((item) => ACTIVE_STATUSES.has(item.status))
        .map((item) => item.id)
    );
    const completedItems = historyQuery.data.filter(
      (item) => activeHistoryIdsRef.current.has(item.id) && !nextActiveIds.has(item.id)
    );
    activeHistoryIdsRef.current = nextActiveIds;
    if (completedItems.some((item) => item.mode === "submit" && item.status === "AC")) {
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      void queryClient.invalidateQueries({ queryKey: ["problem", slug] });
    }
  }, [historyQuery.data, queryClient, slug]);

  useEffect(() => {
    if (
      acceptanceSyncRef.current ||
      !acceptedFromHistory ||
      !problemQuery.data ||
      problemQuery.data.accepted
    ) return;
    acceptanceSyncRef.current = true;
    void queryClient.invalidateQueries({ queryKey: ["problems"] });
    void queryClient.invalidateQueries({ queryKey: ["problem", slug] });
  }, [acceptedFromHistory, problemQuery.data, queryClient, slug]);

  useEffect(() => {
    if (!activeSubmission || ACTIVE_STATUSES.has(activeSubmission.status)) return;
    if (completedSubmissionRef.current === activeSubmission.id) return;
    completedSubmissionRef.current = activeSubmission.id;
    void queryClient.invalidateQueries({ queryKey: ["submissions", slug] });
    if (activeSubmission.mode === "submit" && activeSubmission.status === "AC") {
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      void queryClient.invalidateQueries({ queryKey: ["problem", slug] });
    }
  }, [activeSubmission, queryClient, slug]);

  const startSubmission = (mode: "run" | "submit") => {
    setClientError("");
    if (mode === "run" && problemQuery.data?.public_cases.length === 0) {
      setClientError("该题没有公开测试点，无法运行；仍可直接提交");
      return;
    }
    if (!code.trim()) {
      setClientError("请输入 SQL 后再运行或提交");
      return;
    }
    if (codeLength > MAX_CODE_LENGTH) {
      setClientError(`SQL 不能超过 ${MAX_CODE_LENGTH.toLocaleString()} 个字符`);
      return;
    }
    createMutation.mutate({ mode, sql: code });
  };

  if (problemQuery.isLoading) {
    return <section className="content-state" role="status">正在加载题目…</section>;
  }

  if (problemQuery.isError || !problemQuery.data) {
    return (
      <section className="content-state" role="alert">
        <p>{problemQuery.error instanceof Error ? problemQuery.error.message : "无法加载题目"}</p>
        <button className="primary-button" type="button" onClick={() => void problemQuery.refetch()}>
          重试
        </button>
      </section>
    );
  }

  const problem = problemQuery.data;
  const requestError = clientError ||
    (createMutation.error instanceof Error ? createMutation.error.message : "");

  return (
    <div className="problem-workspace">
      <section className="statement-pane">
        <div className="statement-header">
          <div>
            <div className="title-with-status">
              <h1>{problem.title}</h1>
              {(problem.accepted || acceptedFromHistory) && <span className="accepted-badge">AC</span>}
            </div>
            <p>{problem.slug}</p>
          </div>
          <span className="difficulty">{problem.difficulty}</span>
        </div>
        <article className="markdown">
          {problem.statement_md.trim()
            ? <MarkdownContent>{problem.statement_md}</MarkdownContent>
            : <p className="muted">暂无题目说明</p>}
        </article>
        {problem.schema_md && (
          <section className="schema-block markdown">
            <h2>表结构</h2>
            <MarkdownContent>{problem.schema_md}</MarkdownContent>
          </section>
        )}
        <section className="schema-block">
          <h2>公开测试点</h2>
          {problem.public_cases.map((item) => (
            <details key={item.id}>
              <summary>{item.name}（{item.score} 分）</summary>
              <pre>{item.expected_output ?? "（未提供期望输出）"}</pre>
            </details>
          ))}
          {problem.public_cases.length === 0 && <div className="empty-row">暂无公开测试点</div>}
        </section>
      </section>
      <section className="editor-pane" aria-label="SQL 作答区">
        <div className="editor-frame">
          <Suspense fallback={<div className="empty-row" role="status">正在加载编辑器…</div>}>
            <SqlEditor
              value={code}
              schemaMarkdown={problem.schema_md}
              onChange={(value) => {
                setCode(value);
                if (clientError) setClientError("");
                if (createMutation.isError) createMutation.reset();
              }}
            />
          </Suspense>
        </div>
        <div className={`code-count${codeLength > MAX_CODE_LENGTH ? " invalid" : ""}`}>
          {codeLength.toLocaleString()} / {MAX_CODE_LENGTH.toLocaleString()} 字符
        </div>
        <section className="result-pane" aria-busy={activeQuery.isFetching}>
          <div className="result-title">
            <h2>结果</h2>
            {activeSubmission && <StatusBadge status={activeSubmission.status} />}
          </div>
          {requestError && <div className="error-box" role="alert">{requestError}</div>}
          {activeQuery.isError && (
            <div className="error-box retry-message" role="alert">
              <span>{activeQuery.error instanceof Error ? activeQuery.error.message : "无法刷新评测结果"}</span>
              <button type="button" onClick={() => void activeQuery.refetch()}>重试</button>
            </div>
          )}
          {activeSubmission ? (
            <>
              <div className="score-line">
                <SubmissionIcon status={activeSubmission.status} />
                {activeSubmission.score} / {activeSubmission.max_score}
              </div>
              {activeSubmission.error_message && (
                <div className="error-box" role="alert">{activeSubmission.error_message}</div>
              )}
              <div className="case-list">
                {activeSubmission.results.map((result) => (
                  <details key={result.id} open={!result.is_hidden}>
                    <summary>
                      <StatusBadge status={result.status} />
                      <span>{result.name}</span>
                      <small>{result.runtime_ms}ms · {result.score}/{result.max_score}</small>
                    </summary>
                    {result.message && <p>{result.message}</p>}
                    {result.actual_output !== null && <OutputBlock label="实际输出" value={result.actual_output} />}
                    {result.expected_output !== null && <OutputBlock label="期望输出" value={result.expected_output} />}
                  </details>
                ))}
                {ACTIVE_STATUSES.has(activeSubmission.status) && activeSubmission.results.length === 0 && (
                  <div className="empty-row" role="status">评测中…</div>
                )}
              </div>
            </>
          ) : (
            <div className="empty-row">暂无提交</div>
          )}
        </section>
        <section className="history-pane" aria-busy={historyQuery.isFetching}>
          <div className="history-title">
            <h2>历史</h2>
            {historyQuery.isFetching && <LoaderCircle className="spin" size={16} aria-label="正在刷新" />}
          </div>
          {historyQuery.isError && (
            <div className="error-box history-message" role="alert">
              <span>{historyQuery.error instanceof Error ? historyQuery.error.message : "无法加载历史提交"}</span>
              <button type="button" onClick={() => void historyQuery.refetch()}>重试</button>
            </div>
          )}
          {historyQuery.data?.slice(0, 8).map((item) => (
            <button
              className={`history-row${item.id === activeSubmissionId ? " active" : ""}`}
              type="button"
              key={item.id}
              onClick={() => {
                completedSubmissionRef.current = null;
                setActiveSubmissionId(item.id);
              }}
              aria-pressed={item.id === activeSubmissionId}
            >
              <StatusBadge status={item.status} />
              <span>#{item.id}</span>
              <small>{item.mode === "run" ? "运行" : "提交"}</small>
              <small>{item.score}/{item.max_score}</small>
            </button>
          ))}
          {!historyQuery.isLoading && !historyQuery.isError && historyQuery.data?.length === 0 && (
            <div className="empty-row history-message">暂无历史提交</div>
          )}
        </section>
        <div className="editor-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => startSubmission("run")}
            disabled={createMutation.isPending || problem.public_cases.length === 0}
            title={problem.public_cases.length === 0 ? "该题没有公开测试点" : undefined}
          >
            <Play size={17} aria-hidden="true" />
            {createMutation.isPending && createMutation.variables?.mode === "run" ? "请求中…" : "运行"}
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={() => startSubmission("submit")}
            disabled={createMutation.isPending}
          >
            <Send size={17} aria-hidden="true" />
            {createMutation.isPending && createMutation.variables?.mode === "submit" ? "请求中…" : "提交"}
          </button>
        </div>
      </section>
    </div>
  );
}
