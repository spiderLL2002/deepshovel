"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ScientificContent } from "./scientific-content-lazy";

export function ExpandableClaim({ value, className = "" }: { value: string; className?: string }) {
  const [expanded, setExpanded] = useState(true);
  const [overflowing, setOverflowing] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    const content = contentRef.current;
    if (!content || expanded) return;
    const measure = () => setOverflowing(content.scrollHeight > content.clientHeight + 1);
    const frame = window.requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [expanded, value]);

  return (
    <div className={`expandable-claim${className ? ` ${className}` : ""}`} data-expanded={expanded ? "true" : "false"}>
      <div id={id} ref={contentRef} className="expandable-claim-copy">
        <ScientificContent value={value} inline />
      </div>
      {overflowing || expanded ? (
        <button className="claim-disclosure-button" type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded((current) => !current)}>
          {expanded ? "收起" : "展开完整 Claim"}
        </button>
      ) : null}
    </div>
  );
}

export function ClaimListDisclosure({ total, expanded, onToggle, visibleCount = 1 }: { total: number; expanded: boolean; onToggle: () => void; visibleCount?: number }) {
  if (total <= visibleCount) return null;
  return (
    <button className="claim-list-disclosure" type="button" aria-expanded={expanded} onClick={onToggle}>
      {expanded ? "收起 Claim 列表" : `查看其余 ${total - visibleCount} 条 Claim`}
    </button>
  );
}
