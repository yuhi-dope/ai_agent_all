"use client";
import { useEffect, useRef } from "react";

interface Props {
  chart: string;
  className?: string;
}

export function MermaidDiagram({ chart, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    import("mermaid").then((mermaid) => {
      mermaid.default.initialize({ startOnLoad: false, theme: "default" });
      const id = `mermaid-${Math.random().toString(36).slice(2)}`;
      mermaid.default.render(id, chart).then(({ svg }) => {
        if (ref.current) ref.current.innerHTML = svg;
      }).catch(() => {
        // フォールバック: preタグで表示
        if (ref.current) ref.current.innerHTML = `<pre>${chart}</pre>`;
      });
    }).catch(() => {
      if (ref.current) ref.current.innerHTML = `<pre>${chart}</pre>`;
    });
  }, [chart]);

  return <div ref={ref} className={className} />;
}
