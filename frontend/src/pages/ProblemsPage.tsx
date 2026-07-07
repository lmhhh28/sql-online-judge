import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../lib/api";

export default function ProblemsPage() {
  const { data: problems = [], isLoading } = useQuery({ queryKey: ["problems"], queryFn: api.problems });
  const [keyword, setKeyword] = useState("");
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

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>题库</h1>
          <p>{problems.length} 道题</p>
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
        {filtered.map((problem) => (
          <Link className="problem-row" to={`/problems/${problem.slug}`} key={problem.id}>
            <span>
              <strong>{problem.title}</strong>
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
    </section>
  );
}

