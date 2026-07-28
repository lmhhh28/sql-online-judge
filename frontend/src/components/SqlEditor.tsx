import Editor from "@monaco-editor/react";
import { useEffect } from "react";
import monaco from "../lib/monaco";
import { createCompletionProvider, parseSchema } from "../lib/sqlCompletion";

type SqlEditorProps = {
  value: string;
  schemaMarkdown: string;
  onChange: (value: string) => void;
};

export default function SqlEditor({ value, schemaMarkdown, onChange }: SqlEditorProps) {
  useEffect(() => {
    const disposable = monaco.languages.registerCompletionItemProvider(
      "pgsql",
      createCompletionProvider(monaco, parseSchema(schemaMarkdown))
    );
    return () => disposable.dispose();
  }, [schemaMarkdown]);

  return (
    <Editor
      height="100%"
      defaultLanguage="pgsql"
      value={value}
      onChange={(nextValue) => onChange(nextValue ?? "")}
      loading={<div className="empty-row" role="status">正在加载编辑器…</div>}
      options={{
        acceptSuggestionOnEnter: "off",
        ariaLabel: "SQL 编辑器",
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 14,
        scrollBeyondLastLine: false,
        tabCompletion: "on"
      }}
    />
  );
}
