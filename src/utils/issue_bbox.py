"""Locate approximate issue bbox regions directly from the source PDF."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.schemas.issues import IssueItem


class PDFBBoxLocator:
    def __init__(self, pdf_path: str):
        self.pdf_path = str(pdf_path or "")
        self._document = None
        self._fitz = None

    def close(self) -> None:
        if self._document is not None:
            try:
                self._document.close()
            except Exception:
                pass
        self._document = None

    def locate(self, issue: IssueItem) -> IssueItem:
        if not self.pdf_path or not Path(self.pdf_path).exists():
            return issue

        location = dict(issue.location or {})
        refs = location.get("table_refs")
        if isinstance(refs, list):
            location["table_refs"] = self._locate_refs(issue=issue, refs=refs)

        page_number, bbox = self._resolve_primary_bbox(issue=issue, location=location)
        if not page_number or not bbox:
            issue.location = location
            return issue

        issue.page_number = page_number
        issue.bbox = bbox
        if issue.evidence:
            issue.evidence[0]["page"] = page_number
            issue.evidence[0]["bbox"] = bbox

        location["page"] = page_number
        issue.location = location
        return issue

    def _locate_refs(self, *, issue: IssueItem, refs: Sequence[Any]) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            item = dict(ref)
            page_number, bbox = self._locate_target(issue=issue, target=item)
            if page_number:
                item["page"] = page_number
            if bbox:
                item["bbox"] = bbox
            resolved.append(item)
        return resolved

    def _resolve_primary_bbox(
        self,
        *,
        issue: IssueItem,
        location: Dict[str, Any],
    ) -> tuple[Optional[int], Optional[List[float]]]:
        issue_page = self._first_page(
            location.get("page"),
            *(location.get("pages") or [] if isinstance(location.get("pages"), list) else []),
            issue.evidence[0].get("page") if issue.evidence else None,
            issue.page_number,
        )
        issue_bbox = self._normalize_bbox(issue.bbox)
        if issue_page and issue_bbox:
            return issue_page, issue_bbox

        refs = location.get("table_refs")
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                page_number = self._to_positive_int(ref.get("page"))
                bbox = self._normalize_bbox(ref.get("bbox"))
                if page_number and bbox:
                    return page_number, bbox

        return self._locate_target(issue=issue, target=self._choose_target(issue=issue, location=location))

    def _choose_target(self, *, issue: IssueItem, location: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        refs = location.get("table_refs")
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                if ref.get("page") and self._ref_has_search_terms(ref):
                    return dict(ref)

        if location.get("page") and self._ref_has_search_terms(location):
            return dict(location)

        issue_page = self._first_page(
            location.get("page"),
            *(location.get("pages") or [] if isinstance(location.get("pages"), list) else []),
            issue.evidence[0].get("page") if issue.evidence else None,
            issue.page_number,
        )
        if issue_page and self._issue_has_text_terms(issue):
            return {"page": issue_page}

        if issue.evidence:
            page = issue.evidence[0].get("page")
            if page:
                return {
                    "page": page,
                    "field": location.get("field"),
                    "row": location.get("row"),
                    "code": location.get("code"),
                    "subject": location.get("subject"),
                }
        return None

    def _locate_target(
        self,
        *,
        issue: IssueItem,
        target: Optional[Dict[str, Any]],
    ) -> tuple[Optional[int], Optional[List[float]]]:
        if not isinstance(target, dict):
            return None, None

        page_number = self._to_positive_int(target.get("page"))
        if not page_number:
            return None, None

        bbox = self._normalize_bbox(target.get("bbox"))
        if bbox:
            return page_number, bbox

        terms = self._build_terms(issue=issue, target=target)
        if not terms:
            return page_number, None

        return page_number, self._search(page_number=page_number, terms=terms, target=target)

    def _build_terms(self, *, issue: IssueItem, target: Dict[str, Any]) -> List[str]:
        terms: List[str] = []
        for key in ("row", "code", "subject", "field", "section", "col"):
            raw = str(target.get(key) or "").strip()
            if not raw:
                continue
            terms.append(raw)

        terms.extend(self._expand_table_terms(target.get("table")))

        evidence_text = str(issue.display.evidence_text if issue.display else issue.text_snippet or "").strip()
        if evidence_text:
            first_line = evidence_text.splitlines()[0].strip()
            if len(first_line) >= 6:
                terms.append(first_line[:40])
        terms.extend(self._extract_text_terms(issue=issue, target=target))

        seen = set()
        ordered: List[str] = []
        for term in terms:
            if len(term) < 4:
                continue
            if term in seen:
                continue
            seen.add(term)
            ordered.append(term)
        return ordered

    def _search(
        self,
        *,
        page_number: int,
        terms: Sequence[str],
        target: Optional[Dict[str, Any]] = None,
    ) -> Optional[List[float]]:
        try:
            document, _fitz = self._load_document()
        except Exception:
            return None

        if page_number < 1 or page_number > len(document):
            return None

        page = document[page_number - 1]

        if isinstance(target, dict):
            field_match = self._first_match(
                page,
                [target.get("field"), target.get("col")],
                skip_generic=True,
            )
            row_match = self._row_match(
                page,
                target.get("row"),
                below_y=float(field_match.y1) if field_match is not None else None,
            )
            if field_match is not None and row_match is not None:
                cell_bbox = self._compose_cell_bbox(row_match=row_match, field_match=field_match)
                if cell_bbox is not None:
                    return cell_bbox

        rect = self._first_match(page, terms, skip_generic=True)
        if rect is None:
            rect = self._first_match(page, terms, skip_generic=False)
        if rect is None:
            return None
        return self._rect_to_bbox(rect)

    def _load_document(self):
        if self._document is not None and self._fitz is not None:
            return self._document, self._fitz
        import fitz

        self._fitz = fitz
        self._document = fitz.open(self.pdf_path)
        return self._document, self._fitz

    def _expand_table_terms(self, raw_table: Any) -> List[str]:
        token = str(raw_table or "").strip()
        if not token:
            return []

        terms: List[str] = []
        try:
            from src.utils.issue_location import _normalize_table_name

            normalized = _normalize_table_name(token)
            if normalized:
                terms.append(str(normalized).strip())
        except Exception:
            pass

        upper_token = token.upper()
        if upper_token.startswith("BUD_T"):
            try:
                from src.engine.budget_rules import BUDGET_TABLE_SPECS

                for spec in BUDGET_TABLE_SPECS:
                    if str(spec.get("key") or "").upper() != upper_token:
                        continue
                    for alias in spec.get("aliases") or []:
                        alias_text = str(alias or "").strip()
                        if alias_text:
                            terms.append(alias_text)
                    break
            except Exception:
                pass

        terms.append(token)
        seen = set()
        ordered: List[str] = []
        for term in terms:
            if not term or term in seen:
                continue
            seen.add(term)
            ordered.append(term)
        return ordered

    def _extract_text_terms(self, *, issue: IssueItem, target: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for key in ("text", "text_snippet", "context", "original", "quote"):
            raw = str(target.get(key) or "").strip()
            if raw:
                candidates.append(raw)

        for evidence in issue.evidence or []:
            if not isinstance(evidence, dict):
                continue
            for key in ("text", "text_snippet", "context", "original", "quote"):
                raw = str(evidence.get(key) or "").strip()
                if raw:
                    candidates.append(raw)

        if issue.text_snippet:
            candidates.append(str(issue.text_snippet))
        if issue.display and issue.display.evidence_text:
            candidates.append(str(issue.display.evidence_text))

        terms: List[str] = []
        for text in candidates:
            terms.extend(self._text_to_search_terms(text))

        seen = set()
        ordered: List[str] = []
        for term in terms:
            if not term or term in seen:
                continue
            seen.add(term)
            ordered.append(term)
        return ordered

    @classmethod
    def _text_to_search_terms(cls, text: Any) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []

        normalized = re.sub(r"\s+", " ", raw).strip()
        if len(normalized) < 4:
            return []

        fragments: List[str] = []
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            lines = [normalized]

        for line in lines[:3]:
            pieces = [
                piece.strip()
                for piece in re.split(r"[，。；;、,:：()（）\[\]【】]", line)
                if piece and piece.strip()
            ]
            if not pieces:
                pieces = [line]
            for piece in pieces[:4]:
                fragments.extend(cls._slice_text_fragments(piece))

        seen = set()
        ordered: List[str] = []
        for fragment in fragments:
            cleaned = str(fragment or "").strip()
            if len(cleaned) < 4 or cleaned in seen:
                continue
            seen.add(cleaned)
            ordered.append(cleaned)
        return ordered

    @staticmethod
    def _slice_text_fragments(text: str) -> List[str]:
        cleaned = str(text or "").strip()
        if len(cleaned) < 4:
            return []

        variants: List[str] = [cleaned]
        compact = cleaned.replace(" ", "")
        if compact and compact != cleaned:
            variants.append(compact)

        base = compact or cleaned
        for width in (48, 32, 20, 12):
            if len(base) <= width:
                continue
            starts = [
                0,
                max(0, (len(base) // 2) - (width // 2)),
                max(0, len(base) - width),
            ]
            for start in starts:
                variants.append(base[start : start + width])

        return variants

    @staticmethod
    def _first_match(page: Any, terms: Sequence[Any], *, skip_generic: bool) -> Any:
        for raw_term in terms:
            term = str(raw_term or "").strip()
            if not term:
                continue
            if skip_generic and term in {"合计", "总计"}:
                continue
            matches = page.search_for(term)
            if matches:
                return matches[0]
        return None

    @classmethod
    def _row_match(cls, page: Any, term: Any, *, below_y: Optional[float]) -> Any:
        token = str(term or "").strip()
        if not token:
            return None
        matches = page.search_for(token)
        if not matches:
            return None
        if below_y is not None:
            lower_matches = [item for item in matches if float(item.y0) > below_y + 1]
            if lower_matches:
                return lower_matches[0]
        return matches[0]

    @classmethod
    def _compose_cell_bbox(cls, *, row_match: Any, field_match: Any) -> Optional[List[float]]:
        x0 = min(float(field_match.x0), float(field_match.x1)) - 2.0
        x1 = max(float(field_match.x0), float(field_match.x1)) + 2.0
        y0 = min(float(row_match.y0), float(row_match.y1)) - 2.0
        y1 = max(float(row_match.y0), float(row_match.y1)) + 2.0
        if x1 <= x0 or y1 <= y0:
            return None
        return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]

    @staticmethod
    def _rect_to_bbox(rect: Any) -> List[float]:
        return [
            round(float(rect.x0), 2),
            round(float(rect.y0), 2),
            round(float(rect.x1), 2),
            round(float(rect.y1), 2),
        ]

    @staticmethod
    def _ref_has_search_terms(ref: Dict[str, Any]) -> bool:
        return any(str(ref.get(key) or "").strip() for key in ("row", "code", "subject", "field", "section", "col", "table"))

    def _issue_has_text_terms(self, issue: IssueItem) -> bool:
        return bool(self._extract_text_terms(issue=issue, target={}))

    @staticmethod
    def _normalize_bbox(raw: Any) -> Optional[List[float]]:
        if not isinstance(raw, Sequence) or len(raw) != 4:
            return None
        values: List[float] = []
        for item in raw:
            try:
                values.append(round(float(item), 2))
            except Exception:
                return None
        if values[2] <= values[0] or values[3] <= values[1]:
            return None
        return values

    @staticmethod
    def _to_positive_int(value: Any) -> Optional[int]:
        try:
            parsed = int(float(value))
        except Exception:
            return None
        return parsed if parsed > 0 else None

    def _first_page(self, *values: Any) -> Optional[int]:
        for value in values:
            if isinstance(value, list):
                iterable: Iterable[Any] = value
            else:
                iterable = [value]
            for item in iterable:
                page = self._to_positive_int(item)
                if page:
                    return page
        return None

