# API 文档

## 概述

政府预决算检查系统提供RESTful API接口，支持传统规则检查、AI智能分析和双模式融合检测。本文档详细描述了所有可用的API端点、请求格式和响应结构。

## 基础信息

- **基础URL**: `http://localhost:18743`（本机开发默认端口，可用 `GOVBUDGET_BACKEND_PORT` 覆盖）
- **API版本**: v1
- **内容类型**: `application/json` 或 `multipart/form-data`
- **字符编码**: UTF-8

## 认证

当前版本暂不需要认证，后续版本将支持API密钥认证。

## 通用响应格式

### 成功响应
```json
{
  "status": "success",
  "data": {...},
  "message": "操作成功"
}
```

### 错误响应
```json
{
  "status": "error",
  "error": {
    "code": "ERROR_CODE",
    "message": "错误描述",
    "details": {...}
  }
}
```

## API 端点

### 1. 健康检查

检查系统运行状态和各服务可用性。

**端点**: `GET /health`

**响应示例**:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "services": {
    "api": "healthy",
    "ai_service": "healthy",
    "rule_engine": "healthy"
  },
  "version": "2.0.0"
}
```

### 2. 文档分析

核心分析接口，支持多种检测模式。

**端点**: `POST /analyze`

**请求格式**: `multipart/form-data`

**参数**:
- `file` (required): PDF文件，最大50MB
- `mode` (optional): 分析模式，可选值：
  - `dual`: 双模式分析（默认）
  - `ai`: 仅AI分析
  - `local`: 仅规则检查

**请求示例**:
```bash
curl -X POST "http://localhost:18743/analyze" \
  -F "file=@budget_report.pdf" \
  -F "mode=dual"
```

**响应格式**:

#### 双模式分析响应 (`mode=dual`)
```json
{
  "job_id": "job_12345",
  "status": "completed",
  "mode": "dual",
  "dual_mode": {
    "ai_findings": [
      {
        "id": "ai_001",
        "source": "ai",
        "severity": "high",
        "category": "budget_execution",
        "title": "预算执行率异常",
        "message": "第三季度预算执行率为45%，显著低于同期正常水平（70-85%），可能存在预算管理问题",
        "evidence": [
          {
            "type": "text",
            "content": "预算执行率：45%",
            "page": 3,
            "coordinates": {"x": 100, "y": 200, "width": 150, "height": 20}
          }
        ],
        "location": {
          "page": 3,
          "section": "预算执行情况表",
          "coordinates": {"x": 100, "y": 200, "width": 300, "height": 50}
        },
        "suggestions": [
          "建议分析预算执行缓慢的具体原因",
          "制定加快预算执行的改进措施",
          "加强预算执行进度监控"
        ],
        "confidence": 0.92,
        "timestamp": "2024-01-15T10:30:00Z"
      }
    ],
    "rule_findings": [
      {
        "id": "rule_001",
        "source": "rule",
        "rule_id": "BUDGET_EXEC_001",
        "rule_name": "预算执行率检查",
        "severity": "medium",
        "category": "budget_execution",
        "title": "预算执行率偏低",
        "message": "预算执行率45%低于标准阈值60%",
        "evidence": [
          {
            "type": "numeric",
            "field": "execution_rate",
            "value": 0.45,
            "expected_min": 0.60,
            "page": 3
          }
        ],
        "location": {
          "page": 3,
          "section": "预算执行情况表",
          "field": "执行率"
        },
        "rule_details": {
          "condition": "execution_rate < 0.60",
          "threshold": 0.60,
          "operator": "less_than"
        },
        "timestamp": "2024-01-15T10:30:00Z"
      }
    ],
    "merged": {
      "totals": {
        "ai": 15,
        "rule": 12,
        "merged": 20,
        "conflicts": 2,
        "agreements": 7
      },
      "conflicts": [
        {
          "id": "conflict_001",
          "ai_finding": "ai_001",
          "rule_finding": "rule_001",
          "conflict_type": "severity_mismatch",
          "description": "AI检测为高严重性，规则检测为中等严重性",
          "resolution": "采用AI判断，因为包含更多上下文信息",
          "final_severity": "high"
        }
      ],
      "agreements": [
        {
          "id": "agreement_001",
          "ai_finding": "ai_002",
          "rule_finding": "rule_002",
          "agreement_type": "full_match",
          "confidence": 0.95,
          "description": "两种方式都检测到相同的预算科目错误"
        }
      ],
      "summary": {
        "high_severity": 5,
        "medium_severity": 8,
        "low_severity": 7,
        "categories": {
          "budget_execution": 6,
          "format_error": 4,
          "calculation_error": 3,
          "compliance_issue": 7
        }
      }
    }
  },
  "processing_time": {
    "total": 45.2,
    "ai_analysis": 32.1,
    "rule_check": 8.3,
    "merging": 4.8
  },
  "metadata": {
    "file_name": "budget_report.pdf",
    "file_size": 2048576,
    "pages": 25,
    "processed_at": "2024-01-15T10:30:00Z"
  }
}
```

#### AI模式分析响应 (`mode=ai`)
```json
{
  "job_id": "job_12346",
  "status": "completed",
  "mode": "ai",
  "findings": [
    {
      "id": "ai_001",
      "source": "ai",
      "severity": "high",
      "category": "budget_execution",
      "title": "预算执行率异常",
      "message": "第三季度预算执行率为45%，显著低于同期正常水平",
      "evidence": [...],
      "location": {...},
      "suggestions": [...],
      "confidence": 0.92,
      "timestamp": "2024-01-15T10:30:00Z"
    }
  ],
  "summary": {
    "total_issues": 15,
    "high_severity": 3,
    "medium_severity": 7,
    "low_severity": 5,
    "categories": {...}
  },
  "processing_time": {
    "total": 32.1,
    "ai_analysis": 32.1
  },
  "metadata": {...}
}
```

#### 规则模式分析响应 (`mode=local`)
```json
{
  "job_id": "job_12347",
  "status": "completed",
  "mode": "local",
  "findings": [
    {
      "id": "rule_001",
      "source": "rule",
      "rule_id": "BUDGET_EXEC_001",
      "rule_name": "预算执行率检查",
      "severity": "medium",
      "category": "budget_execution",
      "title": "预算执行率偏低",
      "message": "预算执行率45%低于标准阈值60%",
      "evidence": [...],
      "location": {...},
      "rule_details": {...},
      "timestamp": "2024-01-15T10:30:00Z"
    }
  ],
  "summary": {
    "total_issues": 12,
    "rules_triggered": 8,
    "categories": {...}
  },
  "processing_time": {
    "total": 8.3,
    "rule_check": 8.3
  },
  "metadata": {...}
}
```

### 3. 作业状态查询

查询异步分析作业的状态和进度。

**端点**: `GET /jobs/{job_id}/status`

**路径参数**:
- `job_id`: 作业ID

**响应示例**:
```json
{
  "job_id": "job_12345",
  "status": "processing",
  "progress": {
    "current_step": "ai_analysis",
    "percentage": 65,
    "steps": [
      {"name": "file_upload", "status": "completed", "duration": 2.1},
      {"name": "ocr_processing", "status": "completed", "duration": 15.3},
      {"name": "ai_analysis", "status": "processing", "progress": 65},
      {"name": "rule_check", "status": "pending"},
      {"name": "result_merging", "status": "pending"}
    ]
  },
  "estimated_completion": "2024-01-15T10:35:00Z",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**状态值**:
- `pending`: 等待处理
- `processing`: 正在处理
- `completed`: 处理完成
- `failed`: 处理失败
- `cancelled`: 已取消

### 4. 作业结果获取

获取已完成作业的详细结果。

**端点**: `GET /jobs/{job_id}/result`

**路径参数**:
- `job_id`: 作业ID

**查询参数**:
- `format` (optional): 结果格式，可选值：`json`（默认）、`pdf`、`excel`
- `include_evidence` (optional): 是否包含证据详情，默认`true`

**响应**: 与分析接口相同的结果格式

### 5. 作业取消

取消正在处理的作业。

**端点**: `DELETE /jobs/{job_id}`

**路径参数**:
- `job_id`: 作业ID

**响应示例**:
```json
{
  "job_id": "job_12345",
  "status": "cancelled",
  "message": "作业已成功取消"
}
```

### 6. 系统配置

获取系统配置信息。

**端点**: `GET /config`

**响应示例**:
```json
{
  "ai_service": {
    "enabled": true,
    "model": "glm-4-flash",
    "max_tokens": 4000,
    "temperature": 0.1
  },
  "rule_engine": {
    "enabled": true,
    "rules_count": 45,
    "last_updated": "2024-01-10T15:20:00Z"
  },
  "file_limits": {
    "max_size_mb": 50,
    "supported_formats": ["pdf"],
    "max_pages": 100
  },
  "processing": {
    "timeout_seconds": 300,
    "max_concurrent_jobs": 10
  }
}
```

## 数据模型

### Issue (问题)
```typescript
interface Issue {
  id: string;                    // 问题唯一标识
  source: "ai" | "rule";         // 问题来源
  severity: "low" | "medium" | "high";  // 严重程度
  category: string;              // 问题分类
  title: string;                 // 问题标题
  message: string;               // 问题描述
  evidence: Evidence[];          // 证据列表
  location: Location;            // 位置信息
  suggestions?: string[];        // 改进建议（AI模式）
  rule_details?: RuleDetails;    // 规则详情（规则模式）
  confidence?: number;           // 置信度（AI模式）
  timestamp: string;             // 检测时间
}
```

### Evidence (证据)
```typescript
interface Evidence {
  type: "text" | "numeric" | "image";  // 证据类型
  content?: string;              // 文本内容
  value?: number;                // 数值
  expected_min?: number;         // 期望最小值
  expected_max?: number;         // 期望最大值
  page: number;                  // 页码
  coordinates?: Coordinates;     // 坐标信息
}
```

### Location (位置)
```typescript
interface Location {
  page: number;                  // 页码
  section?: string;              // 章节名称
  field?: string;                // 字段名称
  coordinates?: Coordinates;     // 坐标信息
}
```

### Coordinates (坐标)
```typescript
interface Coordinates {
  x: number;                     // X坐标
  y: number;                     // Y坐标
  width: number;                 // 宽度
  height: number;                // 高度
}
```

### RuleDetails (规则详情)
```typescript
interface RuleDetails {
  rule_id: string;               // 规则ID
  rule_name: string;             // 规则名称
  condition: string;             // 触发条件
  threshold?: number;            // 阈值
  operator: string;              // 操作符
}
```

### DualModeResult (双模式结果)
```typescript
interface DualModeResult {
  ai_findings: Issue[];          // AI检测结果
  rule_findings: Issue[];        // 规则检测结果
  merged: MergedResult;          // 合并结果
}
```

### MergedResult (合并结果)
```typescript
interface MergedResult {
  totals: {
    ai: number;                  // AI检测问题数
    rule: number;                // 规则检测问题数
    merged: number;              // 合并后问题数
    conflicts: number;           // 冲突数量
    agreements: number;          // 一致数量
  };
  conflicts: Conflict[];         // 冲突详情
  agreements: Agreement[];       // 一致详情
  summary: IssueSummary;         // 问题汇总
}
```

## 错误代码

| 错误代码 | HTTP状态码 | 描述 |
|---------|-----------|------|
| `INVALID_FILE_FORMAT` | 400 | 不支持的文件格式 |
| `FILE_TOO_LARGE` | 413 | 文件大小超出限制 |
| `FILE_CORRUPTED` | 400 | 文件损坏或无法读取 |
| `MISSING_REQUIRED_FIELD` | 400 | 缺少必需字段 |
| `INVALID_MODE` | 400 | 无效的分析模式 |
| `JOB_NOT_FOUND` | 404 | 作业不存在 |
| `JOB_ALREADY_COMPLETED` | 409 | 作业已完成 |
| `AI_SERVICE_UNAVAILABLE` | 503 | AI服务不可用 |
| `PROCESSING_TIMEOUT` | 408 | 处理超时 |
| `INTERNAL_ERROR` | 500 | 内部服务器错误 |
| `RATE_LIMIT_EXCEEDED` | 429 | 请求频率超限 |

## 使用示例

### Python 客户端示例

```python
import requests
import json

# 基础配置
BASE_URL = "http://localhost:18743"
headers = {"Accept": "application/json"}

# 上传文件进行双模式分析
def analyze_document(file_path, mode="dual"):
    url = f"{BASE_URL}/analyze"
    
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {'mode': mode}
        
        response = requests.post(url, files=files, data=data)
        
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"分析失败: {response.text}")

# 查询作业状态
def check_job_status(job_id):
    url = f"{BASE_URL}/jobs/{job_id}/status"
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"查询失败: {response.text}")

# 获取作业结果
def get_job_result(job_id):
    url = f"{BASE_URL}/jobs/{job_id}/result"
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"获取结果失败: {response.text}")

# 使用示例
if __name__ == "__main__":
    # 分析文档
    result = analyze_document("budget_report.pdf", "dual")
    print(f"分析完成，检测到 {len(result['dual_mode']['merged'])} 个问题")
    
    # 如果是异步处理，查询状态
    if result.get('job_id'):
        job_id = result['job_id']
        
        # 轮询状态直到完成
        import time
        while True:
            status = check_job_status(job_id)
            print(f"处理进度: {status['progress']['percentage']}%")
            
            if status['status'] == 'completed':
                final_result = get_job_result(job_id)
                break
            elif status['status'] == 'failed':
                raise Exception("处理失败")
            
            time.sleep(5)
```

### JavaScript 客户端示例

```javascript
// 分析文档
async function analyzeDocument(file, mode = 'dual') {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', mode);
    
    const response = await fetch('/analyze', {
        method: 'POST',
        body: formData
    });
    
    if (!response.ok) {
        throw new Error(`分析失败: ${response.statusText}`);
    }
    
    return await response.json();
}

// 查询作业状态
async function checkJobStatus(jobId) {
    const response = await fetch(`/jobs/${jobId}/status`);
    
    if (!response.ok) {
        throw new Error(`查询失败: ${response.statusText}`);
    }
    
    return await response.json();
}

// 使用示例
document.getElementById('fileInput').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    
    try {
        const result = await analyzeDocument(file, 'dual');
        console.log('分析结果:', result);
        
        // 显示结果
        displayResults(result);
    } catch (error) {
        console.error('分析失败:', error);
    }
});
```

## 性能考虑

### 请求限制
- 单个文件最大50MB
- 单个文档最多100页
- 并发请求限制：每IP每分钟最多10个请求
- 处理超时：5分钟

### 优化建议
1. **文件预处理**: 压缩PDF文件以减少上传时间
2. **批量处理**: 对于多个文件，使用批量接口
3. **缓存结果**: 相同文件的分析结果会被缓存24小时
4. **异步处理**: 大文件建议使用异步模式

## 版本更新

### v2.0.0 (当前版本)
- 新增双模式分析功能
- 支持AI和规则检测结果合并
- 添加冲突检测和一致性验证
- 优化响应格式和错误处理

### v1.0.0
- 基础分析功能
- 规则检查引擎
- 文件上传和处理

---

**注意**: 本API文档会随着系统更新而持续完善，请关注版本变更说明。

## 可信 PDF 事实及分析版本接口

以下接口沿用任务访问权限；历史/候选读取不会切换当前版本。新分析使用数据库当前事实指针作为权威，文件为可重试镜像。

- `GET /api/jobs/{job_id}/fact-snapshot?view=raw|effective&revision_id=...&offset=0&limit=1000`：读取指定不可变快照的原始或有效事实；省略 `revision_id` 时读取当前版本。返回快照、事实摘要及分页信息。人工列映射前的原始解释有独立来源摘要，不能用校正后结果替换。
- `GET /api/jobs/{job_id}/analysis-versions`：列出可读取的当前、历史及候选分析，包含版本状态及当前版本标志。
- `GET /api/jobs/{job_id}?analysis_revision=...`：查看该版本的任务及报告；不把历史结果与当前结构化查询拼接。
- `GET /api/jobs/{job_id}/preserved-analyses`：列出旧协议任务重分析前按原始字节归档的文件。
- `GET /api/jobs/{job_id}/preserved-analyses/{archive_id}/{filename}`：只读下载白名单内且摘要校验通过的历史文件。
- 报告下载沿用 `analysis_revision` 参数，JSON/CSV/Word/PDF 必须读取同一版本。摘要校验失败、未知版本、无权限须拒绝读取，不能静默回退当前报告。

`check_coverage` 仍表示检查完成率。`result.meta.recognition_state` 只报告已恢复、待裁定、待处理及存储一致性；没有独立逐格真值时 `recognition_accuracy` 和正确自动恢复覆盖率为空值。

OCR 未完成或服务暂不可用时任务保持 `queued`，携带 `ocr_pending` 和 `retry_not_before`。文档单轮软预算默认 600 秒，到限保存检查点并后台续跑；未开始区域不计作完成。实际恢复时间由页数、块数和本机资源决定，不适用上文基础接口的简化超时描述。

历史候选重建与发布工具为 `scripts/rebuild_trusted_pdf_history.py`。`inventory/build/resume` 不切换当前结果；`publish` 要求与原件及快照摘要绑定的明确验收记录，每批最多 20 份。`rollback/sync` 用于撤回及文件镜像重试，原件、旧快照和人工记录保留。操作前应备份并核对验收库，不能对未验收候选自动发布。
