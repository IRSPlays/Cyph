# Cyph Companion: Core + Planner + App (spec 1 of 5)

Date: 2026-10-23. Owner: Haziq. Status: draft for review.

## 1. Goal

Replace the terminal app with a native companion that works the same on desktop, laptop and phone, backed by the
24/7 server (`hazserver`). Cyph lives in the bottom-right corner, plans your days with you, remembers you, and is
reachable everywhere. The robot arm is optional.

**In this spec:** Cyph Core (server), the Planner, the Companion app (desktop/laptop/phone), cloud voice, memory
editor, proactivity, and the arm as an optional connected agent.
**Not in this spec (own specs, built on this foundation):** Study mode (note embeddings, agentic retrieval, camera),
Architecture mode (project files loaded by tool), OpenClaw/Hermes research and borrowed features, desktop window
reading, Gemini Robotics-ER arm control. The app ships the mode switches and the arm panel for these from day one.

## 2. Decisions already made

| Topic | Decision |
|---|---|
| App stack | Tauri 2 + React/TypeScript, one UI on Windows, laptop and Android. A phone web app (PWA) is not a viable fallback over Playit because it needs a certificate the browser trusts. If Tauri Android fails milestone 0, the fallback is a minimal native Android shell around the same React UI. |
| Server | Existing Python on `hazserver`; add FastAPI (REST + WebSocket). SQLite for the planner. Memory stays as `.md` files. |
| Remote access | Playit tunnel. Server treated as internet-facing: TLS with a pinned cert, per-device revocable tokens, rate limit and lockout. |
| Offline | Server-first. Each device caches the day and memory read-only; edits made offline queue and replay. |
| Voice | Hold-to-talk or hotkey on every device; cloud STT/TTS through the server, so Cyph sounds the same everywhere. Cartesia for both (already integrated), Gemini TTS as fallback. No wake word in this spec. |
| Proactivity | Schedule-driven only. Mute/idle state with a hotkey. |
| Look | "The Bench": olive jacket panel, neon circuit traces, LEDs, label-tape day ruler; Cyph's replies in italic serif on a "tape"; terminal-style speech bubble in the corner; the minimized widget is the Strip, expanding to the Peek card on hover, with a separate visualizer window. Mockups in `.superpowers/brainstorm/`. |
| Brain | Gemini 3.8 Flash for chat and tools; lighter model for curation, summaries and parsing, as today. |

## 3. Architecture

```
Desktop/Laptop/Phone (Tauri)  ──TLS pinned, device token──▶ Playit ──▶ CYPH CORE (hazserver)
Telegram bot (owner-only)     ───────── same API ─────────────────────▶  FastAPI
PC arm agent (Python, COM15)  ──outbound WebSocket, agent token──────▶  • /chat (WebSocket, streamed)
                                                                        • /plan  • /memory  • /voice
                                                                        • scheduler  • brain + tools
```

- The existing reverse SSH tunnel is retired: the PC arm agent connects outward to the same API as a device of kind
  `agent`, which works through Playit like any client.
- Telegram becomes one more client of the API. Its current server-mode code is reused, not rewritten.
- The legacy TUI stays runnable during the transition and is removed only after the new app replaces it.

## 4. Core server

**Auth.** `devices(id, name, kind[app|agent|telegram], token_hash, created, last_seen, revoked)`. First pairing uses a
one-time code shown in the terminal on the server and entered once in the new device. Tokens are 256-bit random,
stored hashed. 5 failed attempts from an address locks it for 15 minutes; every attempt is logged without payloads.
The app lists devices and revokes any of them.

**Brain tools** (one list, used by every client):
`plan.list/add/move/remove/complete`, `day.note`, `memory.search/read/write`, `visual.show(html)`, `mute.set`,
`arm.*` (only registered while an arm agent is connected).

**Context discipline.** The always-on prompt is capped: persona, USER/MEMORY/people digests (existing caps), today's
plan, tomorrow's headline. Everything else (older notes, projects, later study material) enters only through tool
calls. A per-request token budget is enforced and logged.

**Memory.** Existing `MemoryStore`, curator, digest and `recall()` stay. Two changes: (1) the curator and nightly
review also read Cyph's own lines, but only to record commitments Cyph made ("I'll remind you Friday") and corrections,
never as facts about Haziq; (2) edits from the app go through the same secret filter and `changes.log`, marked
`source: app`, with size caps shown in the editor instead of silently refusing.

## 5. Planner

Tables:
- `events(id, title, start, end, kind[fixed|block|task|deadline], status[planned|done|skipped|moved], project, notes,
  rrule, created_by, updated_at)`
- `day_notes(date, text)`: free-form notes per date ("what I want to do that day", your own details).
- `reminders(id, event_id, fire_at, sent_to, state)`.

Rules: all times stored UTC with the user's zone (Asia/Singapore) on display; any event can be created for any future
date by voice or text ("plan Friday afternoon: parade prep, then maths"); moving an event keeps a history row;
conflicts are reported by the tool result so Cyph can resolve them out loud, never silently overwritten.

**Proactivity.** The scheduler runs on the server and only does four things: the morning "what do you want to do
today?" (once per day, at your configured wake time or the first activity, whichever is first); a heads-up before a
block starts; a check-in when a block ended without being marked done; an evening "plan tomorrow?" nudge. Delivery
goes to the most recently active device, else Telegram. States: `active`, `idle` (no speech, passive only), `muted
until <time>`; a global hotkey (desktop) and a button (phone) toggles them. No other unprompted speech.

## 6. Companion app

Shared React UI, platform shell differs.
- **Home (Bench layout, chat-first):** mode switches (General/Study/Architect), status LEDs (server, arm, voice),
  stage window with Cyph and his current mood, mood keypad, arm panel with joint gauges, the conversation tape, day
  ruler, command line with mic.
- **Moods and face:** the brain keeps emitting `[mood]` tags; the client maps them to sprite expressions. The pixel
  face renderer (`face.py`, 26 moods) is ported to TypeScript canvas; this is a real piece of work, not a re-skin.
- **Desktop corner widget:** separate transparent, always-on-top, click-through-except-Cyph window bottom-right.
  Strip by default (block, time range, time left, whole-day bar with NOW, next block, LEDs); hover opens the Peek;
  replies show as the terminal-style bubble; `visual.show` opens a separate visualizer window (sandboxed iframe, no
  network, content supplied by Cyph). Hotkey summons the full Home.
- **Phone:** Chat, Plan, Study, Memory tabs; hold-to-talk mic; day picker; add-by-voice.
- **Memory editor:** list, open, edit, save with version check against the server; shows caps and the change log.
- **Cache and queue:** day, week, memory and recent chat cached locally; offline edits queue, replay in order, and
  surface conflicts to the user instead of overwriting.

## 7. Voice path

Hold key/button → client streams Opus to `/voice` → Cartesia STT → text enters `/chat` as a normal turn → reply
streams as text; sentences go to TTS as they complete and audio chunks play while the rest generates. Interrupting
(press again) cancels playback and generation. Failure of STT or TTS degrades to text with a visible LED; it never
blocks the chat.

## 8. Arm

The PC runs the existing arm code as a headless agent. When connected it reports `body.status()` (mood, joints,
calm/passive) which the app shows in the arm panel, and registers `arm.*` tools. When the PC is off, the panel shows
"arm offline" and nothing else changes. Passive idle motion stays on the PC agent. Gemini Robotics-ER control is a
later spec.

## 9. Errors, security, testing

- Server unreachable: app shows cached data and a clear LED state; chat input stays but is marked "queued".
- Gemini failure or budget cap: existing budget code applies; reply says so plainly.
- Never logged: message text, tokens, memory contents. Telegram stays owner-only.
- Server tests (pytest): auth, lockout, token revoke, planner CRUD and conflicts, scheduler with a fake clock, tool
  list per connected agent, prompt budget, sync replay.
- Client tests (vitest): cache and queue replay, mood mapping, strip countdown math.
- Manual per release: pair a new device, revoke it, offline edit and replay, Playit round trip on mobile data.

## 10. Milestones (risk first)

0. **Spike:** Tauri Android hello-world calling the server over Playit with the pinned certificate. Decides the stack.
1. Core: FastAPI, auth and pairing, planner tables and tools, `/chat` WebSocket with the brain.
2. Desktop Home (Bench) with chat, plan and mood; face renderer port.
3. Corner widget: Strip, Peek, bubble, visualizer window, hotkey, mute/idle.
4. Phone app: same UI, tabs, queue and cache.
5. Voice path on all devices.
6. Scheduler and proactivity delivery.
7. Memory editor, curator/digest changes.
8. PC arm agent and arm panel; retire the reverse tunnel.

## 10b. Changes found while planning against the real code (these override the text above)

- **Planner storage:** stays on the existing `schedule.json` (extended with status, notes, project; a free-form note
  per day in `day_notes.json`), not new SQLite tables. Telegram, the PC sync and the prompt snapshot already read that
  file. SQLite is used only for devices and pair codes.
- **One process:** the API runs as a thread inside `python -m cyph.server`, sharing the planner, memory and brain with
  Telegram. No second service.
- **Brain tools in spec 1:** plan tools and `day_note` only. Memory is already written by the curator. `mute.set`,
  `visual.show` and `arm.*` arrive with the widget and arm plans.
- **Networking:** a webview cannot pin a self-signed certificate, so all app network I/O runs in the Tauri (Rust)
  layer. The server sends no CORS headers.
- **Lockout keys:** behind the Playit tunnel every client shares one address, so lockouts are global (`pair`, `auth`),
  not per address.
- **Face:** the spec's "port the face renderer to TypeScript" became "bake it": `tools_bake_face.py` renders every
  mood from the existing Python renderer into `frames.json`, and the app only draws frames (v1: per-mood frame, blink,
  talking mouth; no gaze, ear twitching or wind yet).
- **Crypto and pinning:** a custom rustls verifier that pins one certificate fingerprint, with the `ring` provider (not
  `aws-lc-rs`); the fingerprint travels in the pair link. v1 stores the device token in the app data folder (OS keystore
  is a follow-up).
- **Offline cache and send queue:** moved from the desktop Home to the phone plan; plan 2 shows an offline LED and an
  error line only.
- **Plan documents:** plan 1 (core server) is `docs/superpowers/plans/2026-10-23-cyph-core-server.md` (done and
  deployed). Plan 2 (Android spike + desktop Home) is `docs/superpowers/plans/2026-10-24-cyph-app-spike-and-home.md`.
  Later plans: 3 corner widget, 4 phone, 5 voice, 6 scheduler/proactivity, 7 memory editor and curator changes, 8 PC
  arm agent.

## 11. Request coverage (honest)

| You asked for | This spec |
|---|---|
| Native desktop, not a TUI | Yes |
| Same features on PC, laptop, phone | Yes (one UI codebase) |
| Data synced via the 24/7 server | Yes |
| Pixel Cyph in the corner, typographic bubbles, popups | Yes |
| Typing and talking, text in the main home | Yes |
| Calendar/planning, days ahead, morning discussion | Yes |
| Edit memory and files from the app | Memory files yes; arbitrary server files no (deliberate, safer) |
| Telegram continues, with upgrades | Yes |
| Arm optional | Yes |
| Proactive but not too much; mute and hotkey | Yes |
| Memory also learns from what Cyph said | Yes, commitments only |
| Study mode, RAG, camera, visualizer by another model | Spec 3 (visualizer window host is here) |
| Architecture mode with project files | Spec 4 |
| OpenClaw/Hermes features | Spec 2 research, then borrowed pieces |
| Read desktop windows | Spec 5 |
| Gemini Robotics-ER arm control | Spec 5 |
| Wake word on every device | Not planned (hold/hotkey instead) |
| Live voice like Gemini Live | No (too expensive, as you decided) |
