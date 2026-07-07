import { ChangeEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileArchive, RefreshCw, Upload } from "lucide-react";
import { api } from "../lib/api";

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status.toLowerCase()}`}>{status}</span>;
}

export default function AdminPage() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState("");
  const { data: submissions = [] } = useQuery({ queryKey: ["admin-submissions"], queryFn: api.adminSubmissions });
  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请选择 zip 文件");
      return api.adminImport(file);
    },
    onSuccess: (result) => {
      setMessage(`已导入 ${result.problem.title}，测试点 ${result.imported_cases} 个`);
      queryClient.invalidateQueries({ queryKey: ["problems"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "导入失败")
  });

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.target.files?.[0] ?? null);
    setMessage("");
  };

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>管理</h1>
          <p>题目导入与提交查看</p>
        </div>
        <button className="secondary-button" onClick={() => queryClient.invalidateQueries({ queryKey: ["admin-submissions"] })}>
          <RefreshCw size={17} />
          刷新
        </button>
      </div>
      <section className="admin-grid">
        <div className="admin-panel">
          <h2>导入题目</h2>
          <label className="file-picker">
            <FileArchive size={20} />
            <input type="file" accept=".zip" onChange={onFile} />
            <span>{file?.name ?? "选择 zip 包"}</span>
          </label>
          <button className="primary-button" onClick={() => uploadMutation.mutate()} disabled={uploadMutation.isPending}>
            <Upload size={17} />
            上传
          </button>
          {message && <div className="info-box">{message}</div>}
        </div>
        <div className="admin-panel wide">
          <h2>最近提交</h2>
          <div className="submission-table">
            {submissions.map((item) => (
              <div className="submission-row" key={item.id}>
                <StatusBadge status={item.status} />
                <span>{item.problem_title}</span>
                <span>{item.username}</span>
                <span>{item.score}/{item.max_score}</span>
              </div>
            ))}
            {submissions.length === 0 && <div className="empty-row">暂无提交</div>}
          </div>
        </div>
      </section>
    </section>
  );
}

