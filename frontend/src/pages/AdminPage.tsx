import { ChangeEvent, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileArchive, LoaderCircle, RefreshCw, Upload } from "lucide-react";
import { api, type SubmissionStatus } from "../lib/api";

function StatusBadge({ status }: { status: SubmissionStatus }) {
  return <span className={`status status-${status.toLowerCase()}`}>{status}</span>;
}

type Notice = { kind: "success" | "error"; text: string } | null;
const MAX_ZIP_SIZE = 10 * 1024 * 1024;

export default function AdminPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const submissionsQuery = useQuery({
    queryKey: ["admin-submissions"],
    queryFn: ({ signal }) => api.adminSubmissions(signal)
  });
  const submissions = submissionsQuery.data ?? [];
  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请选择 zip 文件");
      return api.adminImport(file);
    },
    onSuccess: (result) => {
      setNotice({ kind: "success", text: `已导入 ${result.problem.title}，测试点 ${result.imported_cases} 个` });
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      void queryClient.invalidateQueries({ queryKey: ["problems"] });
      void queryClient.invalidateQueries({ queryKey: ["problem"] });
    },
    onError: (error) => {
      setNotice({ kind: "error", text: error instanceof Error ? error.message : "导入失败" });
    }
  });

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFile = event.target.files?.[0] ?? null;
    setNotice(null);
    if (nextFile && !nextFile.name.toLowerCase().endsWith(".zip")) {
      setFile(null);
      event.target.value = "";
      setNotice({ kind: "error", text: "请选择 .zip 格式的题目包" });
      return;
    }
    if (nextFile?.size === 0) {
      setFile(null);
      event.target.value = "";
      setNotice({ kind: "error", text: "Zip 文件不能为空" });
      return;
    }
    if (nextFile && nextFile.size > MAX_ZIP_SIZE) {
      setFile(null);
      event.target.value = "";
      setNotice({ kind: "error", text: "Zip 文件不能超过 10 MiB" });
      return;
    }
    setFile(nextFile);
  };

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>管理</h1>
          <p>题目导入与提交查看</p>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void submissionsQuery.refetch()}
          disabled={submissionsQuery.isFetching}
        >
          {submissionsQuery.isFetching
            ? <LoaderCircle className="spin" size={17} aria-hidden="true" />
            : <RefreshCw size={17} aria-hidden="true" />}
          {submissionsQuery.isFetching ? "刷新中…" : "刷新"}
        </button>
      </div>
      <section className="admin-grid">
        <div className="admin-panel">
          <h2>导入题目</h2>
          <label className={`file-picker${uploadMutation.isPending ? " disabled" : ""}`}>
            <FileArchive size={20} aria-hidden="true" />
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              onChange={onFile}
              disabled={uploadMutation.isPending}
            />
            <span title={file?.name}>{file?.name ?? "选择 zip 包"}</span>
            <small>最大 10 MiB</small>
          </label>
          <button
            className="primary-button"
            type="button"
            onClick={() => uploadMutation.mutate()}
            disabled={!file || uploadMutation.isPending}
          >
            <Upload size={17} aria-hidden="true" />
            {uploadMutation.isPending ? "上传中…" : "上传"}
          </button>
          {notice && (
            <div className={notice.kind === "error" ? "error-box" : "info-box"} role={notice.kind === "error" ? "alert" : "status"}>
              {notice.text}
            </div>
          )}
        </div>
        <div className="admin-panel wide" aria-busy={submissionsQuery.isFetching}>
          <h2>最近提交</h2>
          <div className="submission-head" aria-hidden="true">
            <span>状态</span>
            <span>题目</span>
            <span>用户</span>
            <span>得分</span>
          </div>
          {submissionsQuery.isLoading && <div className="empty-row" role="status">加载中…</div>}
          {submissionsQuery.isError && (
            <div className="error-box table-message" role="alert">
              <span>{submissionsQuery.error instanceof Error ? submissionsQuery.error.message : "无法加载提交记录"}</span>
              <button type="button" onClick={() => void submissionsQuery.refetch()}>重试</button>
            </div>
          )}
          <div className="submission-table" role="list" aria-label="最近提交">
            {!submissionsQuery.isError && submissions.map((item) => (
              <div className="submission-row" role="listitem" key={item.id}>
                <span data-label="状态"><span className="visually-hidden">状态：</span><StatusBadge status={item.status} /></span>
                <span data-label="题目" title={item.problem_title}><span className="visually-hidden">题目：</span>{item.problem_title}</span>
                <span data-label="用户" title={item.username}><span className="visually-hidden">用户：</span>{item.username}</span>
                <span data-label="得分"><span className="visually-hidden">得分：</span>{item.score}/{item.max_score}</span>
              </div>
            ))}
          </div>
          {!submissionsQuery.isLoading && !submissionsQuery.isError && submissions.length === 0 && (
            <div className="empty-row">暂无提交</div>
          )}
        </div>
      </section>
    </section>
  );
}
