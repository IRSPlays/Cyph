import { describe, expect, it } from "vitest";
import { plain, renderRich } from "./rich";

describe("renderRich", () => {
  it("escapes markup outside maths", () => {
    const h = renderRich("<img src=x onerror=alert(1)> **bold**");
    expect(h).not.toContain("<img");
    expect(h).toContain("&lt;img");
    expect(h).toContain("<strong>bold</strong>");
  });
  it("renders inline and display maths with KaTeX", () => {
    expect(renderRich("speed $v = u + at$ here")).toContain('class="katex"');
    expect(renderRich("$$x^2$$")).toContain("katex-display");
    expect(renderRich("$$\nx^2\n$$")).toContain("katex-display");
  });
  it("renders chemistry with mhchem", () => {
    const h = renderRich("water is $\\ce{H2O}$");
    expect(h).toContain("katex");
    expect(h).not.toContain("ParseError");
    expect(h).not.toContain("\\ce");
  });
  it("keeps * and _ inside maths from becoming italics", () => {
    const h = renderRich("a *b* and $x_1 * y_2$");
    expect(h).toContain("<em>b</em>");
    expect(h).not.toContain("<em> y");
  });
  it("handles headings, lists and tables", () => {
    const h = renderRich("# Title\n\n- one\n- two\n\n1. a\n2. b\n\n| h1 | h2 |\n|---|---|\n| x | y |");
    expect(h).toContain("<h2>Title</h2>");
    expect(h.match(/<li>/g)?.length).toBe(4);
    expect(h).toContain("<th>h1</th>");
    expect(h).toContain("<td>y</td>");
  });
  it("never throws on broken maths", () => {
    expect(() => renderRich("$\\frac{1$ and $$\\bad$$")).not.toThrow();
  });
});

describe("plain", () => {
  it("strips maths markers for notifications", () => {
    expect(plain("Mass of $\\ce{H2O}$ is $18\\text{ g}$")).toBe("Mass of H2O is 18 g");
  });
});

describe("money is not maths", () => {
  it("leaves dollar prices alone but still renders maths", () => {
    expect(renderRich("It costs $5 and $10 today")).toContain("$5 and $10");
    expect(renderRich("Solve $2x+1=5$")).toContain("katex");
  });
});
