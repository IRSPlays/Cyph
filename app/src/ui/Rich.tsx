import { useMemo } from "react";
import "katex/dist/katex.min.css";
import { renderRich } from "../lib/rich";

/** Study text with maths. Output is escaped HTML plus KaTeX markup (see lib/rich.ts), so it is safe to inject. */
export function Rich({ text, className = "", inline = false }: { text: string; className?: string; inline?: boolean }) {
  const html = useMemo(() => {
    const h = renderRich(text);
    return inline ? h.replace(/^<p>|<\/p>$/g, "") : h;
  }, [text, inline]);
  const Tag = inline ? "span" : "div";
  return <Tag className={"rich " + className} dangerouslySetInnerHTML={{ __html: html }} />;
}
