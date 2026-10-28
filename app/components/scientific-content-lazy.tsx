"use client";

import dynamic from "next/dynamic";
import type {
  ScientificContentProps,
  SourceAnchorViewProps,
  SourceMediaPreviewProps,
  StructuredVisualEvidenceViewProps,
} from "./scientific-content";

export const ScientificContent = dynamic<ScientificContentProps>(
  () => import("./scientific-content").then((module) => module.ScientificContent),
);

export const StructuredVisualEvidenceView = dynamic<StructuredVisualEvidenceViewProps>(
  () => import("./scientific-content").then((module) => module.StructuredVisualEvidenceView),
);

export const SourceAnchorView = dynamic<SourceAnchorViewProps>(
  () => import("./scientific-content").then((module) => module.SourceAnchorView),
);

export const SourceMediaPreview = dynamic<SourceMediaPreviewProps>(
  () => import("./scientific-content").then((module) => module.SourceMediaPreview),
);
