"""Value-free audit of every Compiler rewrite of model output.

The model owns graph semantics; the Compiler owns provenance hydration and a
few deterministic repairs. Each such rewrite, every Claim the Compiler removes,
and every optional projection that failed is recorded here so the import
envelope states exactly how the stored graph differs from the model's output.
Entries carry graph-local identifiers and fixed codes only, never source text.
"""

from __future__ import annotations

from typing import Any

from .graph_contract import PaperEvidenceGraph

AUDIT_SCHEMA_VERSION = "compiler-audit/1.0"
MAX_AUDIT_ENTRIES = 512
_MAX_DETAIL = 300
_SELECTION_FIELDS = ("figure", "table", "row_range", "series", "paragraph", "section")


class CompilerAudit:
    def __init__(self) -> None:
        self._entries: list[dict[str, str | None]] = []
        self._omitted = 0
        self._warnings: list[str] = []

    def record(
        self, kind: str, target: str | None = None, detail: str | None = None
    ) -> None:
        if len(self._entries) >= MAX_AUDIT_ENTRIES:
            self._omitted += 1
            return
        self._entries.append(
            {
                "kind": kind,
                "target": target,
                "detail": None if detail is None else detail[:_MAX_DETAIL],
            }
        )

    def warn(self, code: str) -> None:
        if code not in self._warnings:
            self._warnings.append(code[:_MAX_DETAIL])

    def kinds(self) -> list[str]:
        return [str(entry["kind"]) for entry in self._entries]

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "entries": [dict(entry) for entry in self._entries],
            "omitted_entries": self._omitted,
            "warnings": list(self._warnings),
        }

    def record_graph_rewrites(
        self,
        before: PaperEvidenceGraph,
        after: PaperEvidenceGraph,
        *,
        stage: str,
    ) -> None:
        """Record the model-visible differences one deterministic stage made."""

        if before is after:
            return
        old_anchors = {item.id: item for item in before.anchors}
        for anchor in after.anchors:
            previous = old_anchors.get(anchor.id)
            if previous is None:
                self.record("anchor_added", anchor.id, stage)
                continue
            if previous.anchor_type != anchor.anchor_type:
                self.record(
                    "anchor_retyped",
                    anchor.id,
                    f"{stage}:{previous.anchor_type}->{anchor.anchor_type}",
                )
            cleared = [
                name
                for name in _SELECTION_FIELDS
                if getattr(previous.locator, name) is not None
                and getattr(anchor.locator, name) is None
            ]
            if previous.locator.column_names and not anchor.locator.column_names:
                cleared.append("column_names")
            if cleared:
                self.record(
                    "anchor_selection_cleared",
                    anchor.id,
                    f"{stage}:{','.join(cleared)}",
                )
            if (
                previous.locator.region != anchor.locator.region
                and previous.locator.region is not None
            ):
                self.record("anchor_region_replaced", anchor.id, stage)
            if (
                previous.locator.page is not None
                and previous.locator.page != anchor.locator.page
            ):
                self.record("anchor_page_replaced", anchor.id, stage)
            if (
                previous.locator.quote is not None
                and previous.locator.quote != anchor.locator.quote
            ):
                self.record("anchor_quote_replaced", anchor.id, stage)
        for collection in ("claims", "observations", "reasoning", "assumptions"):
            previous_nodes = {item.id: item for item in getattr(before, collection)}
            for node in getattr(after, collection):
                old = previous_nodes.get(node.id)
                if old is None:
                    continue
                added = [item for item in node.anchor_ids if item not in old.anchor_ids]
                if added:
                    self.record(
                        "anchor_reference_added",
                        node.id,
                        f"{stage}:{','.join(added)}"[:_MAX_DETAIL],
                    )


__all__ = ["AUDIT_SCHEMA_VERSION", "MAX_AUDIT_ENTRIES", "CompilerAudit"]
