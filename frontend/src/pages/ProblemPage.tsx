import Editor from "@monaco-editor/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useParams } from "react-router-dom";
import { CheckCircle2, Play, Send, XCircle } from "lucide-react";
import { api, Submission } from "../lib/api";
import { createCompletionProvider, parseSchema } from "../lib/sqlCompletion";

type Monaco = typeof import("monaco-editor/esm/vs/editor/editor.api");

const DEFAULT_SQL = "-- 在这里编写 PostgreSQL SQL\nselect 1 as answer;\n";

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status.toLowerCase()}`}>{status}</span>;
}

function MarkdownContent({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>;
}

export default function ProblemPage() {
  const { slug = "" } = useParams();
  const queryClient = useQueryClient();
  const { data: problem } = useQuery({ queryKey: ["problem", slug], queryFn: () => api.problem(slug), enabled: !!slug });
  const { data: history = [] } = useQuery({
    queryKey: ["submissions", slug],
    queryFn: () => api.submissions(slug),
    enabled: !!slug
  });
  const [code, setCode] = useState(DEFAULT_SQL);
  const [activeSubmission, setActiveSubmission] = useState<Submission | null>(null);
  const [monacoInstance, setMonacoInstance] = useState<Monaco | null>(null);

  const refreshSubmission = async (id: number) => {
    const fresh = await api.submission(id);
    setActiveSubmission(fresh);
    queryClient.invalidateQueries({ queryKey: ["submissions", slug] });
    return fresh;
  };

  useEffect(() => {
    if (!activeSubmission || !["PENDING", "RUNNING"].includes(activeSubmission.status)) return;
    const timer = window.setInterval(() => refreshSubmission(activeSubmission.id), 1000);
    return () => window.clearInterval(timer);
  }, [activeSubmission?.id, activeSubmission?.status]);

  useEffect(() => {
    if (!monacoInstance) return;
    const schema = parseSchema(problem?.schema_md ?? "");
    const disposable = monacoInstance.languages.registerCompletionItemProvider(
      "pgsql",
      createCompletionProvider(monacoInstance, schema)
    );
    return () => disposable.dispose();
  }, [monacoInstance, problem?.schema_md]);

  const runMutation = useMutation({
    mutationFn: () => api.run(slug, code),
    onSuccess: (submission) => setActiveSubmission(submission)
  });
  const submitMutation = useMutation({
    mutationFn: () => api.submit(slug, code),
    onSuccess: (submission) => setActiveSubmission(submission)
  });

  return (
    <div className="problem-workspace">
      <section className="statement-pane">
        <div className="statement-header">
          <div>
            <h1>{problem?.title ?? "加载中"}</h1>
            <p>{problem?.slug}</p>
          </div>
          {problem && <span className="difficulty">{problem.difficulty}</span>}
        </div>
        <article className="markdown">
          <MarkdownContent>{problem?.statement_md || ""}</MarkdownContent>
        </article>
        {problem?.schema_md && (
          <section className="schema-block markdown">
            <h2>表结构</h2>
            <MarkdownContent>{problem.schema_md}</MarkdownContent>
          </section>
        )}
        <section className="schema-block">
          <h2>公开测试点</h2>
          {problem?.public_cases?.map((item) => (
            <details key={item.id}>
              <summary>{item.name}</summary>
              <pre>{item.expected_output}</pre>
            </details>
          ))}
        </section>
      </section>
      <section className="editor-pane">
        <div className="editor-frame">
          <Editor
            height="100%"
            defaultLanguage="pgsql"
            value={code}
            onChange={(value) => setCode(value ?? "")}
            onMount={(_editor, monaco) => setMonacoInstance(monaco)}
            options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }}
          />
        </div>
        <section className="result-pane">
          <div className="result-title">
            <h2>结果</h2>
            {activeSubmission && <StatusBadge status={activeSubmission.status} />}
          </div>
          {activeSubmission ? (
            <>
              <div className="score-line">
                {activeSubmission.status === "AC" ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
                {activeSubmission.score} / {activeSubmission.max_score}
              </div>
              <div className="case-list">
                {activeSubmission.results.map((result) => (
                  <details key={result.id} open={!result.is_hidden}>
                    <summary>
                      <StatusBadge status={result.status} />
                      <span>{result.name}</span>
                      <small>{result.runtime_ms}ms</small>
                    </summary>
                    <p>{result.message}</p>
                    {result.actual_output && <pre>{result.actual_output}</pre>}
                  </details>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-row">暂无提交</div>
          )}
        </section>
        <section className="history-pane">
          <h2>历史</h2>
          {history.slice(0, 8).map((item) => (
            <button className="history-row" key={item.id} onClick={() => refreshSubmission(item.id)}>
              <StatusBadge status={item.status} />
              <span>#{item.id}</span>
              <small>{item.mode}</small>
              <small>{item.score}/{item.max_score}</small>
            </button>
          ))}
        </section>
        <div className="editor-actions">
          <button className="secondary-button" onClick={() => runMutation.mutate()} disabled={runMutation.isPending}>
            <Play size={17} />
            运行
          </button>
          <button className="primary-button" onClick={() => submitMutation.mutate()} disabled={submitMutation.isPending}>
            <Send size={17} />
            提交
          </button>
        </div>
      </section>
    </div>
  );
}
