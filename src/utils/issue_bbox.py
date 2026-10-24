"""Validate source-provided exact bboxes; exact positioning must be content-anchored.

AC-09：精确 bbox 不能由模型或输入自报 ``verified`` 便直接采信——每个
exact/verified 坐标都必须通过"原件内容锚定"核验：bbox 区域内必须能抽取到
与该证据引文一致的原文（去空白后包含）。无法核验（PDF 缺失、区域无文本、
引文与区域内容不符）一律降级为 region/unverified，绝不保留"看似精确"的
坐标。纯坐标（无引文）不允许被认证为精确定位。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import unicodedata

from src.schemas.issues import IssueItem


class PDFBBoxLocator:
    """Keep only source-verified exact coordinates and in-range physical pages."""

    def __init__(self, pdf_path: str):
        self.pdf_path = str(pdf_path or "")
        self._document = None
        self._native_document = None
        self._native_pages: Dict[int, Any] = {}
        self._file_hash: Optional[str] = None

    def close(self) -> None:
        if self._document is not None:
            try:
                self._document.close()
            except Exception:
                pass
        self._document = None
        if self._native_document is not None:
            try:
                self._native_document.close()
            except Exception:
                pass
        self._native_document = None
        self._native_pages.clear()

    @staticmethod
    def _native_text(value: Any) -> str:
        return "".join(unicodedata.normalize("NFKC", str(value or "")).split())

    def _native_span_bbox(self, evidence: Dict[str, Any], page: Optional[int]) -> Optional[List[float]]:
        """Locate only a binder-authenticated literal span using PDF characters.

        The declared offset is checked against the same native page text used
        by the binder. Every coordinate comes from its original characters;
        neither an amount nor a fuzzy match can select a source location.
        """
        if (evidence.get("source_method") != "native_text"
                or evidence.get("source_identity_status") != "verified"
                or not self._source_identity_complete(evidence, page)):
            return None
        block = re.fullmatch(r"native:p(\d+):s(\d+):e(\d+)", str(evidence.get("source_block_id") or ""))
        span = evidence.get("source_span")
        if (block is None or not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) is not int for value in span)
                or int(block[1]) != page or [int(block[2]), int(block[3])] != list(span)):
            return None
        start, end = span
        if start < 0 or end <= start:
            return None
        try:
            if page not in self._native_pages:
                import pdfplumber

                if self._native_document is None:
                    self._native_document = pdfplumber.open(self.pdf_path)
                if page is None or page > len(self._native_document.pages):
                    return None
                original = self._native_document.pages[page - 1]
                text = original.extract_text() or ""
                normalized_chars, positions = [], []
                for line in original.extract_text_lines(return_chars=True):
                    for char in line["chars"]:
                        for normalized in self._native_text(char.get("text")):
                            normalized_chars.append(normalized)
                            positions.append(char)
                self._native_pages[page] = (text, "".join(normalized_chars), positions)
            text, characters, positions = self._native_pages[page]
            quote = str(evidence.get("quote") or "")
            needle = self._native_text(quote)
            if (end > len(text) or text[start:end] != quote or not needle
                    or self._native_text(text).count(needle) != 1 or characters.count(needle) != 1):
                return None
            found = characters.index(needle)
            source_chars = positions[found:found + len(needle)]
            bbox = self._normalize_bbox([
                min(float(char["x0"]) for char in source_chars),
                min(float(char["top"]) for char in source_chars),
                max(float(char["x1"]) for char in source_chars),
                max(float(char["bottom"]) for char in source_chars),
            ])
            if self._valid_source_bbox(page, bbox) and self._region_text_matches(page, bbox, quote):
                return bbox
        except Exception:
            return None
        return None

    def _source_identity_complete(self, evidence: Dict[str, Any], page: Optional[int]) -> bool:
        """Exact source anchors must identify this original PDF, page, source object and quote."""
        file_hash = str(evidence.get("file_hash") or "").strip().lower()
        physical_page = self._to_positive_int(evidence.get("physical_page"))
        source_id = str(evidence.get("source_cell_id") or evidence.get("source_block_id") or "")
        quote = str(evidence.get("quote") or "")
        evidence_id = str(evidence.get("evidence_id") or "").strip().lower()
        if (
            len(file_hash) != 64
            or physical_page is None
            or page != physical_page
            or not source_id.strip()
            or not quote.strip()
            or len(evidence_id) != 16
        ):
            return False
        try:
            if self._file_hash is None:
                digest = hashlib.sha256()
                with Path(self.pdf_path).open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                self._file_hash = digest.hexdigest()
            expected_evidence_id = hashlib.sha1(
                "|".join((file_hash, str(physical_page), source_id, quote)).encode("utf-8")
            ).hexdigest()[:16]
            return file_hash == self._file_hash and evidence_id == expected_evidence_id
        except OSError:
            return False

    def locate(self, issue: IssueItem) -> IssueItem:
        location = dict(issue.location or {})
        location_level = self._effective_location_level(issue, location)
        verification_status = self._effective_verification_status(issue, location)

        raw_refs = location.get("table_refs")
        if isinstance(raw_refs, list):
            location["table_refs"] = [self._validate_ref(item) for item in raw_refs if isinstance(item, dict)]

        page = self._to_positive_int(issue.page_number or location.get("page"))
        if page is not None and not self._valid_document_page(page):
            page = None
            issue.page_number = None
            location.pop("page", None)

        pages = location.get("pages")
        if isinstance(pages, list):
            valid_pages = [
                item
                for item in (self._to_positive_int(value) for value in pages)
                if item is not None and self._valid_document_page(item)
            ]
            if valid_pages:
                location["pages"] = valid_pages
            else:
                location.pop("pages", None)

        valid_evidence = [
            self._validate_evidence(item)
            for item in (issue.evidence or [])
            if isinstance(item, dict)
        ]
        issue.evidence = valid_evidence
        # Operand references carry independent cells from several tables/pages.
        # Apply the same content gate to their nested references; an unchecked
        # coordinate must not survive via this additional export path.
        operands = location.get("operand_refs")
        if isinstance(operands, list):
            validated_operands = []
            for raw_operand in operands:
                if not isinstance(raw_operand, dict):
                    continue
                operand = dict(raw_operand)
                sources = operand.get("evidence_refs")
                if isinstance(sources, list):
                    operand["evidence_refs"] = [self._validate_evidence(item) for item in sources if isinstance(item, dict)]
                if operand.get("source_cell_id") or operand.get("source_block_id"):
                    operand = self._validate_ref(operand)
                validated_operands.append(operand)
            location["operand_refs"] = validated_operands

        bbox = self._normalize_bbox(issue.bbox or location.get("bbox"))
        bbox_page = page
        # A literal claim is the useful primary display anchor for a finding
        # comparing it with table amounts. Keep every monetary operand on its
        # own physical page, and show the statement instead of the first cell.
        native_anchors = [
            item for item in valid_evidence
            if item.get("source_span") and item.get("source_block_id")
            and item.get("location_level") == "exact_span"
            and item.get("verification_status") == "verified" and item.get("bbox")
        ]
        if native_anchors:
            preferred = next((item for item in native_anchors if item.get("role") in {"完成率声明", "比较表述", "说明决算金额", "占比声明"}),
                             native_anchors[0])
            bbox = self._normalize_bbox(preferred["bbox"])
            bbox_page = self._to_positive_int(preferred.get("physical_page"))
            location_level, verification_status = "exact_span", "verified"
        elif bbox is None or not self._is_verified_exact(location_level, verification_status):
            for item in valid_evidence:
                evidence_page = self._to_positive_int(item.get("page"))
                evidence_bbox = self._normalize_bbox(item.get("bbox"))
                if (
                    evidence_bbox
                    and self._is_verified_exact(
                        str(item.get("location_level") or ""),
                        str(item.get("verification_status") or ""),
                    )
                    and self._valid_source_bbox(evidence_page, evidence_bbox)
                ):
                    bbox = evidence_bbox
                    bbox_page = evidence_page
                    location_level = str(item["location_level"])
                    verification_status = "verified"
                    break

        source_anchors = [
            item for item in valid_evidence
            if item.get("source_identity_status") == "verified"
            and self._to_positive_int(item.get("page") or item.get("physical_page")) == bbox_page
            and self._normalize_bbox(item.get("bbox")) == bbox
            and self._is_verified_exact(
                str(item.get("location_level") or ""), str(item.get("verification_status") or "")
            )
        ]
        # Authenticate this bbox using its own quote. The first evidence item
        # may be a narrative or an operand on another physical page.
        source_anchor_matches = bool(source_anchors)
        anchor_quote = str(source_anchors[0].get("quote") or "") if source_anchors else ""
        if (
            bbox
            and bbox_page
            and source_anchor_matches
            and self._is_verified_exact(location_level, verification_status)
            and self._valid_source_bbox(bbox_page, bbox)
            and self._region_text_matches(bbox_page, bbox, anchor_quote)
        ):
            issue.bbox = bbox
            issue.page_number = bbox_page
            issue.location_level = location_level
            issue.verification_status = "verified"
            location["page"] = bbox_page
            location["bbox"] = bbox
            location["location_level"] = location_level
            location["verification_status"] = "verified"
        else:
            issue.bbox = None
            has_region = any(location.get(key) for key in ("table", "section", "row", "field"))
            location_level = "region" if has_region else "unresolved"
            verification_status = "unverified"
            issue.location_level = location_level
            issue.verification_status = verification_status
            location.pop("bbox", None)
            location["location_level"] = location_level
            location["verification_status"] = verification_status

        issue.location = location
        return issue

    def _validate_ref(self, raw_ref: Dict[str, Any]) -> Dict[str, Any]:
        """table_refs 与主证据同一认证门（RC-09：封堵引用旁路）。

        几何合法 + 自报 verified 还不够：必须通过内容锚定（bbox 区域内
        能抽到与该引用一致的原文）。伪引文、空白 bbox、跨页引用在这里
        一律降级 region/unverified 并摘除 bbox——下游标注导出只画
        verified 精确目标，降级即不再被画到原件上。
        """
        ref = dict(raw_ref)
        if ref.get("source_span") or str(ref.get("source_block_id") or "").startswith("native:"):
            ref = self._validate_evidence(ref)
        page = self._to_positive_int(ref.get("page"))
        bbox = self._normalize_bbox(ref.get("bbox"))
        quote = ""
        for key in ("quote", "text", "cell_text", "text_snippet"):
            candidate = str(ref.get(key) or "").strip()
            if candidate:
                quote = candidate
                break
        content_anchored = self._region_text_matches(page, bbox, quote)
        source_identity_complete = self._source_identity_complete(ref, page)
        if (
            page is None
            or not self._valid_document_page(page)
            or not self._is_verified_exact(
                str(ref.get("location_level") or ""),
                str(ref.get("verification_status") or ""),
            )
            or not self._valid_source_bbox(page, bbox)
            or not content_anchored
            or not source_identity_complete
        ):
            ref.pop("bbox", None)
            ref["location_level"] = (
                "region"
                if (ref.get("table") or ref.get("section") or ref.get("row") or ref.get("field"))
                else "unresolved"
            )
            ref["verification_status"] = "unverified"
            if page is None or not self._valid_document_page(page):
                ref.pop("page", None)
        else:
            ref["page"] = page
            ref["bbox"] = bbox
            ref["source_identity_status"] = "verified"
        return ref

    def _validate_evidence(self, raw_evidence: Dict[str, Any]) -> Dict[str, Any]:
        evidence = dict(raw_evidence)
        page = self._to_positive_int(evidence.get("page"))
        if page is None:
            page = self._to_positive_int(evidence.get("physical_page"))
        bbox = self._normalize_bbox(evidence.get("bbox"))
        source_identity_complete = self._source_identity_complete(evidence, page)
        native_span = ("source_span" in evidence or
                       str(evidence.get("source_block_id") or "").startswith("native:"))
        if native_span:
            bbox = self._native_span_bbox(evidence, page)
            source_identity_complete = source_identity_complete and bbox is not None
            if bbox is not None:
                evidence.update(bbox=bbox, location_level="exact_span", verification_status="verified")
            else:
                evidence.pop("bbox", None)
                evidence["location_level"] = "region"
        # Source identity hashes the exact quote, so a display text field must
        # never authenticate a different quoted amount or page.
        quote = evidence.get("quote") or evidence.get("text") or evidence.get("text_snippet")
        page_quote_matches = self._page_text_matches(page, quote)
        exact_location_valid = (
            bbox is not None
            and self._is_verified_exact(
                str(evidence.get("location_level") or ""),
                str(evidence.get("verification_status") or ""),
            )
            and self._valid_source_bbox(page, bbox)
            and self._region_text_matches(page, bbox, quote)
        )
        is_authenticated = (
            page is not None
            and self._valid_document_page(page)
            and source_identity_complete
            and page_quote_matches
        )
        evidence["source_identity_status"] = "verified" if source_identity_complete else "unverified"
        if page is not None and self._valid_document_page(page):
            evidence["page"] = page
            evidence["physical_page"] = page
        else:
            evidence.pop("page", None)
            evidence.pop("physical_page", None)
        if is_authenticated:
            evidence["verification_status"] = "verified"
            if exact_location_valid:
                evidence["bbox"] = bbox
            else:
                evidence.pop("bbox", None)
                evidence["location_level"] = "region"
        else:
            evidence["verification_status"] = "unverified"
            evidence.pop("bbox", None)
            if evidence.get("location_level") in {"exact_cell", "exact_span"}:
                evidence["location_level"] = "region"
        return evidence

    def _page_text_matches(self, page_number: Optional[int], quote: Any) -> bool:
        """Authenticate a quoted source span on the physical page without requiring a bbox."""
        needle = self._native_text(quote)
        if not needle or not page_number or not self._valid_document_page(page_number):
            return False
        try:
            document, _fitz = self._load_document()
            page_text = self._native_text(document.load_page(page_number - 1).get_text())
            return bool(page_text and needle in page_text)
        except Exception:
            return False

    def _issue_anchor_quote(self, issue: IssueItem) -> str:
        """取可用于内容锚定的引文：证据条目的原文，取不到则回退定位行/字段名。"""
        for item in issue.evidence or []:
            if isinstance(item, dict):
                for key in ("text", "quote", "text_snippet"):
                    text = str(item.get(key) or "").strip()
                    if text:
                        return text
        location = issue.location or {}
        for key in ("quote", "row", "field"):
            text = str(location.get(key) or "").strip()
            if text:
                return text
        return ""

    def _region_text_matches(
        self, page_number: Optional[int], bbox: Optional[List[float]], quote: Any
    ) -> bool:
        """bbox 区域内抽取的原文（去空白）必须包含引文（去空白）。

        PDF 不可读/区域无文本/引文为空 → False（不可认证为精确）。
        """
        needle = self._native_text(quote)
        if not needle or not page_number or not bbox:
            return False
        if not self.pdf_path or not Path(self.pdf_path).exists():
            return False
        try:
            document, _fitz = self._load_document()
            if page_number > len(document):
                return False
            page = document[page_number - 1]
            words = page.get_text("words") or []
            x0, y0, x1, y1 = bbox
            region_tokens = [
                str(word[4] or "")
                for word in words
                if not (word[2] < x0 or word[0] > x1 or word[3] < y0 or word[1] > y1)
            ]
            haystack = self._native_text("".join(region_tokens))
            return bool(haystack) and needle in haystack
        except Exception:
            return False

    def _valid_document_page(self, page_number: int) -> bool:
        if page_number <= 0:
            return False
        if not self.pdf_path or not Path(self.pdf_path).exists():
            return True
        try:
            document, _fitz = self._load_document()
            return page_number <= len(document)
        except Exception:
            return False

    def _valid_source_bbox(self, page_number: Optional[int], bbox: Optional[List[float]]) -> bool:
        if not page_number or not bbox or not self.pdf_path or not Path(self.pdf_path).exists():
            return False
        try:
            document, _fitz = self._load_document()
            if page_number > len(document):
                return False
            rect = document[page_number - 1].rect
            return (
                bbox[0] >= rect.x0
                and bbox[1] >= rect.y0
                and bbox[2] <= rect.x1
                and bbox[3] <= rect.y1
            )
        except Exception:
            return False

    def _load_document(self):
        if self._document is not None:
            import fitz

            return self._document, fitz
        import fitz

        self._document = fitz.open(self.pdf_path)
        return self._document, fitz

    @staticmethod
    def _effective_location_level(issue: IssueItem, location: Dict[str, Any]) -> str:
        value = str(issue.location_level or "unresolved")
        if value not in {"exact_cell", "exact_span"}:
            value = str(location.get("location_level") or value)
        return value

    @staticmethod
    def _effective_verification_status(issue: IssueItem, location: Dict[str, Any]) -> str:
        value = str(issue.verification_status or "unverified")
        if value != "verified":
            value = str(location.get("verification_status") or value)
        return value

    @staticmethod
    def _is_verified_exact(location_level: str, verification_status: str) -> bool:
        return location_level in {"exact_cell", "exact_span"} and verification_status == "verified"

    @staticmethod
    def _normalize_bbox(raw: Any) -> Optional[List[float]]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            values = [float(item) for item in raw]
        except (TypeError, ValueError):
            return None
        x0, y0, x1, y1 = values
        if not all(value == value and abs(value) != float("inf") for value in values):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        return values

    @staticmethod
    def _to_positive_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
