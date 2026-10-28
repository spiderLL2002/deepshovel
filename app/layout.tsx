import type { Metadata, Viewport } from "next";
import "@fontsource-variable/geist-mono/wght.css";
import "@fontsource-variable/instrument-sans/standard.css";
import "@fontsource-variable/noto-sans-sc/wght.css";
import "katex/dist/katex.min.css";
import "./styles/foundation.css";
import "./styles/shell.css";
import "./styles/search-synthesis.css";
import "./styles/library-paper-review.css";
import "./styles/knowledge-visual.css";
import "./styles/geochemical-atlas.css";
import "./styles/overlays-responsive.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://deepshovel.iiot-shxt.xyz"),
  title: "DeepShovel · Evidence Bench",
  description: "面向科研审阅的论文证据平台：以 Claim、观察结果和原文位置为基础，比对后续文献的确认、限定与质疑。",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
  openGraph: {
    title: "DeepShovel · Evidence Bench",
    description: "A precise review bench for claims, source anchors, citation reception, and scientific evidence relationships.",
    type: "website",
    images: [{ url: "/og.png", width: 1731, height: 909, alt: "DeepShovel Evidence Bench scientific review workspace" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "DeepShovel · Evidence Bench",
    description: "A precise review bench for claims, source anchors, citation reception, and scientific evidence relationships.",
    images: ["/og.png"],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
