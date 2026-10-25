"""Prefix every selector of a flat CSS file with a page class, so the page styles ported from the mockups cannot leak
into each other once Vite bundles them all together.   python tools/scope_css.py <in.css> <out.css> <.pg-class>"""
import re
import sys


def block_end(css: str, i: int) -> int:
    """index just past the } that closes the { at css[i]"""
    depth = 0
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced braces")


def scope(css: str, prefix: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i = [], 0
    while i < len(css):
        if css[i].isspace():
            out.append(css[i])
            i += 1
            continue
        brace = css.index("{", i)
        head = css[i:brace].strip()
        end = block_end(css, brace)
        body = css[brace + 1:end - 1]
        if head.startswith("@media") or head.startswith("@supports"):
            out.append(f"{head}{{{scope(body, prefix)}}}")
        elif head.startswith("@"):
            out.append(f"{head}{{{body}}}")
        else:
            sels = []
            for s in head.split(","):
                s = s.strip()
                if s.startswith(prefix) or s.startswith(".deck" + prefix):
                    sels.append(s)
                elif s in (":root", "html", "body") or s.startswith("html") or s.startswith("body"):
                    sels.append(s)
                elif s.startswith(".deck"):
                    sels.append(".deck" + prefix + s[5:])        # .deck:after  ->  .deck.pg-x:after
                else:
                    sels.append(f"{prefix} {s}")
            out.append(f"{','.join(sels)}{{{body}}}")
        i = end
    return "".join(out)


if __name__ == "__main__":
    src, dst, prefix = sys.argv[1:4]
    open(dst, "w", encoding="utf-8").write(scope(open(src, encoding="utf-8").read(), prefix) + "\n")
