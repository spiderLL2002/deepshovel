import type { ReactNode, SVGProps } from "react";

export type UiIconName =
  | "activity"
  | "atlas"
  | "arrow-left"
  | "check"
  | "chevron-down"
  | "close"
  | "document"
  | "external"
  | "graph"
  | "library"
  | "more"
  | "refresh"
  | "review"
  | "search"
  | "upload"
  | "visuals"
  | "warning";

type Props = Omit<SVGProps<SVGSVGElement>, "children"> & {
  name: UiIconName;
  size?: number;
};

const iconPaths: Record<UiIconName, ReactNode> = {
  activity: <><path d="M4 12h3l2-5 4 10 2-5h5" /><path d="M5 4.8A9 9 0 1 1 3.2 15" /></>,
  atlas: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.6 2.35 4 5.2 4 8.5s-1.4 6.15-4 8.5M12 3.5C9.4 5.85 8 8.7 8 12s1.4 6.15 4 8.5" /><circle cx="7" cy="9" r="1" fill="currentColor" stroke="none" /><circle cx="16.5" cy="14.5" r="1" fill="currentColor" stroke="none" /></>,
  "arrow-left": <><path d="m15 18-6-6 6-6" /><path d="M9 12h11" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  "chevron-down": <path d="m7 10 5 5 5-5" />,
  close: <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>,
  document: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v5h5" /><path d="M10 12h5M10 16h5" /></>,
  external: <><path d="M14 4h6v6" /><path d="m20 4-9 9" /><path d="M18 13v6H5V6h6" /></>,
  graph: <><circle cx="6" cy="12" r="2.25" /><circle cx="17.5" cy="6" r="2.25" /><circle cx="17.5" cy="18" r="2.25" /><path d="m8 11 7.5-4M8 13l7.5 4" /></>,
  library: <><path d="M4 5.5h5v14H4zM10.5 4h4v15.5h-4zM16 6h4v13.5h-4z" /><path d="M3 20h18" /></>,
  more: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>,
  refresh: <><path d="M20 7v5h-5" /><path d="M19 12a7 7 0 1 0-1.8 4.7" /></>,
  review: <><path d="M5 4h14v16H5z" /><path d="M8 8h8M8 12h5" /><path d="m14.5 16 1.5 1.5 3-3" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  upload: <><path d="M12 16V4" /><path d="m7.5 8.5 4.5-4.5 4.5 4.5" /><path d="M5 14v6h14v-6" /></>,
  visuals: <><path d="M4 19V5M4 19h16" /><path d="m7 15 3-4 3 2 4-6" /><circle cx="7" cy="15" r="1" fill="currentColor" stroke="none" /><circle cx="10" cy="11" r="1" fill="currentColor" stroke="none" /><circle cx="13" cy="13" r="1" fill="currentColor" stroke="none" /><circle cx="17" cy="7" r="1" fill="currentColor" stroke="none" /></>,
  warning: <><path d="M12 3 2.8 20h18.4z" /><path d="M12 9v5M12 17.2v.1" /></>,
};

export function UiIcon({ name, size = 18, className, ...props }: Props) {
  return (
    <svg
      aria-hidden="true"
      className={["ui-icon", className].filter(Boolean).join(" ")}
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7">
        {iconPaths[name]}
      </g>
    </svg>
  );
}

export function EvidenceMark({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" className={["evidence-mark", className].filter(Boolean).join(" ")} viewBox="0 0 32 32">
      <path className="evidence-mark-vessel" d="M8 5v15l8 7 8-7V5" />
      <path className="evidence-mark-trace" d="M4 8h13" />
      <circle className="evidence-mark-node" cx="22" cy="8" r="2.25" />
      <path className="evidence-mark-tail" d="M24.25 8H28" />
    </svg>
  );
}
