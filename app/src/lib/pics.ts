import type { Pic } from "./types";

export const MAX_PICS = 4;
const SIDE = 1600; // longest side sent; he does not need more, and it keeps a screenshot well under the server's 8 MB

const load = (src: string) =>
  new Promise<HTMLImageElement>((ok, no) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => no(new Error("That file is not a picture I can read."));
    im.src = src;
  });

const readAsDataUrl = (f: Blob) =>
  new Promise<string>((ok, no) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result));
    r.onerror = () => no(new Error("Could not read that file."));
    r.readAsDataURL(f);
  });

/** A picked or pasted image, shrunk and re-encoded as JPEG (transparent areas become white). */
export async function toPic(file: Blob): Promise<Pic> {
  if (!file.type.startsWith("image/")) throw new Error("That is not a picture.");
  const im = await load(await readAsDataUrl(file));
  const k = Math.min(1, SIDE / Math.max(im.naturalWidth, im.naturalHeight));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(im.naturalWidth * k));
  c.height = Math.max(1, Math.round(im.naturalHeight * k));
  const g = c.getContext("2d");
  if (!g) throw new Error("Could not prepare that picture.");
  g.fillStyle = "#fff";
  g.fillRect(0, 0, c.width, c.height);
  g.drawImage(im, 0, 0, c.width, c.height);
  const url = c.toDataURL("image/jpeg", 0.85);
  return { mime: "image/jpeg", data: url.slice(url.indexOf(",") + 1), url };
}
