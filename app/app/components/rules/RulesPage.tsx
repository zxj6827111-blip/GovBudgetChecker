/**
 * RulesPage：Task 8.3 规则与版本页——M2 版本留痕的第二个前端出口
 * （第一个是 Task 6 审核工作台的元数据 tab，展示单个任务的
 * result.meta.versions；本页展示**当前生效**的规则集与引擎版本）。
 *
 * 数据全部来自 GET /api/rules/version 与 GET /api/rules/entries
 * （Task 7.2 后端补充的只读端点，require_admin，读真实 YAML 与
 * provenance.ENGINE_VERSION）。前端不出现任何硬编码版本号——
 * 原型图里的 "3.8.1" 是设计稿占位，读不到的值一律显示"未识别到"。
 *
 * 关于历史任务用过的版本分布：/api/jobs 列表接口不返回
 * result.meta.versions 字段，逐任务拉详情需要 786 个请求，且实测只有
 * 少数任务带该留痕（M2 之前产生的任务没有），展示价值与成本不成比例，
 * 本页不渲染该分布，只说明去哪里看单个任务的版本留痕。
 */
"use client";

import { useEffect, useState } from "react";

import { Badge, Card, Metric, SectionTitle, Td, Th } from "@/components/ui";

import {
  formatDocScopeText,
  formatRuleCountText,
  formatRulesValueText,
  resolveSeverityTone,
  type RulesEntriesResponse,
  type RulesEntry,
  type RulesVersionResponse,
} from "./rulesPageAdapters";

const ENTRIES_PAGE_SIZE = 50;

export function RulesPage() {
  const [version, setVersion] = useState<RulesVersionResponse | null>(null);
  const [versionFailed, setVersionFailed] = useState(false);
  const [entries, setEntries] = useState<RulesEntry[]>([]);
  const [entriesTotal, setEntriesTotal] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadVersion() {
      try {
        const response = await fetch("/api/rules/version", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setVersionFailed(true);
          }
          return;
        }
        const payload = (await response.json()) as RulesVersionResponse;
        if (!cancelled) {
          setVersion(payload);
          setVersionFailed(false);
        }
      } catch {
        if (!cancelled) {
          setVersionFailed(true);
        }
      }
    }

    async function loadEntries() {
      try {
        const response = await fetch(
          `/api/rules/entries?limit=${ENTRIES_PAGE_SIZE}&offset=0`,
          { cache: "no-store" },
        );
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as RulesEntriesResponse;
        if (!cancelled) {
          setEntries(Array.isArray(payload.items) ? payload.items : []);
          setEntriesTotal(payload.total ?? null);
        }
      } catch {
        // 保持空列表：条目区显示"暂无法获取规则条目"降级态。
      }
    }

    void loadVersion();
    void loadEntries();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="p-8" data-testid="gbc-rules-page">
      <SectionTitle
        title="规则与版本"
        desc="当前生效的规则集、引擎版本与规则条目清单（只读；版本信息来自真实文件解析）。"
      />

      {versionFailed ? (
        <div
          className="mt-4 rounded-card border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800"
          data-testid="gbc-rules-unavailable"
        >
          规则版本端点暂不可用（可能为会话过期或服务暂不可达），以下版本信息显示为&ldquo;未识别到&rdquo;。
        </div>
      ) : null}
      {version?.available === false && version.unavailable_reason ? (
        <div
          className="mt-4 rounded-card border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800"
          data-testid="gbc-rules-unavailable"
        >
          当前生效的规则文件不可读：{version.unavailable_reason}
        </div>
      ) : null}

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="规则集版本"
          value={version ? formatRulesValueText(version.ruleset_version) : null}
          desc={`规则文件：${version ? formatRulesValueText(version.rules_file) : "未识别到"}`}
          tone="primary"
          data-testid="gbc-rules-ruleset-version"
        />
        <Metric
          label="规则修订号"
          value={version ? formatRulesValueText(version.metadata_version) : null}
          desc="规则文件 metadata.version（修订版次）"
          tone="info"
          data-testid="gbc-rules-metadata-version"
        />
        <Metric
          label="规则条目数"
          value={version ? formatRuleCountText(version.rule_entry_count) : null}
          desc="当前生效规则文件内的规则条目总数"
          tone="primary"
          data-testid="gbc-rules-entry-count"
        />
        <Metric
          label="引擎版本"
          value={version ? formatRulesValueText(version.engine_version) : null}
          desc="provenance.ENGINE_VERSION（仓库声明版本，与 finding 留痕同源）"
          tone="success"
          data-testid="gbc-rules-engine-version"
        />
      </div>

      <Card
        title="规则条目清单"
        desc="当前生效规则文件内的规则概要（rule_id / 标题 / 严重级别 / 适用范围）"
        className="mt-8"
        data-testid="gbc-rules-entries-card"
      >
        {entries.length === 0 ? (
          <div
            className="rounded-card border border-dashed border-border bg-surface-100 p-6 text-center text-sm text-slate-500"
            data-testid="gbc-rules-entries-empty"
          >
            {entriesTotal === null
              ? "暂无法获取规则条目（规则文件不可读或端点暂不可用）。"
              : "当前规则文件内没有规则条目。"}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left" data-testid="gbc-rules-entries-table">
              <thead>
                <tr>
                  <Th>规则编号</Th>
                  <Th>标题</Th>
                  <Th>严重级别</Th>
                  <Th>适用范围</Th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.rule_id}
                    data-testid={`gbc-rules-entry-${entry.rule_id}`}
                    className="bg-surface-100"
                  >
                    <Td>
                      <span className="font-medium text-slate-800">{entry.rule_id}</span>
                    </Td>
                    <Td>{entry.title}</Td>
                    <Td>
                      <Badge tone={resolveSeverityTone(entry.severity)}>
                        {entry.severity}
                      </Badge>
                    </Td>
                    <Td>{formatDocScopeText(entry.doc_scope)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="版本留痕说明"
        desc="历史任务的版本信息在哪里看、为什么本页不展示历史版本分布"
        className="mt-8"
        data-testid="gbc-rules-provenance-note"
      >
        <ul className="list-disc space-y-2 pl-5 text-sm leading-6 text-slate-600">
          <li>
            单个任务用了哪个规则/模型版本：在任务详情的「元数据」标签页查看
            （result.meta.versions，M2 版本留痕的第一个前端出口）。
          </li>
          <li>
            历史任务普遍缺少版本留痕字段（M2 之前产生的任务没有该字段），
            覆盖率过低，本页不渲染历史版本分布，避免给人"全量可追溯"的错觉。
          </li>
          <li>
            本页展示的是**当前生效**的规则集与引擎版本，两者来源不同：
            前者来自 RULES_FILE 指向的 YAML 文件，后者来自仓库声明的引擎版本
            （provenance.ENGINE_VERSION），均为只读展示。
          </li>
        </ul>
      </Card>
    </div>
  );
}

export default RulesPage;
