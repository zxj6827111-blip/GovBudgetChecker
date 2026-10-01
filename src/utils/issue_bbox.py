"""Validate source-provided exact bboxes without searching for similar PDF text."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.schemas.issues import IssueItem


class PDFBBoxLocator:
    """Keep only source-verified exact coordinates and in-range physical pages."""

    def __init__(self, pdf_path: str):
        self.pdf_path = str(pdf_path or "")
        self._document = None

    def close(self) -> None:
        if self._document is not None:
            try:
                self._document.close()
            except Exception:
                pass
        self._document = None

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

        bbox = self._normalize_bbox(issue.bbox or location.get("bbox"))
        bbox_page = page
        if bbox is None or not self._is_verified_exact(location_level, verification_status):
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

        if (
            bbox
            and bbox_page
            and self._is_verified_exact(location_level, verification_status)
            and self._valid_source_bbox(bbox_page, bbox)
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
        ref = dict(raw_ref)
        page = self._to_positive_int(ref.get("page"))
        bbox = self._normalize_bbox(ref.get("bbox"))
        if (
            page is None
            or not self._valid_document_page(page)
            or not self._is_verified_exact(
                str(ref.get("location_level") or ""),
                str(ref.get("verification_status") or ""),
            )
            or not self._valid_source_bbox(page, bbox)
        ):
            ref.pop("bbox", None)
            ref["location_level"] = "region" if ref.get("table") or ref.get("section") else "unresolved"
            ref["verification_status"] = "unverified"
            if page is None or not self._valid_document_page(page):
                ref.pop("page", None)
        else:
            ref["page"] = page
            ref["bbox"] = bbox
        return ref

    def _validate_evidence(self, raw_evidence: Dict[str, Any]) -> Dict[str, Any]:
        evidence = dict(raw_evidence)
        page = self._to_positive_int(evidence.get("page"))
        bbox = self._normalize_bbox(evidence.get("bbox"))
        is_valid = (
            page is not None
            and self._valid_document_page(page)
            and self._is_verified_exact(
                str(evidence.get("location_level") or ""),
                str(evidence.get("verification_status") or ""),
            )
            and self._valid_source_bbox(page, bbox)
        )
        if page is not None and self._valid_document_page(page):
            evidence["page"] = page
        else:
            evidence.pop("page", None)
        if is_valid and bbox is not None:
            evidence["bbox"] = bbox
        else:
            evidence.pop("bbox", None)
            if evidence.get("location_level") in {"exact_cell", "exact_span"}:
                evidence["location_level"] = "region"
            evidence["verification_status"] = "unverified"
        return evidence

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
