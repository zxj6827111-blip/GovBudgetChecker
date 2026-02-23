"""
PDF Report Generator

Generates PDF reports from QC findings using WeasyPrint.
Converts Markdown → HTML → PDF with proper Chinese font support.
"""

import logging
from pathlib import Path
from typing import Dict, List, Any
import asyncpg

logger = logging.getLogger(__name__)

# Report output directory
REPORT_DIR = Path("uploads/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


async def generate_pdf_report(run_id: int, conn: asyncpg.Connection) -> str:
    """
    Generate PDF report for a QC run.
    
    Returns: Path to generated PDF file
    """
    from src.qc.runner_v2 import generate_qc_report_markdown, get_finding_drilldown
    
    # Get run info
    run_info = await conn.fetchrow("""
        SELECT r.*, v.document_id, d.fiscal_year, o.org_name
        FROM qc_runs_v2 r
        JOIN fiscal_document_versions v ON r.document_version_id = v.id
        JOIN fiscal_documents d ON v.document_id = d.id
        JOIN org_units o ON d.org_unit_id = o.id
        WHERE r.id = $1
    """, run_id)
    
    if not run_info:
        raise ValueError(f"QC run {run_id} not found")
    
    # Get findings
    findings = await conn.fetch("""
        SELECT f.*, r.severity, r.description as rule_description
        FROM qc_findings_v2 f
        LEFT JOIN qc_rule_definitions_v2 r ON f.rule_key = r.rule_key
        WHERE f.run_id = $1
        ORDER BY 
            CASE f.status 
                WHEN 'fail' THEN 1 
                WHEN 'warn' THEN 2 
                WHEN 'pass' THEN 3 
                WHEN 'skip' THEN 4 
            END,
            f.rule_key
    """, run_id)
    
    # Get drilldowns
    drilldowns = {}
    for f in findings[:10]:  # Limit to 10 for performance
        try:
            drilldowns[f['id']] = await get_finding_drilldown(f['id'])
        except:
            drilldowns[f['id']] = {}
    
    # Generate enhanced markdown
    markdown = _generate_enhanced_markdown(dict(run_info), [dict(f) for f in findings], drilldowns)
    
    # Convert to HTML
    html = _markdown_to_html_with_css(markdown, run_info)
    
    # Generate PDF
    pdf_path = _html_to_pdf(html, run_id)
    
    logger.info(f"Generated PDF report: {pdf_path}")
    return str(pdf_path)


def _generate_enhanced_markdown(run_info: Dict, findings: List[Dict], drilldowns: Dict) -> str:
    """Generate enhanced markdown report with better structure."""
    lines = []
    
    # Header
    lines.append("# 财政预决算 QC 检查报告\n")
    lines.append("---\n")
    
    # Summary section
    lines.append("## 摘要\n")
    lines.append(f"| 项目 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| **单位名称** | {run_info.get('org_name', 'N/A')} |")
    lines.append(f"| **会计年度** | {run_info.get('fiscal_year', 'N/A')} |")
    lines.append(f"| **检查时间** | {run_info['started_at']} |")
    lines.append(f"| **报告编号** | QC-{run_info['id']:04d} |")
    lines.append("")
    
    # Statistics
    pass_count = sum(1 for f in findings if f['status'] == 'pass')
    fail_count = sum(1 for f in findings if f['status'] == 'fail')
    warn_count = sum(1 for f in findings if f['status'] == 'warn')
    skip_count = sum(1 for f in findings if f['status'] == 'skip')
    
    lines.append("### 检查结果统计\n")
    lines.append("| 状态 | 数量 | 占比 |")
    lines.append("|------|------|------|")
    total = len(findings)
    lines.append(f"| ✅ **通过** | {pass_count} | {pass_count/total*100:.1f}% |")
    lines.append(f"| ❌ **失败** | {fail_count} | {fail_count/total*100:.1f}% |")
    lines.append(f"| ⚠️ **警告** | {warn_count} | {warn_count/total*100:.1f}% |")
    lines.append(f"| ⏭️ **跳过** | {skip_count} | {skip_count/total*100:.1f}% |")
    lines.append("")
    
    # Key issues
    if fail_count > 0 or warn_count > 0:
        lines.append("---\n")
        lines.append("## 关键异常\n")
        
        for f in findings:
            if f['status'] in ['fail', 'warn']:
                icon = "❌" if f['status'] == 'fail' else "⚠️"
                lines.append(f"### {icon} {f['rule_key']}: {f.get('rule_description', '')}\n")
                lines.append(f"- **差异**: {f['diff']:.4f}")
                lines.append(f"- **左值**: {f['lhs_value']}")
                lines.append(f"- **右值**: {f['rhs_value']}")
                lines.append(f"- **结论**: {f['message']}")
                lines.append("")
    
    # Full results table
    lines.append("---\n")
    lines.append("## 完整检查结果\n")
    lines.append("| 规则 | 状态 | 描述 | 差异 | 结论 |")
    lines.append("|------|------|------|------|------|")
    
    for f in findings:
        status_icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}.get(f['status'], "?")
        desc = f.get('rule_description', '')[:20]
        diff_str = f"{f['diff']:.4f}" if f['status'] != 'skip' else "N/A"
        msg = f['message'][:40]
        lines.append(f"| {f['rule_key']} | {status_icon} | {desc} | {diff_str} | {msg} |")
    
    lines.append("")
    
    # Evidence section (for first failed finding)
    fail_findings = [f for f in findings if f['status'] == 'fail']
    if fail_findings and drilldowns:
        lines.append("---\n")
        lines.append("## 证据明细（示例）\n")
        
        first_fail = fail_findings[0]
        if first_fail['id'] in drilldowns:
            drill = drilldowns[first_fail['id']]
            lines.append(f"### {first_fail['rule_key']} 证据\n")
            
            if drill.get('cells'):
                lines.append("**涉及单元格**:\n")
                lines.append("| 表号 | 行 | 列 | 内容 |")
                lines.append("|------|----|----|------|")
                for c in drill['cells'][:5]:
                    text = str(c.get('raw_text', ''))[:20]
                    lines.append(f"| {c['table_code']} | {c['row_idx']} | {c['col_idx']} | {text} |")
                lines.append("")
    
    # Footer
    lines.append("---\n")
    lines.append("*报告生成时间: " + str(run_info.get('finished_at', '')) + "*\n")
    lines.append("*检查引擎版本: QC-V3*\n")
    
    return "\n".join(lines)


def _markdown_to_html_with_css(markdown: str, run_info: Dict) -> str:
    """Convert markdown to HTML with CSS styling."""
    try:
        from markdown import markdown as md_to_html
    except ImportError:
        # Fallback: simple HTML wrapper
        html_body = markdown.replace('\n', '<br>')
        return f"<html><body>{html_body}</body></html>"
    
    # Convert markdown to HTML
    html_content = md_to_html(markdown, extensions=['tables', 'fenced_code'])
    
    # Add CSS styling
    css = """
    <style>
        @page {
            size: A4;
            margin: 2cm;
        }
        body {
            font-family: "Microsoft YaHei", "SimHei", sans-serif;
            font-size: 12pt;
            line-height: 1.6;
            color: #333;
        }
        h1 {
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-top: 0;
        }
        h2 {
            color: #34495e;
            border-bottom: 2px solid #95a5a6;
            padding-bottom: 5px;
            margin-top: 30px;
        }
        h3 {
            color: #7f8c8d;
            margin-top: 20px;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 15px 0;
            font-size: 11pt;
        }
        th {
            background-color: #3498db;
            color: white;
            padding: 10px;
            text-align: left;
            font-weight: bold;
        }
        td {
            border: 1px solid #bdc3c7;
            padding: 8px;
        }
        tr:nth-child(even) {
            background-color: #ecf0f1;
        }
        .pass { color: #27ae60; font-weight: bold; }
        .fail { color: #e74c3c; font-weight: bold; }
        .warn { color: #f39c12; font-weight: bold; }
        .skip { color: #95a5a6; }
        hr {
            border: none;
            border-top: 1px solid #bdc3c7;
            margin: 20px 0;
        }
        code {
            background-color: #f4f4f4;
            padding: 2px 5px;
            border-radius: 3px;
            font-family: "Courier New", monospace;
        }
    </style>
    """
    
    # Construct full HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QC检查报告 - {run_info.get('org_name', '')} - {run_info.get('fiscal_year', '')}</title>
    {css}
</head>
<body>
    {html_content}
</body>
</html>"""
    
    return html


def _html_to_pdf(html: str, run_id: int) -> Path:
    """Convert HTML to PDF using WeasyPrint."""
    pdf_filename = f"qc_report_run_{run_id}.pdf"
    pdf_path = REPORT_DIR / pdf_filename
    
    try:
        from weasyprint import HTML, CSS
        
        # Generate PDF
        HTML(string=html).write_pdf(
            pdf_path,
            stylesheets=[CSS(string='@page { size: A4; margin: 2cm; }')]
        )
        
    except ImportError:
        logger.warning("WeasyPrint not available, saving as HTML instead")
        # Fallback: save as HTML
        html_path = REPORT_DIR / f"qc_report_run_{run_id}.html"
        html_path.write_text(html, encoding='utf-8')
        return html_path
    
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        # Fallback: save as HTML
        html_path = REPORT_DIR / f"qc_report_run_{run_id}.html"
        html_path.write_text(html, encoding='utf-8')
        return html_path
    
    return pdf_path
