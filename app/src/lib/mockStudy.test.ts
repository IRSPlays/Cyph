import { describe, expect, it } from "vitest";
import { createMockServer } from "./mockServer";

const server = () => {
  // the mock only needs a window for its arm helpers
  (globalThis as unknown as { window: unknown }).window = globalThis;
  return createMockServer();
};
const call = (s: ReturnType<typeof createMockServer>, m: string, p: string, b?: unknown) => s.handle(m, p, b) as Promise<any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("study over the mock server", () => {
  it("runs the whole card loop: draft, approve, review, due moves out", async () => {
    const s = server();
    const drafts = (await call(s, "GET", "/study/cards?subject=Chemistry&state=draft")).cards;
    expect(drafts.length).toBeGreaterThan(0);
    const before = (await call(s, "GET", "/study/due?subject=Chemistry")).cards.length;
    await call(s, "POST", `/study/cards/${drafts[0].id}/approve`);
    expect((await call(s, "GET", "/study/due?subject=Chemistry")).cards.length).toBe(before + 1);
    const done = await call(s, "POST", `/study/cards/${drafts[0].id}/review`, { grade: 2 });
    expect(done.interval).toBe(1);
    expect((await call(s, "GET", "/study/due?subject=Chemistry")).cards.length).toBe(before);
    await expect(call(s, "POST", `/study/cards/${drafts[0].id}/review`, { grade: 9 })).rejects.toMatchObject({ status: 422 });
  });
  it("again keeps the card due today and counts a lapse", async () => {
    const s = server();
    const [c] = (await call(s, "GET", "/study/due?subject=Math")).cards;
    const r = await call(s, "POST", `/study/cards/${c.id}/review`, { grade: 0 });
    expect(r.lapses).toBe(1);
    expect(r.interval).toBe(0);
  });
  it("practice marks, logs and ranks weak topics", async () => {
    const s = server();
    const q = await call(s, "POST", "/study/practice", { subject: "Physics", kind: "structured", topic: "Kinematics" });
    const res = await call(s, "POST", `/study/practice/${q.id}/answer`, { answer: "no idea" });
    expect(res.score).toBeLessThan(res.marks);
    const topics = await call(s, "GET", "/study/topics?subject=Physics");
    expect(topics.weak[0].name).toBe("Kinematics");
    await expect(call(s, "POST", `/study/practice/${q.id}/answer`, { answer: "again" })).rejects.toMatchObject({ status: 404 });
  });
  it("imports a source into a note", async () => {
    const s = server();
    const src = await call(s, "POST", "/study/sources", { subject: "Physics", title: "Motion", mime: "application/pdf", data: "JVBERi0=" });
    expect(src.status).toBe("processing");
    await new Promise((r) => setTimeout(r, 800));
    expect((await call(s, "GET", `/study/sources/${src.id}`)).status).toBe("ready");
    expect((await call(s, "GET", "/study/notes?subject=Physics")).notes.length).toBe(1);
    await expect(call(s, "POST", "/study/sources", { subject: "Physics", mime: "application/zip" })).rejects.toMatchObject({ status: 422 });
  });
  it("notebooks keep at least one page and boards validate items", async () => {
    const s = server();
    const nb = await call(s, "POST", "/study/notebooks", { title: "N" });
    expect(nb.pages.length).toBe(1);
    await expect(call(s, "DELETE", `/study/pages/${nb.pages[0].id}`)).rejects.toMatchObject({ status: 409 });
    const b = await call(s, "POST", "/study/boards", { title: "B" });
    await expect(call(s, "PUT", `/study/boards/${b.id}/items`, { kind: "laser" })).rejects.toMatchObject({ status: 422 });
    const it = await call(s, "PUT", `/study/boards/${b.id}/items`, { kind: "sticky", x: 3 });
    expect((await call(s, "GET", `/study/boards/${b.id}`)).items[0].id).toBe(it.id);
  });
});
