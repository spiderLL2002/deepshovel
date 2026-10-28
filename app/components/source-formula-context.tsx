"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { SourceAnchor } from "@/lib/evidence-graph";
import { expandQuoteMathContext, prepareScientificMarkdown } from "../scientific-markdown";
import type { DocumentTextPageDto } from "../workspace-types";

const EMPTY_CONTEXT = new Map<string, string>();
const SourceFormulaContext = createContext(EMPTY_CONTEXT);

/** Read existing text only; frozen evidence quotes and their hashes stay unchanged. */
export function SourceFormulaContextProvider({
  snapshotId, versionId, sha256, anchors, children,
}: {
  snapshotId: string;
  versionId: string;
  sha256: string;
  anchors: SourceAnchor[];
  children: ReactNode;
}) {
  const [loaded, setLoaded] = useState<{ snapshotId: string; quotes: Map<string, string> } | null>(null);
  useEffect(() => {
    const incomplete = anchors.filter((anchor) => anchor.artifact_version_id === versionId
      && anchor.anchor_type === "text" && anchor.quote
      && prepareScientificMarkdown(anchor.quote).incompleteMath);
    if (!incomplete.length) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(snapshotId)}/document?section=text`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) return;
        const payload = await response.json() as DocumentTextPageDto;
        if (payload.snapshotId !== snapshotId || payload.section !== "text"
          || payload.rootArtifactVersionId !== versionId || payload.sourceVersion?.id !== versionId
          || payload.sourceVersion.sha256 !== sha256 || typeof payload.markdown !== "string") return;
        const quotes = new Map<string, string>();
        for (const anchor of incomplete) {
          const expanded = expandQuoteMathContext(payload.markdown, anchor.quote!);
          if (expanded) quotes.set(anchor.id, expanded);
        }
        if (!controller.signal.aborted) setLoaded({ snapshotId, quotes });
      } catch {
        // If stored text cannot be read or matched, retain the explicit fragment display.
      }
    })();
    return () => controller.abort();
  }, [snapshotId, versionId, sha256, anchors]);

  return <SourceFormulaContext.Provider value={loaded?.snapshotId === snapshotId ? loaded.quotes : EMPTY_CONTEXT}>
    {children}
  </SourceFormulaContext.Provider>;
}

export function useSourceFormulaContext(anchorId: string) {
  return useContext(SourceFormulaContext).get(anchorId);
}
