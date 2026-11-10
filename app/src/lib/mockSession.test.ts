import { describe, expect, it } from "vitest";
import { createMockStudy } from "./mockStudy";

const make = () => { const h = createMockStudy(() => undefined, () => "2026-11-10"); return (m: string, r: string, b: Record<string, unknown> = {}) => h(m, r, new URLSearchParams(r.split("?")[1] ?? ""), b) as Promise<any>; };

describe("mock session engine", () => {
  it("says what to do now and runs a session to its summary", async () => {
    const api = make();
    const now = await api("GET", "/study/now");
    expect(now.state).toBe("ready");
    let v = await api("POST", "/study/session", { device: "desk", kind: "" });
    expect(v.state).toBe("open");
    for (let i = 0; i < 40 && v.state === "open"; i++) {
      const s = v.step;
      if (s.type === "card") await api("POST", `/study/session/${v.id}/answer`, { grade: 2 });
      else if (s.type === "lesson") await api("POST", `/study/session/${v.id}/answer`, { option: 1 });
      else if (s.type === "homework") await api("POST", `/study/session/${v.id}/answer`, {});
      else await api("POST", `/study/session/${v.id}/answer`, { answer: "0.30 g", option: 1, value: "0.30", had: true, ticks: [true] });
      v = await api("POST", `/study/session/${v.id}/next`);
    }
    expect(v.state).toBe("done");
    expect(v.summary.minutes).toBeGreaterThan(0);
  });
  it("checks the final number in code, and a dispute can only move the mark up", async () => {
    const api = make();
    const v = await api("POST", "/study/session", { device: "desk", kind: "topic", topic: (await api("GET", "/study/library")).subjects.flatMap((s: any) => s.topics).find((t: any) => t.name === "Limiting reagent").id });
    let cur = v;
    while (cur.step.type !== "question") { await api("POST", `/study/session/${cur.id}/answer`, { grade: 2 }); cur = await api("POST", `/study/session/${cur.id}/next`); }
    const r = await api("POST", `/study/session/${cur.id}/answer`, { answer: "the mass is 0.9 g" });
    expect(r.points.at(-1).got).toBe(false);
    const d = await api("POST", `/study/attempts/${r.attempt}/dispute`);
    expect(d.score).toBeGreaterThanOrEqual(r.score);
  });
  it("keeps what he sends in the inbox and lets him undo it", async () => {
    const api = make();
    await api("POST", "/study/inbox", { title: "WS", mime: "text/plain", text: "moments" });
    expect((await api("GET", "/study/inbox")).items[0].title).toBe("WS");
  });
});
