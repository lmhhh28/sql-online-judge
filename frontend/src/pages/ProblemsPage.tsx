import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";

const PAGE_SIZE = 10;

export default function ProblemsPage() {
  const { data: problems = [], isLoading } = useQuery({ queryKey: ["problems"], queryFn: api.problems });
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => {
    const value = keyword.trim().toLowerCase();
    if (!value) return problems;
    return problems.filter((problem) => {
      return (
        problem.slug.toLowerCase().includes(value) ||
        problem.title.toLowerCase().includes(value) ||
        problem.tags.join(" ").toLowerCase().includes(value)
      );
    });
  }, [keyword, problems]);

  useEffect(() => setPage(1), [keyword]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>题库</h1>
          <p>{filtered.length} 道题</p>
        </div>
        <label className="search">
          <Search size={18} />
          <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索题目" />
        </label>
      </div>
      <div className="problem-table">
        <div className="table-head">
          <span>题目</span>
          <span>难度</span>
          <span>标签</span>
          <span></span>
        </div>
        {isLoading && <div className="empty-row">加载中</div>}
        {pageItems.map((problem) => (
          <Link className="problem-row" to={`/problems/${problem.slug}`} key={problem.id}>
            <span>
              <span className="problem-title-line">
                <strong>{problem.title}</strong>
                {problem.accepted && <span className="accepted-badge">AC</span>}
              </span>
              <small>{problem.slug}</small>
            </span>
            <span>{problem.difficulty}</span>
            <span className="tags">
              {problem.tags.map((tag) => (
                <em key={tag}>{tag}</em>
              ))}
            </span>
            <ArrowRight size={18} />
          </Link>
        ))}
        {!isLoading && filtered.length === 0 && <div className="empty-row">暂无题目</div>}
      </div>
      {pageCount > 1 && (
        <div className="pagination">
          <button className="page-button" onClick={() => setPage(safePage - 1)} disabled={safePage <= 1} title="上一页">
            <ChevronLeft size={18} />
          </button>
          <span className="page-info">{safePage} / {pageCount}</span>
          <button className="page-button" onClick={() => setPage(safePage + 1)} disabled={safePage >= pageCount} title="下一页">
            <ChevronRight size={18} />
          </button>
        </div>
      )}
    </section>
  );
}
