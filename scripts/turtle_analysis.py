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

REPORT_PERIODS = {
    "年报": "年报",
    "半年报": "半年报", 
    "一季报": "一季报",
    "一季度报": "一季报",
    "三季度报": "三季度报",
    "季报": "季报",
}

PERIOD_ORDER = ["年报", "半年报", "一季报", "三季度报"]

def parse_report_period(period_str: str) -> str:
    """Normalize report period string.
    
    Args:
        period_str: Raw period string (e.g., '年报', '一季度报', '一季报')
    
    Returns:
        Normalized period name (e.g., '年报', '一季报')
    """
    period_str = period_str.strip()
    for key, value in REPORT_PERIODS.items():
        if key in period_str:
            return value
    return "年报"

def infer_report_period_from_filename(filename: str) -> tuple[int, str]:
    """Extract year and period from PDF filename.
    
    Args:
        filename: PDF filename (e.g., '云天化2025年年度报告.pdf')
    
    Returns:
        (year, period) tuple, e.g., (2025, '年报')
    """
    year_match = re.search(r'20\d{2}', filename)
    year = int(year_match.group()) if year_match else datetime.now().year - 1
    
    if "年度报告" in filename or "年报" in filename:
        period = "年报"
    elif "半年度" in filename or "半年报" in filename:
        period = "半年报"
    elif "第一季度" in filename or "一季报" in filename or "一季度" in filename:
        period = "一季报"
    elif "第三季度" in filename or "三季度" in filename or "三季度报" in filename:
        period = "三季度报"
    else:
        period = "年报"
    
    return year, period

def get_company_name(ts_code: str) -> str:
    """Fetch company name from Tushare stock_basic API.
    
    Args:
        ts_code: Stock code (e.g., '600096.SH', '00700.HK')
    
    Returns:
        Company name string, or empty string if not found
    """
    try:
        token = get_token()
        api_url = get_api_url()
        ts.set_token(token)
        pro = ts.pro_api(timeout=30)
        if api_url:
            pro._DataApi__token = token
            pro._DataApi__http_url = api_url
        
        if ts_code.endswith('.HK'):
            df = pro.hk_basic(ts_code=ts_code, fields='ts_code,name')
        else:
            df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name')
        
        if not df.empty and 'name' in df.columns:
            return str(df.iloc[0]['name'])
    except Exception as e:
        print(f"  [WARN] 获取公司名称失败: {e}")
    
    return ""


def copy_local_pdf(local_path: str, output_dir: Path, ts_code: str, 
                   company_name: str = None, year: int = None, 
                   period: str = "年报") -> tuple[bool, str, int, str]:
    """Copy local PDF file to output directory with standardized naming.
    
    Naming convention: {代码}_{年份}_{公司名}_{报告期}.pdf
    Example: 600989_2024_宝丰能源_年报.pdf
    
    Args:
        local_path: Path to local PDF file
        output_dir: Output directory to copy to
        ts_code: Stock code for naming
        company_name: Company name (optional)
        year: Year for naming (default: inferred from filename)
        period: Report period (default: inferred from filename)
    
    Returns:
        (success, new_path or error_message, year, period)
    """
    filename = os.path.basename(local_path)
    
    if year is None or period == "年报":
        inferred_year, inferred_period = infer_report_period_from_filename(filename)
        if year is None:
            year = inferred_year
        if period == "年报":
            period = inferred_period
    
    code = ts_code.split('.')[0]
    
    if not os.path.exists(local_path):
        return False, f"本地 PDF 文件不存在: {local_path}", year, period
    
    if company_name:
        pdf_filename = f"{code}_{year}_{company_name}_{period}.pdf"
    else:
        pdf_filename = f"{code}_{year}_{period}.pdf"
    
    dest_path = output_dir / pdf_filename
    
    try:
        shutil.copy2(local_path, str(dest_path))
        filesize = os.path.getsize(dest_path)
        print(f"  PDF 已保存: {pdf_filename} ({filesize:,} bytes)")
        return True, str(dest_path), year, period
    except Exception as e:
        return False, f"复制 PDF 文件失败: {e}", year, period


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


def run_phase1a(ts_code: str, output_file: Path) -> tuple[bool, str]:
    """Run Tushare data collection (Phase 1A)."""
    
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


def find_existing_output_dir(ts_code: str, company_name: str = None, 
                             year: int = None, period: str = None) -> Path:
    """Find existing output directory for the given stock and report period.
    
    New directory structure:
    output/{代码}_{公司}/{年份}_{报告期}/
    Example: output/600989SH_宝丰能源/2024_年报/
    
    Legacy directory structure (backward compatible):
    output/{代码}_{公司}/
    
    Returns the first existing directory matching the criteria, or None if not found.
    """
    code = ts_code.replace(".", "")
    output_root = PROJECT_ROOT / "output"
    
    company_dir_name = f"{code}_{company_name}" if company_name else code
    
    if year and period:
        period_dir = output_root / company_dir_name / f"{year}_{period}"
        if period_dir.exists() and period_dir.is_dir():
            return period_dir
    
    company_dir = output_root / company_dir_name
    if company_dir.exists() and company_dir.is_dir():
        return None
    
    legacy_candidates = []
    if company_name:
        legacy_candidates.append(f"{company_name}_{code}")
    legacy_candidates.append(code)
    legacy_candidates.append(ts_code.replace(".", "_"))
    
    for dir_name in legacy_candidates:
        legacy_dir = output_root / dir_name
        if legacy_dir.exists() and legacy_dir.is_dir():
            return None
    
    return None


def create_output_dir(ts_code: str, company_name: str = None, 
                      year: int = None, period: str = "年报",
                      reuse_existing: bool = True) -> Path:
    """Create output directory for analysis results.
    
    New directory structure:
    output/{代码}_{公司}/{年份}_{报告期}/
    Example: output/600989SH_宝丰能源/2024_年报/
    
    This structure enables:
    1. Organized storage of multiple report periods per company
    2. Data reuse within the same report period
    3. Clear separation of different report periods
    
    Args:
        ts_code: Stock code (e.g., '600989.SH')
        company_name: Company name (e.g., '宝丰能源')
        year: Report year (e.g., 2024)
        period: Report period (e.g., '年报', '一季报')
        reuse_existing: Whether to reuse existing directory for same period
    """
    code = ts_code.replace(".", "")
    output_root = PROJECT_ROOT / "output"
    
    company_dir_name = f"{code}_{company_name}" if company_name else code
    company_dir = output_root / company_dir_name
    
    if reuse_existing:
        existing_dir = find_existing_output_dir(ts_code, company_name, year, period)
        if existing_dir:
            print(f"  [INFO] 复用已有目录: {existing_dir}")
            return existing_dir
    
    if year and period:
        period_dir_name = f"{year}_{period}"
        period_dir = company_dir / period_dir_name
        period_dir.mkdir(parents=True, exist_ok=True)
        return period_dir
    else:
        company_dir.mkdir(parents=True, exist_ok=True)
        return company_dir


def generate_agent_tasks(ts_code: str, output_dir: Path,
                         has_pdf: bool, pdf_path: str = None, company_name: str = None,
                         channel: str = "direct", year: int = None, period: str = "年报") -> dict:
    """Generate structured Agent task definitions.
    
    Returns a dict with task definitions that can be:
    1. Written to a JSON file for programmatic consumption
    2. Formatted as markdown instructions for LLM consumption
    
    File naming convention:
    - PDF: {代码}_{年份}_{公司名}_{报告期}.pdf
    - Market data: data_pack_market.md (fixed name)
    - Report data: data_pack_report.md (fixed name)
    - Analysis report: {公司名}_{代码}_分析报告.md
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    code = ts_code.replace(".", "")
    if company_name is None:
        company_name = output_dir.name.split("_")[1] if "_" in output_dir.name else ts_code
    
    prompts_dir = PROJECT_ROOT / "prompts"
    
    market_data_filename = "data_pack_market.md"
    report_data_filename = "data_pack_report.md"
    report_filename = f"{company_name}_{code}_分析报告.md" if company_name else f"{code}_分析报告.md"
    
    market_data_path = output_dir / market_data_filename
    report_data_path = output_dir / report_data_filename
    report_path = output_dir / report_filename
    
    tasks = {
        "meta": {
            "ts_code": ts_code,
            "company_name": company_name,
            "channel": channel,
            "date": date_str,
            "year": year,
            "period": period,
            "has_pdf": has_pdf,
            "output_dir": str(output_dir),
            "market_data_path": str(market_data_path),
            "report_data_path": str(report_data_path),
            "report_path": str(report_path),
            "prompts_dir": str(prompts_dir),
        },
        "completed_phases": [],
        "pending_phases": [],
    }
    
    tasks["completed_phases"].append({
        "phase": "1A",
        "name": "Tushare 数据采集",
        "output": str(market_data_path),
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
        "inputs": [str(market_data_path)],
        "outputs": [str(market_data_path)],
        "dependencies": ["1A"],
        "instructions": f"""
请阅读 {prompts_dir / 'phase1_数据采集.md'} 中的完整指令。

目标股票：{ts_code}（{company_name}）
持股渠道：{channel}
报告期：{year}年{period}

{market_data_filename} 已由 tushare_collector.py 生成了 §1-§6, §7(部分:十大股东), §9, §11, §12, §14, §15, §16, §3P, §4P, 审计意见, §13.1 部分。

你的任务是通过 WebSearch 补充以下章节，替换文件中的占位符：
- §7 管理层与治理（追加定性信息）
- §8 行业与竞争（替换占位符）
- §10 MD&A 摘要（替换占位符）
- §13.2 Warnings（替换占位符）

注意：文件中 §8, §10, §13.2 含占位符 `*[§N 待Agent WebSearch补充]*`。
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
            "outputs": [str(report_data_path)],
            "dependencies": ["2A"],
            "instructions": f"""
请阅读 {prompts_dir / 'phase2_PDF解析.md'} 中的完整指令。

pdf_sections.json 文件路径：{output_dir / 'pdf_sections.json'}
公司名称：{company_name}
报告期：{year}年{period}

从 pdf_sections.json 提取以下数据，写入 {report_data_path}：
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
            str(market_data_path),
            str(report_data_path) if has_pdf else None,
        ],
        "outputs": [str(report_path)],
        "dependencies": ["1B"] + (["2B"] if has_pdf else []),
        "instructions": f"""
请阅读 {prompts_dir / 'phase3_分析与报告.md'} 中的完整指令。

数据包文件：
  - {market_data_path}
  - {report_data_path if has_pdf else '（无PDF，使用降级方案）'}

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
    year = meta.get('year', '')
    period = meta.get('period', '')
    period_str = f"{year}年{period}" if year and period else ""
    
    lines = [
        "=" * 60,
        "后续步骤（需要 Agent 执行）",
        "=" * 60,
        "",
        f"股票代码: {meta['ts_code']}",
        f"公司名称: {meta['company_name']}",
        f"持股渠道: {meta['channel']}",
    ]
    
    if period_str:
        lines.append(f"报告期: {period_str}")
    
    lines.extend([
        f"输出目录: {meta['output_dir']}",
        "",
        "-" * 60,
        "已完成阶段",
        "-" * 60,
    ])
    
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
        help="Target year for report (default: inferred from PDF filename or latest available)"
    )
    parser.add_argument(
        "--period",
        choices=["年报", "半年报", "一季报", "三季度报"],
        default="年报",
        help="Report period type (default: 年报)"
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
    
    report_year = args.year
    report_period = args.period
    
    if args.pdf and (report_year is None or report_period == "年报"):
        inferred_year, inferred_period = infer_report_period_from_filename(args.pdf)
        if report_year is None:
            report_year = inferred_year
        if report_period == "年报" and inferred_period != "年报":
            report_period = inferred_period
    
    if report_year is None:
        report_year = datetime.now().year - 1
        if datetime.now().month < 4:
            report_year -= 1
    
    print(f"   报告期: {report_year}年{report_period}")
    
    output_dir = create_output_dir(ts_code, company_name, report_year, report_period)
    
    print(f"   输出目录: {output_dir}")
    
    results = {
        "ts_code": ts_code,
        "channel": args.channel,
        "year": report_year,
        "period": report_period,
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
        
        success, msg, actual_year, actual_period = copy_local_pdf(
            args.pdf, output_dir, ts_code, company_name, report_year, report_period
        )
        results["phases"]["phase0"] = {"success": success, "output": msg, "source": "local"}
        
        if success:
            pdf_path = msg
            has_pdf = True
            if actual_year != report_year:
                report_year = actual_year
                results["year"] = report_year
            if actual_period != report_period:
                report_period = actual_period
                results["period"] = report_period
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
    
    code = ts_code.replace(".", "")
    market_data_filename = "data_pack_market.md"
    market_data_path = output_dir / market_data_filename
    
    # Phase 1A: Tushare data collection
    if not args.skip_phase1a:
        success, msg = run_phase1a(ts_code, market_data_path)
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
        company_name=company_name, channel=args.channel,
        year=report_year, period=report_period
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
