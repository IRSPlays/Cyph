# Cyph app: development notes

Layout: `app/` is a Tauri 2 project (Rust layer in `src-tauri/`, React + TypeScript UI in `src/`). The Rust layer owns
all network I/O and the device token; the UI only calls the commands in `src/lib/ipc.ts`.

## Commands
- `pnpm dev` (in `app/`): the UI alone in a browser. Add `?mock` to the URL for an in-memory fake server (no Tauri needed).
  `?mock=unpaired` shows the pair screen.
- `pnpm tauri dev`: the real app on this PC.
- `pnpm test`, `pnpm exec tsc --noEmit`: frontend tests and types. `cargo test` in `app/src-tauri`: Rust tests (they start
  local TLS servers; no real server needed). `python -m pytest tests/test_bake_face.py`: the face bake.
- Live pinned-TLS check against a real server (opt in): set `CYPH_LIVE_HOST` and `CYPH_LIVE_FP`, then
  `cargo test --test live_health -- --ignored` in `app/src-tauri`.
- `python tools_bake_face.py`: regenerate `app/src/face/frames.json` after changing `cyph/face.py` or `cyph/theme.py`.
- `pnpm tauri android build --debug --apk --target aarch64`: the phone build (needs the Android SDK/NDK, see plan 2 task 1,
  and Windows Developer Mode for the symlink step).

## Pairing
`python deploy/deploy_server.py pair` prints a one-time pair link (valid 10 minutes). Paste it into the app.
The link carries the server address, the certificate fingerprint to pin, and the code. Treat it as a secret.

## Spike result
- Desktop (2026-10-24): pinned TLS verified against the live server through an SSH tunnel. The right fingerprint gets
  `/health` OK; one changed character is refused at the handshake. Local TLS tests pass too.
- Android (2026-10-24): `tauri android init` works and the Rust crate, including `ring`, cross-compiles for
  `aarch64-linux-android`. The APK build then stopped on a Windows symlink permission (Developer Mode off). NOT yet
  tested: APK on a phone, home Wi-Fi, Playit on mobile data. Task 6 of plan 2 is still open.

## Known limits (v1)
- The device token is stored unencrypted in the per-user app data folder. Follow-up: OS keystore.
- Face: per-mood frames, blink and talking mouth only. No gaze, ear twitching or wind yet.
- A message dequeued at the moment the connection drops can be lost; the UI shows the disconnect.
- Arm panel, voice, Study and Architect modes are placeholders.
- Desktop layout only; the phone layout is plan 4.
