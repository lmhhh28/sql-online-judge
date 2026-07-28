import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "wouter";
import { api } from "../lib/api";

const PAGE_SIZE = 10;
const MAX_SEARCH_LENGTH = 100;

export default function ProblemsPage() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const problemsQuery = useQuery({
    queryKey: ["problems", page, search],
    queryFn: ({ signal }) => api.problems(
      { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, search: search || undefined },
      signal
    ),
    placeholderData: (previous) => previous
  });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setSearch(keyword.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  const problems = problemsQuery.data?.items ?? [];
  const total = problemsQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>题库</h1>
          <p>{problemsQuery.isLoading ? "正在加载…" : `${total} 道题`}</p>
        </div>
        <label className="search">
          <Search size={18} aria-hidden="true" />
          <span className="visually-hidden">搜索题目</span>
          <input
            type="search"
            value={keyword}
            onChange={(event) => {
              setKeyword(Array.from(event.target.value).slice(0, MAX_SEARCH_LENGTH).join(""));
            }}
            placeholder="搜索题目"
          />
        </label>
      </div>
      <div className="problem-table" aria-label="题目列表" aria-busy={problemsQuery.isFetching}>
        <div className="table-head" aria-hidden="true">
          <span>题目</span>
          <span>难度</span>
          <span>标签</span>
          <span></span>
        </div>
        {problemsQuery.isLoading && <div className="empty-row" role="status">加载中…</div>}
        {problemsQuery.isError && (
          <div className="error-box table-message" role="alert">
            <span>{problemsQuery.error instanceof Error ? problemsQuery.error.message : "无法加载题目"}</span>
            <button type="button" onClick={() => void problemsQuery.refetch()}>重试</button>
          </div>
        )}
        {!problemsQuery.isError && problems.map((problem) => (
          <Link className="problem-row" href={`/problems/${encodeURIComponent(problem.slug)}`} key={problem.id}>
            <span>
              <span className="problem-title-line">
                <strong>{problem.title}</strong>
                {problem.accepted && <span className="accepted-badge" title="已通过">AC</span>}
              </span>
              <small>{problem.slug}</small>
            </span>
            <span data-label="难度">
              <span className="visually-hidden">难度：</span>
              {problem.difficulty}
            </span>
            <span className="tags" data-label="标签">
              <span className="visually-hidden">标签：</span>
              {problem.tags.map((tag, index) => <em key={`${tag}-${index}`}>{tag}</em>)}
              {problem.tags.length === 0 && <span className="muted">—</span>}
            </span>
            <ArrowRight size={18} aria-hidden="true" />
          </Link>
        ))}
        {!problemsQuery.isLoading && !problemsQuery.isError && problems.length === 0 && (
          <div className="empty-row">{keyword.trim() ? "没有匹配的题目" : "暂无题目"}</div>
        )}
      </div>
      {pageCount > 1 && !problemsQuery.isError && (
        <nav className="pagination" aria-label="题目分页">
          <button
            className="page-button"
            type="button"
            onClick={() => setPage(safePage - 1)}
            disabled={safePage <= 1}
            title="上一页"
            aria-label="上一页"
          >
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
          <span className="page-info" aria-live="polite">{safePage} / {pageCount}</span>
          <button
            className="page-button"
            type="button"
            onClick={() => setPage(safePage + 1)}
            disabled={safePage >= pageCount}
            title="下一页"
            aria-label="下一页"
          >
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        </nav>
      )}
    </section>
  );
}
