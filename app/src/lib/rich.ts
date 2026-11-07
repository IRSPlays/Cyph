/** Study text -> safe HTML: a small markdown subset (headings, bold, italic, code, lists, tables, paragraphs) plus
 *  maths: $inline$ and $$display$$ LaTeX, with \ce{...} chemistry (mhchem), rendered by KaTeX. Everything outside a
 *  maths span is HTML-escaped first, and KaTeX runs with trust off, so a note can never inject markup. */
import katex from "katex";
import "katex/contrib/mhchem";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function tex(src: string, display: boolean): string {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false, trust: false, strict: "ignore", output: "html" });
  } catch {
    return esc(src);
  }
}

/** Inline formatting of an already-split text run (no maths in it). */
function inline(s: string): string {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}

/** Pulls maths out first (so `*` and `_` inside it are safe), formats the rest, then puts the rendered maths back. */
function line(s: string, hold: string[]): string {
  const withHolds = s.replace(/\$\$([^$]+)\$\$|\$([^$\n]+)\$/g, (_m, d, i) => {
    hold.push(d !== undefined ? tex(d.trim(), true) : tex(i.trim(), false));
    return `\u0000${hold.length - 1}\u0000`;
  });
  return inline(withHolds).replace(/\u0000(\d+)\u0000/g, (_m, i) => hold[Number(i)]);
}

export function renderRich(text: string): string {
  const hold: string[] = [];
  const out: string[] = [];
  const lines = String(text ?? "").replace(/\r/g, "").split("\n");
  let list: "ul" | "ol" | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) out.push(`<p>${para.map((l) => line(l, hold)).join("<br>")}</p>`);
    para = [];
  };
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m: RegExpMatchArray | null;
    if (/^\s*\$\$\s*$/.test(l)) {                             // a display block spread over lines
      const end = lines.findIndex((x, j) => j > i && /^\s*\$\$\s*$/.test(x));
      if (end > i) {
        flushPara();
        closeList();
        out.push(tex(lines.slice(i + 1, end).join("\n"), true));
        i = end;
        continue;
      }
    }
    if (!l.trim()) {
      flushPara();
      closeList();
    } else if ((m = l.match(/^(#{1,3})\s+(.*)$/))) {
      flushPara();
      closeList();
      out.push(m[1].length === 1 ? `<h2>${line(m[2], hold)}</h2>` : `<h3>${line(m[2], hold)}</h3>`);
    } else if ((m = l.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara();
      if (list !== "ul") (closeList(), out.push("<ul>"), (list = "ul"));
      out.push(`<li>${line(m[1], hold)}</li>`);
    } else if ((m = l.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (list !== "ol") (closeList(), out.push("<ol>"), (list = "ol"));
      out.push(`<li>${line(m[1], hold)}</li>`);
    } else if (/^\s*\|.*\|\s*$/.test(l)) {
      flushPara();
      closeList();
      const rows: string[] = [l];
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) rows.push(lines[++i]);
      const cells = (r: string) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      const head = rows.length > 1 && /^\s*\|[\s:|-]+\|\s*$/.test(rows[1]);
      out.push("<table>" + body.map((r, j) => "<tr>" + cells(r).map((c) => `<${head && j === 0 ? "th" : "td"}>${line(c, hold)}</${head && j === 0 ? "th" : "td"}>`).join("") + "</tr>").join("") + "</table>");
    } else {
      closeList();
      para.push(l);
    }
  }
  flushPara();
  closeList();
  return out.join("");
}

/** Plain text of a card/note for places that cannot show maths (notifications, titles): `$` and `\ce{}` removed. */
export function plain(text: string): string {
  return String(text ?? "").replace(/\\ce\{([^}]*)\}/g, "$1").replace(/\$+/g, "").replace(/\\(text|mathrm)\{([^}]*)\}/g, "$2").replace(/[\\{}^_]/g, "").replace(/\s+/g, " ").trim();
}
