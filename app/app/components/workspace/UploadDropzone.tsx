/**
 * UploadDropzone：上传中心拖拽区（Task 5.1）。
 *
 * 「单个文件不超过 X MB」的 X 必须来自真实 `/api/config` 的 max_upload_mb
 * （由父组件 UploadCenterPage 拉取后传入），绝不能照抄原型图设计稿的 200——
 * 系统默认限制是 30MB，用户按提示传 100MB 文件会被直接拒绝，是可验证的用户伤害。
 * maxUploadMb 为 null 表示尚未拉到配置，此时不渲染具体数字（不得显示占位的 200）。
 */
"use client";

import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui";

export interface UploadDropzoneProps {
  onFilesSelected: (files: File[]) => void;
  maxUploadMb: number | null;
  maxUploadPages: number | null;
}

function extractPdfFiles(fileList: FileList | File[]): File[] {
  return Array.from(fileList).filter(
    (file) => file.name.toLowerCase().endsWith(".pdf") || file.type === "application/pdf",
  );
}

export function UploadDropzone({ onFilesSelected, maxUploadMb, maxUploadPages }: UploadDropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      const files = extractPdfFiles(event.dataTransfer.files);
      if (files.length > 0) {
        onFilesSelected(files);
      }
    },
    [onFilesSelected],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = extractPdfFiles(event.target.files ?? []);
      if (files.length > 0) {
        onFilesSelected(files);
      }
      event.target.value = "";
    },
    [onFilesSelected],
  );

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      data-testid="gbc-upload-dropzone"
      data-dragging={isDragging}
      className={`flex flex-col items-center justify-center gap-3 rounded-card border-2 border-dashed p-10 text-center transition-colors ${
        isDragging ? "border-primary-400 bg-primary-100" : "border-primary-200 bg-primary-50"
      }`}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white text-2xl text-primary-600">
        +
      </div>
      <div className="text-base font-medium text-slate-900">拖拽 PDF 到这里，或选择文件</div>
      <div className="text-xs text-slate-500" data-testid="gbc-upload-limit-hint">
        支持文本 PDF、扫描 PDF 和混合 PDF
        {typeof maxUploadMb === "number" ? `；单个文件不超过 ${maxUploadMb} MB` : ""}
        {typeof maxUploadPages === "number" ? `，不超过 ${maxUploadPages} 页` : ""}
      </div>
      <Button variant="primary" onClick={() => inputRef.current?.click()} data-testid="gbc-upload-select-button">
        选择 PDF 文件
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        onChange={handleInputChange}
        className="sr-only"
        data-testid="gbc-upload-file-input"
      />
    </div>
  );
}

export default UploadDropzone;
