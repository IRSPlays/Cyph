import { describe, expect, it } from "vitest";
import { chatReducer, initialChat, parseMood, type ChatState } from "./chat";
import type { ClientEvent } from "./types";

const server = (state: ChatState, msg: ClientEvent) => chatReducer(state, { kind: "server", msg });

describe("parseMood", () => {
  it("splits a leading mood tag from the text", () => {
    expect(parseMood("[happy] Hello there")).toEqual({ mood: "happy", body: "Hello there" });
  });
  it("shows nothing while the tag is still arriving", () => {
    expect(parseMood("[ha")).toEqual({ body: "" });
    expect(parseMood("[")).toEqual({ body: "" });
  });
  it("leaves untagged text alone, and only the first tag counts", () => {
    expect(parseMood("no tag")).toEqual({ body: "no tag" });
    expect(parseMood("[sad] a [b] c")).toEqual({ mood: "sad", body: "a [b] c" });
  });
});

describe("chatReducer", () => {
  it("adds the user's line and marks busy", () => {
    const s = chatReducer(initialChat, { kind: "user", text: "hi" });
    expect(s.messages.map((m) => [m.role, m.text])).toEqual([["user", "hi"]]);
    expect(s.busy).toBe(true);
  });

  it("builds one streaming reply out of deltas, then the final text replaces it", () => {
    let s = chatReducer(initialChat, { kind: "user", text: "hi" });
    s = server(s, { type: "delta", text: "[wave] He" });
    s = server(s, { type: "delta", text: "llo" });
    const streaming = s.messages.filter((m) => m.role === "cyph");
    expect(streaming).toHaveLength(1);
    expect(streaming[0].text).toBe("[wave] Hello");
    expect(streaming[0].streaming).toBe(true);
    expect(s.mood).toBe("wave");
    s = server(s, { type: "final", text: "[happy] Hello, Cypher!" });
    const cyph = s.messages.filter((m) => m.role === "cyph");
    expect(cyph).toHaveLength(1);
    expect(cyph[0].text).toBe("[happy] Hello, Cypher!");
    expect(cyph[0].streaming).toBeFalsy();
    expect(s.busy).toBe(false);
    expect(s.mood).toBe("happy");
  });

  it("records tool events in order between the text pieces", () => {
    let s = chatReducer(initialChat, { kind: "user", text: "add maths" });
    s = server(s, { type: "delta", text: "[proud] On it. " });
    s = server(s, { type: "event", name: "plan_add", ok: true, summary: "plan_add: Maths (2026-10-24 09:00)" });
    s = server(s, { type: "final", text: "[proud] On it. Done." });
    // the final text replaces the streaming reply in place, so the order stays user, cyph, event
    expect(s.messages.map((m) => m.role)).toEqual(["user", "cyph", "event"]);
    expect(s.messages[1].text).toBe("[proud] On it. Done.");
  });

  it("an error ends the turn and is shown", () => {
    let s = chatReducer(initialChat, { kind: "user", text: "hi" });
    s = server(s, { type: "error", message: "Cyph could not answer just now." });
    expect(s.busy).toBe(false);
    expect(s.messages.at(-1)).toMatchObject({ role: "system", text: "Cyph could not answer just now." });
  });

  it("tracks the connection", () => {
    let s = server(initialChat, { type: "ready", device: "PC" });
    expect(s.connected).toBe(true);
    s = server(s, { type: "disconnected", reason: "x" });
    expect(s.connected).toBe(false);
    s = server(s, { type: "unpaired" });
    expect(s.messages.at(-1)?.role).toBe("system");
  });

  it("busy from the server is explained, takes back the dropped message and does not stick", () => {
    const s = server(chatReducer(initialChat, { kind: "user", text: "hi" }), { type: "busy" });
    expect(s.messages.map((m) => m.role)).toEqual(["system"]);
    expect(s.busy).toBe(false);
  });

  it("a dropped connection closes a half-streamed reply", () => {
    let s = chatReducer(initialChat, { kind: "user", text: "hi" });
    s = server(s, { type: "delta", text: "[happy] he" });
    s = server(s, { type: "disconnected", reason: "x" });
    expect(s.messages.some((m) => m.streaming)).toBe(false);
    expect(s.busy).toBe(false);
  });

  it("keeps the pictures a device sent on its message", () => {
    const s = chatReducer(initialChat, { kind: "user", text: "look", pics: ["data:image/jpeg;base64,AA"] });
    expect(s.messages[0].pics).toEqual(["data:image/jpeg;base64,AA"]);
  });
});
