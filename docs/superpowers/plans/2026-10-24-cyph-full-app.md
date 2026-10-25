# Cyph full app: every approved mockup, working and synced (plan 3)

Source of truth for the UI: `docs/mockups/` (approved). The bottom-right corner is Cyph's on every page.

## The sync rule (this is what "everything works with each other" means)

**The server owns all state. The app owns none.** A page never keeps its own copy as truth: it reads from a shared
client store, the store is filled from the REST API, and the server tells every connected device when something changed.

```
 Telegram bot ─┐                         ┌─ Desktop app (Home, Calendar, Memory, Settings, Widget)
 PC TUI (sync) ─┼─► files on the server ─► Watcher ─► /events WebSocket ─┼─ Phone app
 App REST/chat ─┘   schedule.json         (polls 1s)   {"changed": ...}  └─ Arm agent (PC)
                    day_notes.json, memory/*.md, changes.log,
                    proactive.json, devices.db, recent_chat.json
```

* Because the Watcher watches the files, EVERY writer is covered (Telegram, the planner tools, the curator, the PC
  sync, a REST edit) without instrumenting each one.
* `/events` sends `{"type":"changed","what":"plan|day_notes|memory|memory_log|settings|devices|chat"}`. A client
  refetches that one resource. Edits are never pushed as diffs, so there is nothing to get out of step.
* Chat turns are also relayed to every connected device (`user` / `delta` / `final`), so a message typed in Telegram
  or on the phone appears live on the desktop, and the whole transcript survives reconnects (`GET /chat/history`).
* Optimistic UI is allowed, but the server's answer always replaces it.

## Pages and what each one is wired to

| Page | Reads | Writes | Live updates from |
|---|---|---|---|
| Home | `/plan` today, `/chat/history`, `/chat` WS | chat, plan status, plan add (slips + quick buttons) | plan, chat |
| Calendar | `/plan?start&days`, `/day/{d}` | `POST/PATCH/DELETE /plan`, `PUT /day`, quick-add via chat tool | plan, day_notes |
| Memory | `/memory`, `/memory/{k}`, `/memory/log` | `POST /memory/{k}`, `POST /memory/log/{n}/undo`, proposals | memory, memory_log |
| Settings | `/devices`, `/settings`, `/me` | `/settings`, `/devices/pair-code`, revoke | devices, settings |
| Widget | same store as Home (second Tauri window) | chat, plan status | everything |
| Study | `/study/*` (library, notes) | quiz, explain via chat brain | study |
| Architect | `/projects/*` | annotate via chat brain | projects |
| Arm | `/arm` state from the PC agent | `/arm/command` relayed to the agent | arm |
| Phone | same pages, responsive layout | same | same |

## Phases (each ends with tests green and a real run)

**Phase 1: sync backbone + server endpoints** (this plan's first part)
1. `core/watch.py`: Watcher + `Hub` (broadcast to sockets). Tests with a temp dir.
2. `/events` WebSocket (same auth as `/chat`), relay of chat turns through the Hub.
3. `GET /chat/history` (from SharedChat, with `src` = tg/app).
4. `GET/PUT /settings` (proactive config: enabled, morning, evening, heads-up, quiet hours, max unanswered).
5. `GET /memory/log`, `POST /memory/log/{i}/undo`; `POST /devices/pair-code`; revoke-other-device (policy below).
6. Deploy to the server (ask first).

**Phase 2: app foundation.** Router + Corner shell; one `Store` (plan, memory, settings, devices, chat) fed by REST and
`/events`; Rust `events.rs` (pinned WS, reconnect, emits `sync`); the live face engine as a React component; buddy.

**Phase 3: Home + Calendar + Memory + Settings** pages from the mockups, fully wired (table above).

**Phase 4: Widget window** (frameless, transparent, always on top, bottom-right, S/M/L, mute, hover peek, proactive
bubble from the server's `proactive` events).

**Phase 5: Voice** (hold-to-talk, Cartesia on the server, plays on every device).

**Phase 6: Study + Architect** (library, retrieval over his notes, quiz/flashcards stored on the server; project tree
read from a path the PC agent exposes).

**Phase 7: Arm** (the PC app is the arm agent: Rust serial port to the VectorM, state and commands relayed through the
server so the phone can drive it too; E-STOP always local first).

**Phase 8: Phone** (Android build; blocked on Developer Mode).

## Policy decisions the build needs from the user (defaults chosen, flagged in the final report)

* **Pair codes and revoking other devices from the app.** The deployed API deliberately lets a device revoke only
  itself, so a stolen phone cannot lock the owner out. The Settings mockup revokes others and makes pair codes.
  Default: allowed, but every pairing/revocation is logged and sent to the owner on Telegram, and a new device starts
  with `kind=app`. Ask before deploying this.
* **Prompt injection from a stolen device** (it can plant text in memory through chat): unchanged; memory edits are
  already in `changes.log` with undo, and the Memory page shows the source device.

## Rules that stay

Ask before any install/download/deploy; tell the user before anything touches the live server; secrets only on the
clipboard and in env vars; message text is never logged; commits only when asked (no git repo exists yet).

## Status (end of session 1 of plan 3)

Done and tested (82 Python core tests, 28 vitest, 17 cargo tests, tsc, vite build):
* Server: change feed (`/events`, Watcher + Hub), `/chat/history`, `/settings`, `/memory/log` + undo, `/memory/proposals`
  (+ `memory_propose` tool), `/memory/overview`, `/devices/pair-code`, revoke-other-device with Telegram notice.
  NOT DEPLOYED: the live server still runs plan 1. Deploying restarts the bot: needs the owner's yes.
* App: Rust `/events` supervisor, shared resource store, sync client, chat relay between devices, live face engine,
  Corner shell + buddy, pages Home, Calendar, Memory, Settings (devices, schedule, look) all wired to real endpoints.
  `?mock` runs the same UI against an in-memory server. `tools_dev_core.py` runs the real server with a fake brain.
* Verified against the real TLS server: REST edits and direct file writes (as Telegram would) reach `/events`.

Not built yet (each needs a decision or an approval, see the end-of-session message):
Study + Architect, Arm (needs a PC arm agent), Widget window, Phone layout, Voice, QR code for pairing, deploy.
