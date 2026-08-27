import { WorkspacePlaceholderPage } from "@/components/workspace/WorkspacePlaceholderPage";

export default function UploadPage() {
  return (
    <WorkspacePlaceholderPage
      title="上传中心"
      desc="批量校验 PDF，并在分析前预设或确认关键元数据。"
      implementingTask={5}
    />
  );
}
