#!/usr/bin/env python3
"""Turtle Investment Framework - Complete Analysis Pipeline.

Executes the full turtle analysis workflow for a given stock:
  Phase 0: PDF auto-download (optional)
  Phase 1A: Tushare data collection
  Phase 1B: WebSearch supplement (requires Agent)
  Phase 2A: PDF preprocessing (optional)
  Phase 2B: PDF extraction (requires Agent)
  Phase 3: Factor analysis and report generation (requires Agent)

Usage:
    python scripts/turtle_analysis.py --code 600887
    python scripts/turtle_analysis.py --code 600887.SH --pdf path/to/report.pdf
    python scripts/turtle_analysis.py --code 00700.HK --channel hkconnect --download-pdf
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from config import get_token, get_api_url, validate_stock_code, check_local_pdf
import tushare as ts


def get_company_name(ts_code: str) -> str:
    """Fetch company name from Tushare stock_basic API.
    
    Args:
        ts_code: Stock code (e.g., '600096.SH', '00700.HK')
    
    Returns:
        Company name string, or empty string if not found
    """
    try:
        token = get_token()
        pro = ts.pro_api(token)
        
        if ts_code.endswith('.HK'):
            df = pro.hk_basic(ts_code=ts_code, fields='ts_code,name')
        else:
            df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name')
        
        if not df.empty and 'name' in df.columns:
            return str(df.iloc[0]['name'])
    except Exception as e:
        print(f"  [WARN] 获取公司名称失败: {e}")
    
    return ""


def copy_local_pdf(local_path: str, output_dir: Path, ts_code: str, year: int = None) -> tuple[bool, str]:
    """Copy local PDF file to output directory with standardized naming.
    
    Args:
        local_path: Path to local PDF file
        output_dir: Output directory to copy to
        ts_code: Stock code for naming
        year: Year for naming (default: latest available)
    
    Returns:
        (success, new_path or error_message)
    """
    if year is None:
        year = datetime.now().year - 1
        if datetime.now().month < 4:
            year -= 1
    
    code = ts_code.split('.')[0]
    
    if not os.path.exists(local_path):
        return False, f"本地 PDF 文件不存在: {local_path}"
    
    filename = f"{code}_{year}_年报.pdf"
    dest_path = output_dir / filename
    
    try:
        shutil.copy2(local_path, str(dest_path))
        filesize = os.path.getsize(dest_path)
        return True, str(dest_path)
    except Exception as e:
        return False, f"复制 PDF 文件失败: {e}"


def search_report_url(stock_code: str, year: int = None) -> dict:
    """Search for annual report PDF URL from multiple sources.
    
    Args:
        stock_code: Stock code (e.g., '600887.SH', '01378.HK')
        year: Target year (default: latest available)
    
    Returns:
        dict with keys: 'url', 'source', 'title', 'year'
    """
    if year is None:
        year = datetime.now().year - 1
        if datetime.now().month < 4:
            year -= 1
    
    code = stock_code.split('.')[0]
    is_hk = stock_code.upper().endswith('.HK')
    
    sources = []
    
    # Source 1: Eastmoney (东方财富) - A股
    if not is_hk:
        try:
            search_url = f"https://searchapi.eastmoney.com/bussiness/web/QuotationLabelSearch"
            params = {
                "keyword": code,
                "type": "report",
                "pi": 1,
                "ps": 10,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://data.eastmoney.com/",
            }
            resp = requests.get(search_url, params=params, headers=headers, timeout=15)
            data = resp.json()
            if data.get("Data"):
                for item in data["Data"]:
                    if "年报" in item.get("title", "") and str(year) in item.get("title", ""):
                        sources.append({
                            "url": item.get("url", ""),
                            "source": "eastmoney",
                            "title": item.get("title", ""),
                            "year": year,
                        })
                        break
        except Exception as e:
            print(f"  [WARN] Eastmoney search failed: {e}")
    
    # Source 2: CNINFO (巨潮资讯) - A股
    if not is_hk:
        try:
            search_url = "http://www.cninfo.com.cn/new/fulltextSearch/full"
            params = {
                "searchkey": code,
                "sdate": f"{year}-01-01",
                "edate": f"{year + 1}-06-30",
                "isfulltext": "false",
                "sortName": "pubdate",
                "sortType": "desc",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "http://www.cninfo.com.cn/",
            }
            resp = requests.get(search_url, params=params, headers=headers, timeout=15)
            data = resp.json()
            if data.get("announcements"):
                for item in data["announcements"]:
                    title = item.get("announcementTitle", "")
                    if "年报" in title and str(year) in title:
                        adj_url = item.get("adjunctUrl", "")
                        if adj_url:
                            pdf_url = f"http://static.cninfo.com.cn/{adj_url}"
                            sources.append({
                                "url": pdf_url,
                                "source": "cninfo",
                                "title": title,
                                "year": year,
                            })
                            break
        except Exception as e:
            print(f"  [WARN] CNINFO search failed: {e}")
    
    # Source 3: HKEXnews (港交所披露易) - 港股
    if is_hk:
        try:
            stock_code_hk = code.zfill(5)
            search_url = "https://www.hkexnews.hk/revised/search-vm-std.aspx"
            params = {
                "sc": stock_code_hk,
                "src": "MAIN",
                "t": "ar",
                "y": year,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.hkexnews.hk/",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            resp = requests.get(search_url, params=params, headers=headers, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                pdf_pattern = r'href="(/revised/[^"]+\.pdf)"'
                matches = re.findall(pdf_pattern, html)
                for match in matches:
                    if "annual" in match.lower() or "年报" in match:
                        pdf_url = f"https://www.hkexnews.hk{match}"
                        sources.append({
                            "url": pdf_url,
                            "source": "hkexnews",
                            "title": f"{stock_code} {year} 年报",
                            "year": year,
                        })
                        break
        except Exception as e:
            print(f"  [WARN] HKEXnews search failed: {e}")
    
    # Source 4: Xueqiu (雪球) - 通用
    try:
        xq_code = code if is_hk else f"SH{code}" if stock_code.endswith('.SH') else f"SZ{code}"
        search_url = f"https://xueqiu.com/query/v1/symbol/search/status.json"
        params = {
            "symbol": xq_code,
            "count": 10,
            "comment": 0,
            "symbol_id": "",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://xueqiu.com/S/{xq_code}",
            "Cookie": "xq_a_token=test;",
        }
        resp = requests.get(search_url, params=params, headers=headers, timeout=15)
        data = resp.json()
        if data.get("list"):
            for item in data["list"]:
                title = item.get("title", "")
                if "年报" in title and str(year) in title:
                    if item.get("target"):
                        sources.append({
                            "url": item.get("target", ""),
                            "source": "xueqiu",
                            "title": title,
                            "year": year,
                        })
                        break
    except Exception as e:
        print(f"  [WARN] Xueqiu search failed: {e}")
    
    return sources[0] if sources else None


def download_pdf(url: str, save_path: str) -> int:
    """Download PDF from URL to save_path.
    
    Returns:
        File size in bytes, or -1 on failure.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://www.hkexnews.hk/",
    }
    
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    
    try:
        resp = requests.get(url, headers=headers, timeout=120, stream=True)
        resp.raise_for_status()
        
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return os.path.getsize(save_path)
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")
        return -1


def run_phase0(ts_code: str, output_dir: Path, year: int = None) -> tuple[bool, str]:
    """Run PDF auto-download (Phase 0).
    
    Returns:
        (success, pdf_path or error_message)
    """
    print(f"\n{'='*60}")
    print(f"[Phase 0] PDF 自动下载")
    print(f"{'='*60}")
    print(f"股票代码: {ts_code}")
    print(f"目标年份: {year or '最新'}")
    print()
    
    code = ts_code.split('.')[0]
    
    # Check if PDF already exists locally
    local_pdf = check_local_pdf(ts_code, year or datetime.now().year - 1, str(output_dir))
    if local_pdf:
        print(f"  找到本地 PDF: {local_pdf}")
        return True, local_pdf
    
    # Search for PDF URL
    print("  搜索年报 PDF...")
    result = search_report_url(ts_code, year)
    
    if not result:
        print("  [WARN] 未找到年报 PDF 下载链接")
        return False, "未找到年报 PDF 下载链接"
    
    print(f"  找到: {result['title']}")
    print(f"  来源: {result['source']}")
    print(f"  URL: {result['url']}")
    
    # Download PDF
    filename = f"{code}_{result['year']}_年报.pdf"
    save_path = output_dir / filename
    
    print(f"  下载中...")
    filesize = download_pdf(result['url'], str(save_path))
    
    if filesize > 0:
        print(f"  下载成功: {save_path}")
        print(f"  文件大小: {filesize:,} bytes")
        return True, str(save_path)
    else:
        return False, "PDF 下载失败"


def run_phase1a(ts_code: str, output_dir: Path) -> tuple[bool, str]:
    """Run Tushare data collection (Phase 1A)."""
    output_file = output_dir / "data_pack_market.md"
    
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "tushare_collector.py"),
        "--code", ts_code,
        "--output", str(output_file),
    ]
    
    print(f"\n{'='*60}")
    print(f"[Phase 1A] Tushare 数据采集")
    print(f"{'='*60}")
    print(f"股票代码: {ts_code}")
    print(f"输出文件: {output_file}")
    print()
    
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=False)
        if result.returncode == 0:
            return True, str(output_file)
        else:
            return False, f"tushare_collector.py exited with code {result.returncode}"
    except Exception as e:
        return False, str(e)


def run_phase2a(pdf_path: str, output_dir: Path) -> tuple[bool, str]:
    """Run PDF preprocessing (Phase 2A)."""
    output_file = output_dir / "pdf_sections.json"
    
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "pdf_preprocessor.py"),
        "--pdf", pdf_path,
        "--output", str(output_file),
    ]
    
    print(f"\n{'='*60}")
    print(f"[Phase 2A] PDF 预处理")
    print(f"{'='*60}")
    print(f"PDF 文件: {pdf_path}")
    print(f"输出文件: {output_file}")
    print()
    
    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=False)
        if result.returncode == 0:
            return True, str(output_file)
        else:
            return False, f"pdf_preprocessor.py exited with code {result.returncode}"
    except Exception as e:
        return False, str(e)


def find_existing_output_dir(ts_code: str, company_name: str = None) -> Path:
    """Find existing output directory for the given stock.
    
    Checks multiple naming conventions to enable data reuse:
    1. {代码}_{公司} (current standard): output/600989SH_宝丰能源
    2. {公司}_{代码} (legacy format): output/宝丰能源_600989SH
    3. {代码} (minimal format): output/600989SH
    
    Returns the first existing directory, or None if not found.
    """
    code = ts_code.replace(".", "")
    output_root = PROJECT_ROOT / "output"
    
    candidates = []
    if company_name:
        candidates.append(f"{code}_{company_name}")
        candidates.append(f"{company_name}_{code}")
    candidates.append(code)
    candidates.append(ts_code.replace(".", "_"))
    
    for dir_name in candidates:
        candidate_path = output_root / dir_name
        if candidate_path.exists() and candidate_path.is_dir():
            return candidate_path
    
    return None


def create_output_dir(ts_code: str, company_name: str = None, reuse_existing: bool = True) -> Path:
    """Create output directory for analysis results.
    
    Follows coordinator.md convention:
    {output_dir} = {workspace}/output/{代码}_{公司}
    Example: output/600989SH_宝丰能源
    
    If reuse_existing=True, first checks for existing directories with different
    naming conventions to enable data reuse across sessions.
    """
    code = ts_code.replace(".", "")
    
    if reuse_existing:
        existing_dir = find_existing_output_dir(ts_code, company_name)
        if existing_dir:
            print(f"  [INFO] 复用已有目录: {existing_dir}")
            return existing_dir
    
    if company_name:
        dir_name = f"{code}_{company_name}"
    else:
        dir_name = ts_code.replace(".", "_")
    
    output_dir = PROJECT_ROOT / "output" / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def generate_agent_tasks(ts_code: str, output_dir: Path,
                         has_pdf: bool, pdf_path: str = None, company_name: str = None,
                         channel: str = "direct") -> dict:
    """Generate structured Agent task definitions.
    
    Returns a dict with task definitions that can be:
    1. Written to a JSON file for programmatic consumption
    2. Formatted as markdown instructions for LLM consumption
    
    Report output follows coordinator.md convention:
    {output_dir}/{公司名}_{代码}_分析报告.md
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    code = ts_code.replace(".", "")
    if company_name is None:
        company_name = output_dir.name.split("_")[1] if "_" in output_dir.name else ts_code
    
    prompts_dir = PROJECT_ROOT / "prompts"
    
    report_filename = f"{company_name}_{code}_分析报告.md"
    report_path = output_dir / report_filename
    
    tasks = {
        "meta": {
            "ts_code": ts_code,
            "company_name": company_name,
            "channel": channel,
            "date": date_str,
            "has_pdf": has_pdf,
            "output_dir": str(output_dir),
            "report_path": str(report_path),
            "prompts_dir": str(prompts_dir),
        },
        "completed_phases": [],
        "pending_phases": [],
    }
    
    tasks["completed_phases"].append({
        "phase": "1A",
        "name": "Tushare 数据采集",
        "output": str(output_dir / "data_pack_market.md"),
    })
    
    if has_pdf:
        tasks["completed_phases"].append({
            "phase": "2A",
            "name": "PDF 预处理",
            "output": str(output_dir / "pdf_sections.json"),
        })
    
    tasks["pending_phases"].append({
        "phase": "1B",
        "name": "WebSearch 数据补充",
        "description": "通过 WebSearch 补充 §7/§8/§10/§13 数据",
        "prompt_file": str(prompts_dir / "phase1_数据采集.md"),
        "inputs": [str(output_dir / "data_pack_market.md")],
        "outputs": [str(output_dir / "data_pack_market.md")],
        "dependencies": ["1A"],
        "instructions": f"""
请阅读 {prompts_dir / 'phase1_数据采集.md'} 中的完整指令。

目标股票：{ts_code}（{company_name}）
持股渠道：{channel}

data_pack_market.md 已由 tushare_collector.py 生成了 §1-§6, §7(部分:十大股东), §9, §11, §12, §14, §15, §16, §3P, §4P, 审计意见, §13.1 部分。

你的任务是通过 WebSearch 补充以下章节，替换 data_pack_market.md 中的占位符：
- §7 管理层与治理（追加定性信息）
- §8 行业与竞争（替换占位符）
- §10 MD&A 摘要（替换占位符）
- §13.2 Warnings（替换占位符）

注意：data_pack_market.md 中 §8, §10, §13.2 含占位符 `*[§N 待Agent WebSearch补充]*`。
使用 Edit 工具**替换**这些占位符为实际内容。
""",
    })
    
    if has_pdf:
        tasks["pending_phases"].append({
            "phase": "2B",
            "name": "PDF 精提取",
            "description": "从 PDF 提取附注数据",
            "prompt_file": str(prompts_dir / "phase2_PDF解析.md"),
            "inputs": [str(output_dir / "pdf_sections.json")],
            "outputs": [str(output_dir / "data_pack_report.md")],
            "dependencies": ["2A"],
            "instructions": f"""
请阅读 {prompts_dir / 'phase2_PDF解析.md'} 中的完整指令。

pdf_sections.json 文件路径：{output_dir / 'pdf_sections.json'}
公司名称：{company_name}

从 pdf_sections.json 提取以下数据，写入 {output_dir / 'data_pack_report.md'}：
- P2 受限资产
- P3 应收账款账龄
- P4 关联方交易
- P6 或有负债
- P13 非经常性损益
- MDA 管理层讨论
- SUB 主要子公司（条件触发）
""",
        })
    
    tasks["pending_phases"].append({
        "phase": "3",
        "name": "因子分析与报告生成",
        "description": "执行 4 因子分析并生成投资报告",
        "prompt_file": str(prompts_dir / "phase3_分析与报告.md"),
        "reference_files": [
            str(prompts_dir / "references" / "factor1_资产质量与商业模式.md"),
            str(prompts_dir / "references" / "factor2_穿透回报率粗算.md"),
            str(prompts_dir / "references" / "factor3_穿透回报率精算.md"),
            str(prompts_dir / "references" / "factor4_估值与安全边际.md"),
        ],
        "inputs": [
            str(output_dir / "data_pack_market.md"),
            str(output_dir / "data_pack_report.md") if has_pdf else None,
        ],
        "outputs": [str(report_path)],
        "dependencies": ["1B"] + (["2B"] if has_pdf else []),
        "instructions": f"""
请阅读 {prompts_dir / 'phase3_分析与报告.md'} 中的完整指令。

数据包文件：
  - {output_dir / 'data_pack_market.md'}
  - {output_dir / 'data_pack_report.md' if has_pdf else '（无PDF，使用降级方案）'}

因子参考文件：
  - {prompts_dir / 'references' / 'factor1_资产质量与商业模式.md'}
  - {prompts_dir / 'references' / 'factor2_穿透回报率粗算.md'}
  - {prompts_dir / 'references' / 'factor3_穿透回报率精算.md'}
  - {prompts_dir / 'references' / 'factor4_估值与安全边际.md'}

输出报告：{report_path}

报告结构：
1. Executive Summary
2. 商业模式深度扫描
3. 管理层与治理分析
4. 三大支柱评估
5. 因子2 & 因子3 穿透回报率计算
6. Fact Check 21 项验证
7. 操作建议
8. 风险提示
9. 结论
""",
    })
    
    return tasks


def format_tasks_as_markdown(tasks: dict) -> str:
    """Format Agent tasks as readable markdown instructions."""
    meta = tasks["meta"]
    lines = [
        "=" * 60,
        "后续步骤（需要 Agent 执行）",
        "=" * 60,
        "",
        f"股票代码: {meta['ts_code']}",
        f"公司名称: {meta['company_name']}",
        f"持股渠道: {meta['channel']}",
        f"输出目录: {meta['output_dir']}",
        "",
        "-" * 60,
        "已完成阶段",
        "-" * 60,
    ]
    
    for phase in tasks["completed_phases"]:
        lines.append(f"  ✅ Phase {phase['phase']}: {phase['name']}")
        lines.append(f"     输出: {phase['output']}")
    
    lines.extend([
        "",
        "-" * 60,
        "待执行阶段（按顺序执行）",
        "-" * 60,
    ])
    
    for phase in tasks["pending_phases"]:
        deps = ", ".join(phase.get("dependencies", []))
        lines.append(f"")
        lines.append(f"### Phase {phase['phase']}: {phase['name']}")
        lines.append(f"**依赖**: {deps}")
        lines.append(f"**描述**: {phase['description']}")
        lines.append(f"**输出**: {', '.join(phase['outputs'])}")
        lines.append(f"")
        lines.append("**指令:**")
        lines.append(phase["instructions"])
    
    lines.extend([
        "",
        "=" * 60,
        "执行说明",
        "=" * 60,
        "",
        "1. 按上述顺序执行各阶段",
        "2. 每个阶段完成后，检查输出文件是否正确生成",
        f"3. Phase 3 完成后，报告将保存到: {tasks['meta']['report_path']}",
        "",
    ])
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Turtle Investment Framework - Complete Analysis Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic analysis without PDF
  python scripts/turtle_analysis.py --code 600887
  
  # Use local PDF file (will be copied to output directory)
  python scripts/turtle_analysis.py --code 600887 --pdf /path/to/report.pdf
  
  # Auto-download PDF from internet
  python scripts/turtle_analysis.py --code 600887 --download-pdf
  
  # HK stock with PDF download
  python scripts/turtle_analysis.py --code 00700.HK --channel hkconnect --download-pdf
  
  # Specify year for PDF
  python scripts/turtle_analysis.py --code 600887 --pdf report.pdf --year 2023
  
  # Fallback: try local PDF first, then download if failed
  python scripts/turtle_analysis.py --code 600887 --pdf local.pdf --download-pdf
        """
    )
    parser.add_argument(
        "--code", "-c",
        required=True,
        help="Stock code (e.g., 600887, 600887.SH, 00700.HK)"
    )
    parser.add_argument(
        "--pdf", "-p",
        help="Path to local annual report PDF file (will be copied to output directory)"
    )
    parser.add_argument(
        "--download-pdf",
        action="store_true",
        help="Auto-download annual report PDF from internet (Phase 0)"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Target year for PDF download (default: latest available)"
    )
    parser.add_argument(
        "--channel",
        choices=["direct", "hkconnect", "us"],
        default="direct",
        help="Holding channel for HK stocks (default: direct)"
    )
    parser.add_argument(
        "--company",
        help="Company name (optional, for output directory naming)"
    )
    parser.add_argument(
        "--skip-phase1a",
        action="store_true",
        help="Skip Phase 1A if data_pack_market.md already exists"
    )
    
    args = parser.parse_args()
    
    ts_code = validate_stock_code(args.code)
    print(f"\n🐢 龟龟投资策略分析")
    print(f"   股票代码: {ts_code}")
    print(f"   持股渠道: {args.channel}")
    
    company_name = args.company
    if not company_name:
        print("  [INFO] 正在获取公司名称...")
        company_name = get_company_name(ts_code)
        if company_name:
            print(f"  [INFO] 公司名称: {company_name}")
        else:
            print("  [WARN] 未能获取公司名称，将使用代码作为目录名")
    
    output_dir = create_output_dir(ts_code, company_name)
    
    print(f"   输出目录: {output_dir}")
    
    results = {
        "ts_code": ts_code,
        "channel": args.channel,
        "output_dir": str(output_dir),
        "phases": {}
    }
    
    has_pdf = False
    pdf_path = None
    
    # Priority: --pdf (local file) > --download-pdf (remote download)
    if args.pdf:
        print(f"\n{'='*60}")
        print(f"[Phase 0] 本地 PDF 文件处理")
        print(f"{'='*60}")
        print(f"本地文件: {args.pdf}")
        
        success, msg = copy_local_pdf(args.pdf, output_dir, ts_code, args.year)
        results["phases"]["phase0"] = {"success": success, "output": msg, "source": "local"}
        
        if success:
            pdf_path = msg
            has_pdf = True
            print(f"  复制成功: {pdf_path}")
            print(f"  文件大小: {os.path.getsize(pdf_path):,} bytes")
        else:
            print(f"\n⚠️ 本地 PDF 处理失败: {msg}")
            if args.download_pdf:
                print("  尝试远程下载...")
            else:
                print("  将继续执行无 PDF 模式...")
    
    # Phase 0: Auto-download PDF (only if local PDF not provided or failed)
    if not has_pdf and args.download_pdf:
        success, msg = run_phase0(ts_code, output_dir, args.year)
        results["phases"]["phase0"] = {"success": success, "output": msg, "source": "remote"}
        if success:
            pdf_path = msg
            has_pdf = True
        else:
            print(f"\n⚠️ Phase 0 失败: {msg}")
            print("  将继续执行无 PDF 模式...")
    
    # Phase 1A: Tushare data collection
    if not args.skip_phase1a:
        success, msg = run_phase1a(ts_code, output_dir)
        results["phases"]["phase1a"] = {"success": success, "output": msg}
        if not success:
            print(f"\n❌ Phase 1A 失败: {msg}")
            sys.exit(1)
    else:
        print(f"\n⏭️ 跳过 Phase 1A（使用现有数据）")
    
    # Phase 2A: PDF preprocessing
    if has_pdf and pdf_path:
        success, msg = run_phase2a(pdf_path, output_dir)
        results["phases"]["phase2a"] = {"success": success, "output": msg}
        has_pdf = success
        if not success:
            print(f"\n⚠️ Phase 2A 失败: {msg}")

    # Generate structured Agent tasks
    tasks = generate_agent_tasks(
        ts_code, output_dir, has_pdf, pdf_path,
        company_name=company_name, channel=args.channel
    )
    
    # Write tasks to JSON file for programmatic consumption
    tasks_file = output_dir / "agent_tasks.json"
    with open(tasks_file, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    
    # Print markdown instructions for LLM consumption
    print(format_tasks_as_markdown(tasks))
    
    print(f"\n任务定义已保存: {tasks_file}")
    
    # Save analysis status
    results_file = output_dir / "analysis_status.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"状态文件已保存: {results_file}")


if __name__ == "__main__":
    main()
