"""Deterministic paper-table and open-dataset quality inspection.

This module adapts the useful, non-UI core of deepshovel-data-quality to the
Compiler's existing DocumentIR boundary.  It deliberately reuses MinerU output
instead of uploading a new PDF to a second parser pipeline.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .models import DocumentIR
from .structured_source import StructuredSourceError, table_quality_grid

DATA_QUALITY_INSPECTION_VERSION = 2

_URL_RE = re.compile(r"https?://[^\s<>\])}\"']+", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_DATA_HINT_RE = re.compile(
    r"data availability|availability of data|supporting information|supplementary|"
    r"数据可用性|数据获取|补充材料|dataset|repository|zenodo|pangaea|figshare|dryad|dataverse",
    re.IGNORECASE,
)
_MARKDOWN_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_MISSING = {"", "nan", "null", "none", "na", "n/a", "-", "—"}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _check(
    rule_id: str,
    name: str,
    conclusion: str,
    evidence: str,
    criterion: str,
    *,
    category: str,
    object_name: str,
    execution_status: str = "executed",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "category": category,
        "object_name": object_name,
        "execution_status": execution_status,
        "conclusion": conclusion,
        "severity": "high"
        if conclusion == "high_warning"
        else "warning"
        if conclusion == "warning"
        else "info",
        "evidence": evidence,
        "criterion": criterion,
    }


def _split_row(line: str) -> list[str]:
    return [
        cell.replace("\\|", "|").strip()
        for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))
    ]


def _markdown_tables(document: DocumentIR) -> list[dict[str, Any]]:
    lines = document.markdown.splitlines()
    tables: list[dict[str, Any]] = []
    index = 0
    while index + 1 < len(lines):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header or not _MARKDOWN_SEPARATOR_RE.match(separator):
            index += 1
            continue
        source_line = index + 1
        rows = [_split_row(header)]
        index += 2
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            rows.append(_split_row(lines[index]))
            index += 1
        width = len(rows[0])
        body = [row for row in rows[1:] if len(row) == width]
        if width and body:
            tables.append(
                {
                    "name": f"论文表格 {len(tables) + 1}",
                    "page": _page_for_markdown_line(document, source_line),
                    "region": None,
                    "columns": rows[0],
                    "rows": body,
                    "source": f"Markdown 第 {source_line} 行",
                }
            )
    return tables


def _structured_tables(
    document: DocumentIR, warnings: list[str]
) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for page in document.pages:
        for block in page.blocks:
            if block.kind != "table":
                continue
            raw_body = block.structured.get("table_body")
            bodies = [raw_body] if isinstance(raw_body, str) else []
            if isinstance(raw_body, list):
                bodies.extend(item for item in raw_body if isinstance(item, str))
            for body in bodies:
                if "<table" not in body.casefold():
                    continue
                try:
                    columns, rows = table_quality_grid(body)
                except StructuredSourceError:
                    warnings.append(
                        f"第 {page.number} 页的表格结构无法可靠展开，请核对原件；"
                        "该表未作数据质量结论"
                    )
                    continue
                caption = block.structured.get("table_caption")
                tables.append(
                    {
                        "name": (
                            str(caption).strip()
                            if isinstance(caption, str) and caption.strip()
                            else f"论文表格 {len(tables) + 1}"
                        ),
                        "page": page.number,
                        "region": (
                            block.bbox.model_dump(mode="json") | {"unit": "normalized"}
                            if block.bbox is not None
                            else None
                        ),
                        "columns": columns,
                        "rows": rows,
                        "source": f"第 {page.number} 页 · MinerU 表格块",
                    }
                )
    return tables


def _paper_tables(document: DocumentIR, warnings: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in [*_structured_tables(document, warnings), *_markdown_tables(document)]:
        digest = hashlib.sha256(
            repr((table["columns"], table["rows"])).encode("utf-8")
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        table["name"] = table["name"] or f"论文表格 {len(result) + 1}"
        result.append(table)
    return result


def _page_for_markdown_line(document: DocumentIR, line: int) -> int | None:
    # MinerU's normalized markdown does not retain a lossless line-to-page map.
    # Use exact table text only when it occurs in exactly one page.
    lines = document.markdown.splitlines()
    needle = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    if not needle:
        return None
    matches = {
        page.number
        for page in document.pages
        for block in page.blocks
        if block.text and needle in block.text
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _table_checks(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[list[str]] = table["rows"]
    columns: list[str] = table["columns"]
    name = str(table["name"])
    checks = [
        _check(
            "structure.empty_table",
            "空表",
            "pass",
            f"{len(rows)} 行 × {len(columns)} 列",
            "表格至少包含一行和一列",
            category="structure",
            object_name=name,
        )
    ]
    duplicate_rows = len(rows) - len(
        {tuple(cell.strip() for cell in row) for row in rows}
    )
    checks.append(
        _check(
            "structure.duplicates",
            "重复行",
            "warning" if duplicate_rows else "pass",
            f"发现 {duplicate_rows} 行完全重复",
            "不存在完全重复的数据行",
            category="structure",
            object_name=name,
        )
    )
    normalized_columns = [column.strip().casefold() for column in columns]
    duplicate_columns = len(normalized_columns) - len(set(normalized_columns))
    checks.append(
        _check(
            "structure.duplicate_columns",
            "重复列名",
            "warning" if duplicate_columns else "pass",
            f"发现 {duplicate_columns} 个重复列名",
            "规范化列名应唯一",
            category="structure",
            object_name=name,
        )
    )
    for column_index, column in enumerate(columns):
        values = [row[column_index].strip() for row in rows]
        missing = sum(value.casefold() in _MISSING for value in values)
        ratio = missing / len(values) if values else 0
        conclusion = (
            "high_warning" if ratio >= 0.2 else "warning" if ratio >= 0.05 else "pass"
        )
        label = column.strip() or f"第 {column_index + 1} 列"
        object_name = f"{name} · {label}"
        checks.append(
            _check(
                "structure.missing",
                "缺失率",
                conclusion,
                f"{missing}/{len(values)} 个单元格缺失（{ratio:.1%}）",
                "缺失率低于 5%",
                category="completeness",
                object_name=object_name,
            )
        )
        numeric = [float(value) for value in values if _NUMBER_RE.fullmatch(value)]
        if len(numeric) >= 4:
            ordered = sorted(numeric)
            q1 = ordered[len(ordered) // 4]
            q3 = ordered[(len(ordered) * 3) // 4]
            iqr = q3 - q1
            low, high = q1 - 3 * iqr, q3 + 3 * iqr
            outliers = (
                sum(value < low or value > high for value in numeric) if iqr else 0
            )
            checks.append(
                _check(
                    "numeric.outliers",
                    "稳健异常值",
                    "warning" if outliers else "pass",
                    f"{outliers}/{len(numeric)} 个数值超出 3×IQR",
                    "异常值需要结合论文方法和量纲复核",
                    category="numeric",
                    object_name=object_name,
                )
            )
            lower_label = label.casefold()
            if any(token in lower_label for token in ("latitude", " lat", "纬度")):
                invalid = sum(not -90 <= value <= 90 for value in numeric)
                checks.append(
                    _check(
                        "geo.range",
                        "纬度范围",
                        "high_warning" if invalid else "pass",
                        f"发现 {invalid} 个超出 [-90, 90] 的值",
                        "纬度应位于 [-90, 90]",
                        category="geoscience",
                        object_name=object_name,
                    )
                )
            if any(
                token in lower_label for token in ("longitude", " lon", " lng", "经度")
            ):
                invalid = sum(not -180 <= value <= 180 for value in numeric)
                checks.append(
                    _check(
                        "geo.range",
                        "经度范围",
                        "high_warning" if invalid else "pass",
                        f"发现 {invalid} 个超出 [-180, 180] 的值",
                        "经度应位于 [-180, 180]",
                        category="geoscience",
                        object_name=object_name,
                    )
                )
            if len(numeric) >= 100:
                last_digits = Counter(abs(int(round(value))) % 10 for value in numeric)
                heaped = (last_digits[0] + last_digits[5]) / len(numeric)
                checks.append(
                    _check(
                        "digits.heaping",
                        "末位 0/5 堆积",
                        "warning" if heaped >= 0.3 else "pass",
                        f"末位 0/5 占 {heaped:.1%}",
                        "占比达到 30% 时提示人工复核；不单独证明数据异常",
                        category="digits",
                        object_name=object_name,
                    )
                )
    return checks


def _discover_datasets(markdown: str) -> list[dict[str, Any]]:
    discovered: dict[str, str] = {}
    for paragraph in re.split(r"\n\s*\n", markdown):
        if not _DATA_HINT_RE.search(paragraph):
            continue
        for url in _URL_RE.findall(paragraph):
            discovered.setdefault(url.rstrip(".,;:"), paragraph[:500])
        for doi in _DOI_RE.findall(paragraph):
            clean = doi.rstrip(".,;:)]}")
            discovered.setdefault(f"https://doi.org/{clean}", paragraph[:500])
    return [
        {"locator": locator, "evidence": evidence}
        for locator, evidence in list(discovered.items())[:20]
    ]


def _repository(locator: str) -> str:
    host = (urlparse(locator).hostname or "").casefold()
    return next(
        (
            name
            for name in ("zenodo", "pangaea", "figshare", "dryad", "dataverse")
            if name in host
        ),
        "generic",
    )


def _dataset_checks(item: dict[str, Any]) -> list[dict[str, Any]]:
    name = item["title"]
    checks = [
        _check(
            "metadata.identifier",
            "持久标识符",
            "pass",
            item["locator"],
            "应提供 DOI 或稳定 URL",
            category="metadata",
            object_name=name,
        ),
        _check(
            "metadata.repository",
            "数据仓库",
            "pass" if item["repository"] != "generic" else "warning",
            item["repository"],
            "建议使用可识别的专业或通用数据仓库",
            category="metadata",
            object_name=name,
        ),
        _check(
            "metadata.accessibility",
            "访问可达性",
            "no_conclusion",
            item["access_evidence"],
            "公开数据地址应可访问",
            category="metadata",
            object_name=name,
            execution_status="not_run",
        ),
        _check(
            "content.not_run",
            "数据内容检查",
            "no_conclusion",
            "本次只发现并探测数据入口，未自动下载外部数据文件",
            "需要用户明确启动数据文件下载后才能作内容结论",
            category="content",
            object_name=name,
            execution_status="not_run",
        ),
    ]
    return checks


def _dataset_record(item: dict[str, Any]) -> dict[str, Any]:
    locator = item["locator"]
    repository = _repository(locator)
    title = f"{repository} 数据集" if repository != "generic" else "论文关联数据入口"
    result = {
        "title": title,
        "locator": locator,
        "doi": next(iter(_DOI_RE.findall(locator)), None),
        "repository": repository,
        "accessible": None,
        "access_evidence": (
            "为避免论文内 URL 形成服务端请求边界，"
            "本轮只登记入口，未自动访问"
        ),
        "source_evidence": item["evidence"],
    }
    result["checks"] = _dataset_checks(result)
    return result


def inspect_document(document: DocumentIR) -> dict[str, Any]:
    warnings = [
        warning
        for warning in document.warnings
        if not warning.startswith("MinerU-derived table/chart/image structures")
    ]
    tables = _paper_tables(document, warnings)
    for table in tables:
        table["checks"] = _table_checks(table)
        columns = table["columns"]
        rows = table["rows"]
        table["row_count"] = len(rows)
        table["column_count"] = len(columns)
        # Keep the authenticated report responsive while retaining enough
        # structure to show users exactly where a quality rule fired.
        table["columns"] = columns[:100]
        table["rows"] = [row[:100] for row in rows[:500]]
        table["preview_truncated"] = len(columns) > 100 or len(rows) > 500

    locators = _discover_datasets(document.markdown)
    datasets = [_dataset_record(item) for item in locators]

    checks = [check for table in tables for check in table["checks"]] + [
        check for dataset in datasets for check in dataset["checks"]
    ]
    counts = Counter(check["conclusion"] for check in checks)
    identity = hashlib.sha256(
        (document.artifact.version_id + document.artifact.artifact_sha256).encode()
    ).hexdigest()
    if not tables:
        warnings.append("MinerU 结果中未发现可机器读取的论文表格")
    return {
        "schema_version": "data-quality-report/1.0",
        "inspection_version": DATA_QUALITY_INSPECTION_VERSION,
        "artifact_version_id": document.artifact.version_id,
        "run_id": f"data-quality:sha256:{identity}",
        "generated_at": _now(),
        "summary": {
            "paper_table_count": len(tables),
            "dataset_count": len(datasets),
            "check_count": len(checks),
            "pass": counts["pass"],
            "warning": counts["warning"],
            "high_warning": counts["high_warning"],
            "no_conclusion": counts["no_conclusion"],
        },
        "paper_tables": tables,
        "datasets": datasets,
        "warnings": warnings[:50],
    }
