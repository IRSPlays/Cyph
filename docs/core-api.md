# Cyph Core API (v1)

HTTPS, self-signed certificate. Clients must pin the certificate's SHA-256 fingerprint (printed with the pair code)
and must do all network I/O outside the webview (Tauri/Rust), because a webview cannot pin. No CORS, no docs page.

Auth: `Authorization: Bearer <device_id>.<secret>`. 401 = not paired or revoked. 429 = too many bad tokens, retry
later (`Retry-After` seconds). A valid token always works, even during a lockout: only bad tokens are locked out.
Request bodies over 64 KiB get 413 (checked before auth).

| Method + path | Body | Result |
|---|---|---|
| GET `/health` | none (no auth) | `{ok, time}` |
| POST `/pair` | `{code, name}` (no auth) | `{device_id, token}`; 403 bad code; 429 too many tries |
| GET `/me` | | `{device, now}` |
| GET `/devices` / DELETE `/devices/{id}` | | `{devices:[{id,name,kind,created,last_seen,revoked}]}` / `{ok}`; a device can only revoke itself (403 for any other id: revoke others with the server CLI) |
| GET `/plan?start=YYYY-MM-DD&days=N` | | `{start, days, events:[{id,title,date,time,start,duration_min,kind,status,notes,project,repeat}]}` |
| POST `/plan` | `{title, date, time?, duration_min?, kind?, notes?, project?, repeat?, days?, until?}` | `{event, conflicts:[{id,title,time}]}`; 422 on bad input (all dates, here and in `/plan` and `/day`, must be in the years 2000-2100) |
| PATCH `/plan/{id}` | any of the same fields | `{event, conflicts}`; 404; 422 |
| POST `/plan/{id}/status` | `{status: planned\|done\|skipped}` | `{event, conflicts}` (not for recurring events) |
| DELETE `/plan/{id}` | | `{ok}`; 404 |
| GET / PUT `/day/{date}` | `{text}` | `{date, events, note}` |
| GET `/memory` | | `{files:[{key,file,heading,used,cap}]}` |
| GET `/memory/{key}` | | `{key, entries, used, cap}` (`key` = user, memory, people) |
| POST `/memory/{key}` | `{action: add\|replace\|remove, text?, match?}` | `{applied, rejected:[{why}]}` (secret filter, duplicates and size caps apply; logged as source `app`) |

## Study

All need the token. Errors are `{detail}` with 404 (missing), 409 (wrong state), 413 (upload over 36 MB), 422 (bad input).
Uploads are base64 in JSON.

| Method + path | Notes |
|---|---|
| GET `/study/now?device=phone\|desk` | what to do now: state (empty, ready, open, done, idle), headline, steps, weak topics, test, debrief, inbox, homework |
| POST `/study/session` | `{device, kind?, topic?}` starts the session Cyph picked (kinds: mini, topic, more, comeback, prep); GET `/study/session/{id}` |
| POST `/study/session/{id}/answer` | `{grade}` card, `{option, value?}` quick, `{answer, images?}` full, `{had}` plan, `{ticks}` paragraph; returns per-point marks |
| POST `/study/session/{id}/next`, `/end`, `/hint` | move on, stop (counts if he did something), next hint, always in order (up to 4; the full solution only after an answer was submitted, else 409) |
| POST `/study/attempts/{id}/dispute` | "I think I'm right": one re-mark per attempt, never lowers. A tapped answer is not given free: a second solve checks the answer key |
| POST `/study/questions/{id}/flag`, `/study/cards/{id}/flag` | `{reason: not_in_notes\|wrong\|confusing}` bins it and records a rule |
| POST `/study/debrief` | `{text, finish?}` the after-school debrief |
| GET / POST `/study/inbox`, POST `/study/sources/{id}/move` | auto-kept uploads (DELETE `/study/sources/{id}` undoes), wrong subject |
| GET `/study/library`, GET `/study/topics/{id}` | subjects and topics with strength; one topic's detail |
| POST `/study/homework`, POST `/study/homework/{id}` | add; `{done}` |
| POST `/study/syllabus` | preload topics for a subject (shown as "not started") |
| GET `/study/today` | what to do now: due count, drafts, tests, weak topics, streak |
| GET `/study/subjects` | the seven subjects with counts |
| POST `/study/sources` | `{subject, title, text?, data?, mime?}` imports and processes; GET lists; POST `/study/sources/{id}/retry`; GET `/study/sources/{id}/original` |
| GET / POST `/study/notes`, GET / PATCH / DELETE `/study/notes/{id}` | notes, edited by hand or from a source |
| POST `/study/search`, POST `/study/reindex` | semantic search over notes |
| GET `/study/topics`, PATCH / POST merge | rename or merge topics |
| GET / POST `/study/cards`, PATCH / DELETE `/study/cards/{id}` | `state`: draft, approved, suspended |
| POST `/study/cards/draft`, POST `/study/cards/{id}/approve`, POST `/study/cards/{id}/review` | draft with the model; `{grade: 0\|1\|2}` |
| GET `/study/due`, GET `/study/stats` | queue and numbers |
| POST `/study/tutor` | `{subject, question, level?}` hint ladder |
| POST `/study/practice`, POST `/study/practice/answer` | generate and mark |
| POST `/study/papers/mark`, GET `/study/attempts` | mark a whole paper from a photo or PDF |
| GET / POST `/study/tests`, DELETE `/study/tests/{id}` | tests with revision blocks in the plan |
| `/study/notebooks`, `/study/pages`, `/study/boards`, `/study/boards/{id}/items` | pen notebooks (PDF export at `/pdf`) and boards |

Chat: the `say` frame accepts `mode: "study"` and `subject`. A change publishes `changed: study` on `/events`.

### Web app

`GET /` serves `app/dist` and `GET /cert.pem` serves the public certificate, both without a token. Every data route still needs one.

## WebSocket `/chat`

1. Connect, then within 5 seconds send `{"type":"auth","token":"..."}`. Server replies `{"type":"ready","device":name}`
   or closes the socket. Close codes:
   - `4401`: the token is not valid (never paired, or revoked). The ONLY code that means "re-pair".
   - `4429`: locked out after too many bad tokens. Keep the token and retry later.
   - `1013`: the server is busy (too many sockets waiting to authenticate). Retry later.
   - `1008`: no auth frame in time, or something else was sent first. Reconnect.
2. Send `{"type":"say","text":"..."}` (max 4000 chars). Messages are answered one at a time, in order. The token is
   checked again on every `say`: a device revoked meanwhile is closed with `4401` and not answered. Only one chat turn
   runs on the server at a time; a `say` sent while another turn (from any device) is running is answered with
   `{"type":"busy"}` and not run. A turn is stopped after 90 seconds.
3. For each `say` the server sends `{"type":"delta","text"}` pieces and `{"type":"event","name","ok","summary"}` for
   each tool call as it finishes, then `{"type":"final","text"}` (or `{"type":"error","message"}`).
   `final.text` starts with one mood tag, e.g. `[happy]`; the moods are the keys of `cyph/theme.py` `MOOD_COLOR`.
4. `{"type":"ping"}` is answered with `{"type":"pong"}`. A frame that is not JSON (or is binary) is answered with
   `{"type":"error","message":"bad frame"}` and the socket stays open. Frames over 64 KiB close the socket.

Pairing: run `python deploy/deploy_server.py pair` on the PC. The code is valid for 10 minutes and works once.
