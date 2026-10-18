# Cyph — the Furry Engineer: architecture plan (draft)

Cyph is a terminal personal assistant (study partner, co-engineer) with a
physical body: the VectorM 6-DOF arm. This is a plan, not built yet.

Decisions so far: Python everywhere; Gemini is the main brain; ER2
(Gemini Robotics) is the embodied worker; project lives in `cyph/` beside
`vectorm-arm/` (the arm stays an independent, testable sub-project).

## Layers (each buildable and testable alone)

1. **Body** — `vectorm-arm/` firmware + host client (done: flashing, pin
   map, expressions prototype) plus a **body daemon** (next):
   - sole owner of the serial port (COM15 is single-open)
   - 50 Hz loop with three additive layers: alive (breathing, flicks,
     glances, never stops) < mood (happy, sad, curious, ...) < task
     (purposeful moves from Gemini/ER2)
   - small local API: `express`, `move_joints`, `look_at`, `grip`, `stop`,
     `set_idle`, `play_sound`; all non-blocking, `stop` always works
   - hard safety: joint limits, speed caps, torque off on fault, watchdog
2. **Reflexes** — local, no LLM: idle life, expressions, pet reaction,
   startle, sounds. Always running; Gemini only overrides when it acts.
3. **Cyph core (terminal harness)**:
   - agent loop in the terminal; screen reading, computer/file tools,
     permission prompts for anything risky
   - memory directory of `.md` files: user profile, skills, project notes
   - **context manager**: harness decides what enters each turn (token
     budget, summaries, retrieval from memory); store context compactly
   - model router (Gemini main; ER2 for physical work)
4. **Embodied brain** — Gemini Robotics ER2 Live session, opened only for
   physical tasks (pick, sort, point). Calls body tools asynchronously and
   hands control back when finished. API details come from the docs the
   user supplies; nothing is assumed until then.

## Open items

- ER2 docs / link (user to supply) before any integration code
- Pet sensing: capacitive touch pad on a spare pin vs camera vs voice
- Camera: OpenMV on the arm (bridge exists in `vectorm-arm/host/`) vs PC
- PA8 servo mystery (moved something during probing; currently undriven)
- Optional: tiny idle loop in firmware so a PC crash does not freeze Cyph

## Status

- DONE: body daemon (`cyph/body.py`), fursona-themed Textual terminal
  (`python -m cyph`), 12 simulator tests. Not yet run on the real arm.
- DONE (offline-tested only): Gemini Live voice (`voice.py`, `tools.py`,
  `audio.py`): NON_BLOCKING body tools, resumption + compression,
  local speak/listen reflexes, 28 fake-session tests. NOT yet verified
  against the live API: run `python -m cyph voice-test`.

## Suggested build order

1. Body daemon + layered engine + idle (port `expressions.py` into it)
2. Local API + safety layer; test by hand with hotkeys
3. Sound (PC audio synced to moods, gripper as mouth) and more moods
4. Core harness skeleton: loop, memory dir, context manager, Gemini tools
5. ER2 integration (needs docs) and camera
6. Pet sensor and failsafe idle in firmware

---

## Findings from the Google docs (read 2026-10-17)

Sources: aistudio.google.com/docs/robotics-overview, /robotics-streaming,
/robotics-orchestration, /live-api/tools.

- ER 2 models: `gemini-robotics-er-2-preview` (standard, no Live API) and
  `gemini-robotics-er-2-streaming-preview` (Live API, low latency). Points
  come back as normalized `[y, x]` on a 0-1000 grid, e.g.
  `[{"point": [376, 508], "label": "banana"}]`.
- **ER 2 streaming: output is TEXT only, and only BLOCKING function calls
  are supported.** Audio in is 16 kHz PCM, video in is JPEG at <= 1 fps,
  and a new prompt while it is generating acts as a barge-in.
- Non-blocking ("background async") calls exist only on the regular Live
  models: `gemini-3.8-live` (sync + async) and
  `gemini-3.8-live-extended-thinking` (async only). Declare
  `"behavior": "NON_BLOCKING"`; reply with scheduling `INTERRUPT`,
  `WHEN_IDLE` or `SILENT` via `session.send_tool_response`. Not
  supported on `gemini-3.1-flash-live-preview`.

### Consequences for the design

- **Two Gemini roles, not one.** `gemini-3.8-live` is Cyph's talking voice
  (audio in/out, NON_BLOCKING body tools). ER 2 is a spatial worker the
  core calls for "where is the cup" / "point at X" / pick-and-place
  planning. It cannot be the conversational voice.
- **Talk-while-moving is done locally, not by the model.** The body daemon
  watches the audio itself: speaking -> gripper bobs with the output
  amplitude and the mood layer plays; listening -> small gripper-pivot
  tilt; wake word -> perk-up. Zero model latency and it works even when
  tools are blocking.
- **Pointing:** ER 2 returns an image point; a calibration maps image
  coordinates (webcam/OpenMV) to arm joint angles; the body daemon does
  the motion as a non-blocking task so Gemini keeps talking.
- Wake word "Hey Cyph" is detected locally (no stream open until then);
  the interactive terminal popup is a hotkey-summoned window.
- Webcam frames go to ER 2 at <= 1 fps; tell people nearby (docs ask
  operators to notify those who may be recorded).
- Always keep a step limit and real success/failure reporting on every
  body tool, plus a stop that always works.
