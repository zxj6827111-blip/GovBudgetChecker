export type Severity = 'high' | 'warning' | 'info';

export interface Problem {
  id: string;
  ruleId: string;
  title: string;
  severity: Severity;
  category: string;
  page: number;
  location?: string;
  description: string;
  suggestion: string;
  snippet: string;
  evidenceImage: string;
  status: 'pending' | 'resolved' | 'ignored';
  source?: string;
  bbox?: number[];
  jobId?: string;
}

export interface Task {
  id: string;
  filename: string;
  department: string;
  departmentId?: string;
  year: string;
  type: 'budget' | 'final';
  reportLabel: string;
  status: 'analyzing' | 'completed' | 'failed';
  problemCount: number;
  highRiskCount: number;
  updatedAt: string;
  version: number;
  pipeline: {
    parse: 'done' | 'processing' | 'pending';
    extract: 'done' | 'processing' | 'pending';
    review: 'done' | 'processing' | 'pending';
    report: 'done' | 'processing' | 'pending';
  };
  structuredData: {
    tables: number;
    facts: number;
    psTables: number;
    psRows: number;
    syncStatus: 'synced' | 'pending';
  };
}

export const MOCK_ORGS = [
  { id: 'org-1', name: '市财政局', children: [
    { id: 'org-1-1', name: '预算处' },
    { id: 'org-1-2', name: '国库处' },
  ]},
  { id: 'org-2', name: '市教育局', children: [
    { id: 'org-2-1', name: '财务处' },
    { id: 'org-2-2', name: '高教处' },
  ]},
  { id: 'org-3', name: '市卫健委', children: [] },
];

export const MOCK_TASKS: Task[] = [
  {
    id: 'task-1',
    filename: '2025年市教育局部门预算草案.pdf',
    department: '市教育局',
    year: '2025',
    type: 'budget',
    reportLabel: '部门预算',
    status: 'completed',
    problemCount: 12,
    highRiskCount: 3,
    updatedAt: '2026-03-12 14:30',
    version: 3,
    pipeline: { parse: 'done', extract: 'done', review: 'done', report: 'done' },
    structuredData: { tables: 9, facts: 342, psTables: 2, psRows: 45, syncStatus: 'synced' }
  },
  {
    id: 'task-2',
    filename: '2024年市卫健委部门决算报告.pdf',
    department: '市卫健委',
    year: '2024',
    type: 'final',
    reportLabel: '部门决算',
    status: 'completed',
    problemCount: 5,
    highRiskCount: 0,
    updatedAt: '2026-03-12 10:15',
    version: 1,
    pipeline: { parse: 'done', extract: 'done', review: 'done', report: 'done' },
    structuredData: { tables: 9, facts: 280, psTables: 2, psRows: 30, syncStatus: 'synced' }
  },
  {
    id: 'task-3',
    filename: '2025年市财政局本级预算.pdf',
    department: '市财政局',
    year: '2025',
    type: 'budget',
    reportLabel: '部门预算',
    status: 'analyzing',
    problemCount: 0,
    highRiskCount: 0,
    updatedAt: '2026-03-12 16:00',
    version: 1,
    pipeline: { parse: 'done', extract: 'processing', review: 'pending', report: 'pending' },
    structuredData: { tables: 0, facts: 0, psTables: 0, psRows: 0, syncStatus: 'pending' }
  }
];

export const MOCK_PROBLEMS: Problem[] = [
  {
    id: 'prob-1',
    ruleId: 'CMM-006',
    title: '口径描述矛盾：文中写“财政拨款收入支出增加”，但收入/支出同比方向不一致。这段文字出现了错误：财政拨款收入支出增加（减少）的主要原因是基建项目减少。',
    severity: 'warning',
    category: '文数一致性',
    page: 6,
    description: '口径描述矛盾：文中写“财政拨款收入支出增加”，但收入/支出同比方向不一致。这段文字出现了错误：财政拨款收入支出增加（减少）的主要原因是基建项目减少。',
    suggestion: '请核对上下文，确保文字描述与实际数据增减方向一致。',
    snippet: '财政拨款收入支出增加（减少）的主要原因是基建项目减少。',
    evidenceImage: 'https://picsum.photos/seed/budget1/800/400',
    status: 'pending'
  },
  {
    id: 'prob-2',
    ruleId: 'R-TXT-005',
    title: '文数一致性校验：文字说明与表格数据冲突',
    severity: 'high',
    category: '文数一致性',
    page: 5,
    description: '第一部分“部门基本情况”中描述“本年一般公共预算拨款收入 800 万元”，但《一般公共预算拨款收支表》中显示该项收入为“850 万元”。',
    suggestion: '请修正文字说明，使其与表格数据（850万元）保持一致。',
    snippet: '...本年一般公共预算拨款收入 800 万元，比上年增长...',
    evidenceImage: 'https://picsum.photos/seed/budget2/800/300',
    status: 'pending'
  },
  {
    id: 'prob-3',
    ruleId: 'R-AI-012',
    title: 'AI 智能审查：项目绩效目标描述过于宽泛',
    severity: 'warning',
    category: 'AI 智能分析',
    page: 24,
    description: '“信息化建设专项”的绩效目标描述为“提升信息化水平，保障系统运行”，缺乏可量化的产出指标和效益指标。',
    suggestion: '建议补充具体的量化指标，如“系统可用性达到 99.9%”、“完成 3 个业务模块的升级改造”等。',
    snippet: '项目绩效目标：提升信息化水平，保障系统运行。',
    evidenceImage: 'https://picsum.photos/seed/budget3/800/200',
    status: 'pending'
  },
  {
    id: 'prob-4',
    ruleId: 'R-FMT-002',
    title: '文档规范：缺少法定公开章节',
    severity: 'info',
    category: '基础信息合规',
    page: 2,
    description: '目录及正文中未发现“三公”经费预算安排情况说明的独立章节。',
    suggestion: '按照预决算公开模板要求，需单列“三公”经费增减变化原因说明。',
    snippet: '目录：一、部门概况... 二、收支总体情况... 四、机关运行经费...',
    evidenceImage: 'https://picsum.photos/seed/budget4/800/600',
    status: 'resolved'
  }
];
