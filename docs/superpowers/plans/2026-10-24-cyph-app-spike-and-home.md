# Cyph App: Android Spike + Desktop Home Implementation Plan (plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that a Tauri app on Android can talk to the Cyph Core server through a Playit tunnel with a pinned self-signed certificate (the spike, which decides the stack), then build the desktop "Bench" Home: pairing, streamed chat with moods, today's plan, all through the same Rust networking layer.

**Architecture:** One Tauri 2 app (`app/`): a Rust layer owns ALL network I/O (pinned-certificate REST and WebSocket, token storage) and exposes a few commands plus one `chat` event stream; a React + TypeScript UI renders the Bench look and never touches the network. Cyph's pixel face is *baked* by a Python script from the existing renderer (`cyph/face.py`) into `frames.json`, so the UI only draws pre-made frames and does not re-implement the rendering maths.

**Tech Stack:** Tauri 2, Rust (reqwest 0.13 + rustls 0.23 with the `ring` provider, tokio-tungstenite, sha2), React + TypeScript + Vite, pnpm, vitest; Python for the face bake. Server side is plan 1 (`docs/core-api.md`).

**Standing rules for whoever executes this (from Haziq):**
- No git repo exists here: never run git commands, never commit. Skip "Checkpoint" steps' commit idea; they only mark a good stopping point.
- **Ask Haziq before ANY download** (see the downloads table below). One question per row, in chat, before running the command. Never download without a yes.
- Never change Windows system settings (for example Developer Mode); tell Haziq what is needed and let him do it.
- Keys, tokens and pair codes are secrets: never print or log them. Pair links contain a one-time code: only show them to Haziq, never store them in files.
- Prefer Read/Edit/Write tools over shell for file work. Run Python tests as `python -m pytest <file> -q` from the project root; run Rust tests with `cargo test` in `app/src-tauri`; run frontend tests with `pnpm test` in `app`.
- The server (`hazserver`) is live: do not restart it or change its config without asking first. The plan says exactly where that is needed (Task 6 only).

**Known unknowns (written against current docs, not compiled yet):** exact `create-tauri-app` flags, reqwest 0.13's `tls_backend_preconfigured` name, whether `ring` cross-compiles for Android without extra setup, and Windows symlink rights for `tauri android init`. Tests are written so a failure points at the cause; when a compile error shows up, fix it against the crate docs and note the change in the task report. The spike (Part A) exists precisely to flush these out before any UI work.

---

## Downloads (ask Haziq first, one yes per row)

| # | What | Approx. size | Needed by | Command / source |
|---|---|---|---|---|
| D1 | Rust toolchain (rustup, stable, MSVC target) | 0.5-1 GB | Task 1 | https://rustup.rs (`rustup-init.exe`) |
| D2 | Android Studio (gives the JDK and SDK Manager) | ~1.2 GB | Task 1 | https://developer.android.com/studio |
| D3 | Android SDK Platform, Platform-Tools, Build-Tools, Command-line Tools, NDK (side by side) via the SDK Manager | 4-7 GB | Task 1 | SDK Manager in Android Studio |
| D4 | Rust Android targets | ~200 MB | Task 1 | `rustup target add aarch64-linux-android armv7-linux-androideabi i686-linux-android x86_64-linux-android` |
| D5 | npm packages for the app (react, vite, tauri cli/api, vitest, fontsource fonts) | ~300 MB | Task 2 | `pnpm install` |
| D6 | Cargo crates (tauri, reqwest, rustls, tokio...) | ~1 GB build cache | Task 2 onward | `cargo build` / `cargo test` |

Disk check at planning time: 39 GB free on C:. Present on this PC already: Node 24, pnpm 10, Java 23, Visual Studio Build Tools 2022, WebView2.

## Decisions made while planning (keep them)

1. **Pinning:** a custom rustls `ServerCertVerifier` that accepts exactly one end-entity certificate (SHA-256 fingerprint), ignoring hostnames. The fingerprint is printed by `python -m cyph.core pair` and travels in the pair link.
2. **Crypto provider:** `ring`, not `aws-lc-rs` (which needs CMake/NASM on Windows and is harder to cross-compile). Task 3 includes a check that `aws-lc-sys` is not in the build.
3. **Face:** baked, not ported (see the architecture line). v1 face = per-mood frames, blink, talking mouth. Gaze, ear twitching and wind are NOT in v1 (they need the animation maths); they are a later polish task.
4. **Token storage:** v1 stores the device token in `settings.json` in the app data directory (per-user folder, not encrypted). Moving it to the OS keystore is a recorded follow-up, not part of this plan.
5. **Webview is offline-only:** strict CSP (no remote content). Fonts are bundled with `@fontsource/*`.
6. **Arm panel, voice, Study/Architect modes** appear in the UI as disabled placeholders ("arm offline", "voice soon"); they are wired in later plans.

## File structure

Create (paths relative to the project root `cyph/`):
- `app/` (Tauri project, scaffolded in Task 2)
  - `app/src-tauri/Cargo.toml`, `build.rs`, `tauri.conf.json`, `capabilities/default.json` (scaffolded, then edited)
  - `app/src-tauri/src/lib.rs`: app setup, command registration, `init_crypto()`
  - `app/src-tauri/src/pin.rs`: pinned verifier + `client_config(fp)`
  - `app/src-tauri/src/net.rs`: `Server`, `ApiError`, `request()`
  - `app/src-tauri/src/link.rs`: pair-link parser
  - `app/src-tauri/src/settings.rs`: persisted settings
  - `app/src-tauri/src/chat.rs`: WebSocket `session()` and `supervise()`
  - `app/src-tauri/src/commands.rs`: Tauri commands + `AppState`
  - `app/src-tauri/tests/common/mod.rs`, `pinned_http.rs`, `chat_ws.rs`: local TLS test servers
  - `app/src/lib/types.ts`, `ipc.ts`, `mockIpc.ts`, `chat.ts`, `chatStore.ts`, `day.ts`, `plan.ts`
  - `app/src/face/frames.json` (generated), `compose.ts`, `Face.tsx`
  - `app/src/ui/App.tsx`, `Pair.tsx`, `Home.tsx`, `TopBar.tsx`, `Stage.tsx`, `Tape.tsx`, `DayRuler.tsx`, `Spike.tsx`
  - `app/src/styles/bench.css`
  - tests: `app/src/lib/*.test.ts`, `app/src/face/compose.test.ts`, `app/src/ui/render.test.tsx`
- `tools_bake_face.py`, `tests/test_bake_face.py`
- `docs/app-dev.md`

Modify:
- `deploy/deploy_server.py`: `core --lan` option (Task 6).

---

# Part A: the spike (decides the stack)

### Task 1: Build environment

**Files:** none in the repo (machine setup). Haziq performs the system-level steps; the agent verifies.

- [ ] **Step 1: Ask Haziq about D1-D4 and explain what they are**

Ask one question per row D1, D2, D3, D4 (sizes above). If he says no to any, stop and report: the spike cannot run without them. If he already has any installed, skip that row.

- [ ] **Step 2: Rust (D1)**

Haziq runs the installer from https://rustup.rs (accept the default stable MSVC toolchain). Then, in a NEW terminal:

Run: `rustc --version; cargo --version`
Expected: both print a version (stable, 1.8x or newer).

- [ ] **Step 3: Android Studio and SDK pieces (D2, D3)**

Haziq installs Android Studio, then in SDK Manager (tick "Show Package Details" only if needed) installs: Android SDK Platform (latest), Android SDK Platform-Tools, NDK (Side by side), Android SDK Build-Tools, Android SDK Command-line Tools.

- [ ] **Step 4: Environment variables (Haziq or the agent with his yes; these are per-user variables, not system settings)**

In PowerShell:

```powershell
[System.Environment]::SetEnvironmentVariable("JAVA_HOME", "C:\Program Files\Android\Android Studio\jbr", "User")
[System.Environment]::SetEnvironmentVariable("ANDROID_HOME", "$env:LocalAppData\Android\Sdk", "User")
$VERSION = Get-ChildItem -Name "$env:LocalAppData\Android\Sdk\ndk" | Select-Object -Last 1
[System.Environment]::SetEnvironmentVariable("NDK_HOME", "$env:LocalAppData\Android\Sdk\ndk\$VERSION", "User")
```

(Tauri's docs use Android Studio's bundled JBR for `JAVA_HOME`; the Java 23 already on this PC may be too new for Gradle, so do not rely on it.) Open a NEW terminal afterwards.

- [ ] **Step 5: Android Rust targets (D4)**

Run: `rustup target add aarch64-linux-android armv7-linux-androideabi i686-linux-android x86_64-linux-android`
Expected: each target reported as installed.

- [ ] **Step 6: Verify everything in one go**

Run (new terminal): `rustc --version; cargo --version; node -v; pnpm -v; echo $env:JAVA_HOME; echo $env:ANDROID_HOME; echo $env:NDK_HOME; & "$env:ANDROID_HOME\platform-tools\adb.exe" version`
Expected: versions print; all three variables are non-empty; `adb` prints "Android Debug Bridge version ...".

- [ ] **Step 7: Note for Haziq (he does this, the agent must not):** `tauri android init` creates symbolic links; on Windows that needs Developer Mode (Settings > System > For developers) or an elevated shell. If Task 6 fails on a symlink error, ask him to enable it.

- [ ] **Step 8: Checkpoint**

---

### Task 2: Scaffold the Tauri app and prove the toolchain

**Files:**
- Create: `app/` (scaffold)
- Modify: `app/src-tauri/Cargo.toml`, `app/src-tauri/tauri.conf.json`

- [ ] **Step 1: Ask Haziq about D5 and D6** (npm packages, then Cargo crates). 

- [ ] **Step 2: Scaffold**

From the project root `cyph/`:

Run: `pnpm create tauri-app@latest app --template react-ts --manager pnpm --identifier com.cyph.app --yes`
Expected: an `app/` folder with `src/`, `src-tauri/`, `package.json`. If a flag is rejected, run `pnpm create tauri-app@latest` interactively with: project name `app`, identifier `com.cyph.app`, TypeScript/React, pnpm. The template's Rust library crate must be named `cyph_app_lib`; if the scaffold chose another name (look at `[lib] name` in `app/src-tauri/Cargo.toml`), set it to `cyph_app_lib` and update `src-tauri/src/main.rs` to call `cyph_app_lib::run()`.

- [ ] **Step 2b: Product name**

In `app/src-tauri/tauri.conf.json` set `"productName": "Cyph"` and the window `"title": "Cyph"`, `"width": 1180`, `"height": 760`. Set the CSP (webview is offline-only):

```json
"security": {
  "csp": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src ipc: http://ipc.localhost"
}
```

- [ ] **Step 3: Install frontend packages**

Run (in `app/`): `pnpm install`
Then: `pnpm add -D vitest` and `pnpm add @fontsource/pixelify-sans @fontsource/instrument-serif @fontsource/dm-mono`
Expected: installs complete; `package.json` lists them. Add to `package.json` scripts: `"test": "vitest run"`.

- [ ] **Step 4: Prove the desktop toolchain builds and runs**

Run (in `app/`): `pnpm tauri dev`
Expected: the first build takes several minutes (Cargo compiles Tauri); then a window titled "Cyph" opens showing the template page. Close the window (stops the dev server). If it fails, the error is almost certainly a missing build tool (Task 1) or WebView2; fix and retry.

- [ ] **Step 5: Edit `app/src-tauri/Cargo.toml`: dependencies**

Replace the `[dependencies]` and add `[dev-dependencies]` (keep the template's `[package]`, `[lib]` and `[build-dependencies]` sections as generated):

```toml
[dependencies]
tauri = { version = "2", features = [] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["rt-multi-thread", "macros", "sync", "time"] }
reqwest = { version = "0.13", default-features = false, features = ["rustls-no-provider", "json"] }
rustls = { version = "0.23", default-features = false, features = ["ring", "std", "tls12"] }
tokio-tungstenite = { version = "0.28", default-features = false, features = ["connect", "handshake", "rustls-tls-webpki-roots"] }
futures-util = "0.3"
sha2 = "0.10"
url = "2"

[dev-dependencies]
rcgen = "0.13"
tokio-rustls = { version = "0.26", default-features = false, features = ["ring", "tls12"] }
```

(If `tauri-plugin-opener` or other template plugins are in the scaffold, leave them; they are unused.)

- [ ] **Step 6: Verify the crate graph is what we expect**

Run (in `app/src-tauri/`): `cargo tree -i rustls --depth 1`
Expected: a single `rustls v0.23.x` (one version only, otherwise `Connector::Rustls`/`tls_backend_preconfigured` would reject our `ClientConfig` as a different type; if two versions appear, align the versions in Cargo.toml until `cargo tree -d | findstr rustls` shows none).

Run: `cargo tree -i aws-lc-sys`
Expected: `error: package ID specification 'aws-lc-sys' did not match any packages` (we must NOT be building aws-lc). If it IS present, find who enables it with `cargo tree -i aws-lc-sys -e features` and remove that default feature.

- [ ] **Step 7: Checkpoint**

---

### Task 3: The pinned certificate verifier

**Files:**
- Create: `app/src-tauri/src/pin.rs`
- Modify: `app/src-tauri/src/lib.rs` (module declaration)

- [ ] **Step 1: Write the failing unit tests inside `pin.rs`** (the integration test with a real TLS server comes in Task 4)

Create `app/src-tauri/src/pin.rs` containing only the tests module first:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const HEX: &str = "e561069748c67beb0e05a9a0e8e5bd6a8f3a3f3d2f55c0c5b7a1b2c3d4e5f607";

    #[test]
    fn accepts_plain_hex() {
        assert_eq!(normalize_fp(HEX).unwrap().len(), 32);
    }

    #[test]
    fn accepts_colons_spaces_and_uppercase() {
        let colon: String = HEX.to_uppercase().as_bytes().chunks(2)
            .map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
        assert_eq!(normalize_fp(&colon).unwrap(), normalize_fp(HEX).unwrap());
        assert_eq!(normalize_fp(&format!("  {HEX}\n")).unwrap(), normalize_fp(HEX).unwrap());
    }

    #[test]
    fn rejects_bad_fingerprints() {
        let too_long = format!("{HEX}00");
        let not_hex = HEX.replace('e', "z");
        for bad in ["", "abc", &HEX[..62], too_long.as_str(), not_hex.as_str()] {
            assert!(normalize_fp(bad).is_err(), "{bad:?} should be rejected");
        }
    }

    #[test]
    fn client_config_builds_for_a_good_fingerprint() {
        crate::init_crypto();
        assert!(client_config(HEX).is_ok());
        assert!(client_config("nope").is_err());
    }
}
```

- [ ] **Step 2: Declare the module and `init_crypto` so the tests compile and fail properly**

In `app/src-tauri/src/lib.rs` replace the file with:

```rust
pub mod pin;

/// Installs the `ring` crypto provider as the process default. Safe to call more than once.
pub fn init_crypto() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    init_crypto();
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

Run (in `app/src-tauri/`): `cargo test --lib pin::`
Expected: FAIL to compile: `cannot find function normalize_fp in this scope` (the implementation does not exist yet).

- [ ] **Step 3: Write the implementation** (put this ABOVE the tests module in `pin.rs`)

```rust
//! Certificate pinning. The server's certificate is self-signed, so there is no certificate authority to trust:
//! we accept exactly one end-entity certificate, identified by the SHA-256 of its DER encoding, and ignore the
//! host name (the address is a tunnel address that never appears in the certificate).

use std::sync::Arc;

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring, verify_tls12_signature, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{CertificateError, ClientConfig, DigitallySignedStruct, Error, SignatureScheme};
use sha2::{Digest, Sha256};

/// "e5:61:..." / "E561..." / " e561...\n" -> 32 bytes.
pub fn normalize_fp(text: &str) -> Result<[u8; 32], String> {
    let hex: String = text.chars().filter(|c| !c.is_whitespace() && *c != ':').collect::<String>().to_lowercase();
    if hex.len() != 64 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("the certificate fingerprint must be 64 hex characters".into());
    }
    let mut out = [0u8; 32];
    for (i, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

#[derive(Debug)]
struct Pinned {
    fingerprint: [u8; 32],
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for Pinned {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, Error> {
        let got = Sha256::digest(end_entity.as_ref());
        if same(&got, &self.fingerprint) {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(Error::InvalidCertificate(CertificateError::ApplicationVerificationFailure))
        }
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        verify_tls12_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        verify_tls13_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

/// A rustls client configuration that trusts only the certificate with this fingerprint.
pub fn client_config(fp_hex: &str) -> Result<ClientConfig, String> {
    let fingerprint = normalize_fp(fp_hex)?;
    let provider = Arc::new(ring::default_provider());
    let verifier = Arc::new(Pinned { fingerprint, provider: provider.clone() });
    Ok(ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|e| e.to_string())?
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth())
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test --lib pin::`
Expected: 4 passed. (If `signature_verification_algorithms` or `supported_schemes` is not found, check the rustls 0.23 docs: the field lives on `CryptoProvider`, and `supported_schemes()` on `WebPkiSupportedAlgorithms`; fix the call and record it.)

- [ ] **Step 5: Checkpoint**

---

### Task 4: Pinned REST client, proven against a real local TLS server

**Files:**
- Create: `app/src-tauri/src/net.rs`, `app/src-tauri/tests/common/mod.rs`, `app/src-tauri/tests/pinned_http.rs`
- Modify: `app/src-tauri/src/lib.rs`

- [ ] **Step 1: Write the test helper** `app/src-tauri/tests/common/mod.rs`

```rust
//! A throwaway HTTPS server with a freshly generated self-signed certificate, for tests.
#![allow(dead_code)]

use std::sync::Arc;

use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

pub struct TestServer {
    pub port: u16,
    pub fp: String,
}

pub fn make_cert() -> (CertificateDer<'static>, PrivatePkcs8KeyDer<'static>) {
    let ck = rcgen::generate_simple_self_signed(vec!["127.0.0.1".to_string()]).unwrap();
    (ck.cert.der().clone(), PrivatePkcs8KeyDer::from(ck.key_pair.serialize_der()))
}

pub fn fingerprint(cert: &CertificateDer<'_>) -> String {
    Sha256::digest(cert.as_ref()).iter().map(|b| format!("{b:02x}")).collect()
}

pub fn acceptor(cert: CertificateDer<'static>, key: PrivatePkcs8KeyDer<'static>) -> TlsAcceptor {
    cyph_app_lib::init_crypto();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let cfg = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert], PrivateKeyDer::Pkcs8(key))
        .unwrap();
    TlsAcceptor::from(Arc::new(cfg))
}

/// Answers every HTTP request with `status` and the JSON `body`, until the test process ends.
pub async fn https_json(status: u16, body: &'static str) -> TestServer {
    let (cert, key) = make_cert();
    let fp = fingerprint(&cert);
    let acceptor = acceptor(cert, key);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = listener.accept().await else { return };
            let acceptor = acceptor.clone();
            tokio::spawn(async move {
                let Ok(mut tls) = acceptor.accept(tcp).await else { return };
                let mut buf = [0u8; 4096];
                let mut seen = Vec::new();
                while !seen.windows(4).any(|w| w == b"\r\n\r\n") {
                    match tls.read(&mut buf).await {
                        Ok(0) | Err(_) => return,
                        Ok(n) => seen.extend_from_slice(&buf[..n]),
                    }
                }
                let reply = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = tls.write_all(reply.as_bytes()).await;
                let _ = tls.shutdown().await;
            });
        }
    });
    TestServer { port, fp }
}
```

- [ ] **Step 2: Write the failing tests** `app/src-tauri/tests/pinned_http.rs`

```rust
mod common;

use cyph_app_lib::net::{request, Server};
use serde_json::json;

fn server_for(s: &common::TestServer, fp: &str) -> Server {
    cyph_app_lib::init_crypto();
    Server { host: format!("127.0.0.1:{}", s.port), fp: fp.to_string() }
}

#[tokio::test]
async fn pinned_client_talks_to_the_pinned_server() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let v = request(&server_for(&s, &s.fp), "GET", "/health", None, None).await.unwrap();
    assert_eq!(v["ok"], true);
}

#[tokio::test]
async fn fingerprint_formatting_does_not_matter() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let spaced: String = s.fp.to_uppercase().as_bytes().chunks(2)
        .map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
    assert!(request(&server_for(&s, &spaced), "GET", "/health", None, None).await.is_ok());
}

#[tokio::test]
async fn a_different_certificate_is_refused() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let other = common::https_json(200, r#"{"ok":true}"#).await;   // a second server has a different cert
    let err = request(&server_for(&s, &other.fp), "GET", "/health", None, None).await.unwrap_err();
    assert_eq!(err.status, 0, "a refused certificate is a network error, not an HTTP status");
}

#[tokio::test]
async fn http_errors_carry_status_and_detail() {
    let s = common::https_json(401, r#"{"detail":"not paired"}"#).await;
    let err = request(&server_for(&s, &s.fp), "GET", "/me", Some("tok"), None).await.unwrap_err();
    assert_eq!((err.status, err.detail.as_str()), (401, "not paired"));
}

#[tokio::test]
async fn bodies_are_sent_as_json() {
    let s = common::https_json(200, r#"{"device_id":"d1","token":"t"}"#).await;
    let v = request(&server_for(&s, &s.fp), "POST", "/pair", None, Some(json!({"code": "X", "name": "n"}))).await.unwrap();
    assert_eq!(v["device_id"], "d1");
}
```

- [ ] **Step 3: Run them to verify they fail**

Run (in `app/src-tauri/`): `cargo test --test pinned_http`
Expected: FAIL to compile (`unresolved import cyph_app_lib::net`).

- [ ] **Step 4: Write `net.rs`**

```rust
//! All HTTPS to the Cyph server goes through here: a client that trusts only the pinned certificate.

use std::time::Duration;

use serde::Serialize;
use serde_json::Value;

/// `host` is "name-or-ip:port" exactly as in the pair link.
#[derive(Debug, Clone)]
pub struct Server {
    pub host: String,
    pub fp: String,
}

#[derive(Debug, Serialize)]
pub struct ApiError {
    /// HTTP status, or 0 for a network/TLS/encoding failure.
    pub status: u16,
    pub detail: String,
}

impl ApiError {
    pub fn net(e: impl std::fmt::Display) -> Self {
        ApiError { status: 0, detail: e.to_string() }
    }
}

pub fn client(fp: &str) -> Result<reqwest::Client, String> {
    let cfg = crate::pin::client_config(fp)?;
    reqwest::Client::builder()
        .tls_backend_preconfigured(cfg)
        .https_only(true)
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())
}

pub async fn request(
    server: &Server,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> Result<Value, ApiError> {
    let http = client(&server.fp).map_err(ApiError::net)?;
    let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(ApiError::net)?;
    let mut req = http.request(method, format!("https://{}{}", server.host, path));
    if let Some(token) = token {
        req = req.bearer_auth(token);
    }
    if let Some(body) = body {
        req = req.json(&body);
    }
    let resp = req.send().await.map_err(ApiError::net)?;
    let status = resp.status();
    let text = resp.text().await.map_err(ApiError::net)?;
    let json: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    if status.is_success() {
        return Ok(json);
    }
    let detail = json.get("detail").and_then(|d| d.as_str()).map(String::from).unwrap_or_else(|| status.to_string());
    Err(ApiError { status: status.as_u16(), detail })
}
```

In `lib.rs` add `pub mod net;` under `pub mod pin;`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cargo test --test pinned_http`
Expected: 5 passed. This proves, with a real TLS handshake, that the pinned verifier accepts the right certificate in any fingerprint format and refuses another. If `tls_backend_preconfigured` does not exist in the resolved reqwest version, use `use_preconfigured_tls` (the older name) and record it.

- [ ] **Step 6: Checkpoint**

---

### Task 5: Desktop proof against the live server

**Files:**
- Create: `app/src/ui/Spike.tsx`
- Modify: `app/src-tauri/src/lib.rs`, `app/src/App.tsx` (scaffold file), `app/src-tauri/src/commands.rs` (new, minimal for now)

- [ ] **Step 1: Add the `health` command (full `commands.rs` comes in Task 8)**

Create `app/src-tauri/src/commands.rs`:

```rust
use serde_json::Value;

use crate::net::{self, ApiError, Server};

/// Spike: GET /health on a server with a pinned fingerprint. No token needed.
#[tauri::command]
pub async fn health(host: String, fp: String) -> Result<Value, ApiError> {
    net::request(&Server { host, fp }, "GET", "/health", None, None).await
}
```

In `lib.rs` add `mod commands;` and change `run()` to register it:

```rust
        .invoke_handler(tauri::generate_handler![commands::health])
```

(placed before `.run(...)`).

- [ ] **Step 2: Add the hidden spike screen** `app/src/ui/Spike.tsx`

```tsx
import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export function Spike() {
  const [host, setHost] = useState("");
  const [fp, setFp] = useState("");
  const [out, setOut] = useState("");
  const test = async () => {
    setOut("...");
    try {
      setOut("OK " + JSON.stringify(await invoke("health", { host, fp })));
    } catch (e) {
      setOut("FAILED " + JSON.stringify(e));
    }
  };
  return (
    <div style={{ padding: 16, fontFamily: "monospace", color: "#e6efd8", background: "#10150e", minHeight: "100vh" }}>
      <h3>Spike: pinned health check</h3>
      <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="host:port" style={{ width: "100%" }} />
      <input value={fp} onChange={(e) => setFp(e.target.value)} placeholder="certificate sha256" style={{ width: "100%", marginTop: 8 }} />
      <button onClick={test} style={{ marginTop: 8, padding: 12 }}>Test</button>
      <pre style={{ whiteSpace: "pre-wrap" }}>{out}</pre>
    </div>
  );
}
```

Replace the scaffold's `app/src/App.tsx` with:

```tsx
import { Spike } from "./ui/Spike";

export default function App() {
  return <Spike />;
}
```

(The real App comes in Task 12; for now the spike screen is the whole app.)

- [ ] **Step 3: Get a certificate fingerprint and open the SSH tunnel (the server stays untouched)**

Open the tunnel, leave it running:
`ssh -i "$env:USERPROFILE\.ssh\cyph_server" -o BatchMode=yes -N -L 18443:127.0.0.1:8443 hazserver@192.168.0.149`

The server prints the fingerprint with a pair code (the code expires in 10 minutes and is not used here; ignore it): `python deploy/deploy_server.py pair`. Copy only the `Cert SHA-256` line.

- [ ] **Step 4: Run the desktop app and test**

Run (in `app/`): `pnpm tauri dev`. In the window enter host `127.0.0.1:18443` and the fingerprint, press Test.
Expected: `OK {"ok":true,"time":"..."}`.
Then change one character of the fingerprint and press Test again.
Expected: `FAILED {"status":0,"detail":"..."}` (the handshake is refused). Record both results.

- [ ] **Step 5: Close the tunnel** (stop the ssh process) and close the app.

- [ ] **Step 6: Checkpoint.** Desktop pinned TLS is proven end to end against the real server.

---

### Task 6: THE GATE: Android on a real phone

**Files:**
- Modify: `deploy/deploy_server.py` (`core --lan`)
- Create (generated): `app/src-tauri/gen/android/`

This task decides the stack. Do not start Part B until it passes or Haziq chooses the fallback.

- [ ] **Step 1: Initialise and build the Android project**

Ask Haziq to enable Developer Mode first if `init` complains about symlinks (see Task 1 step 7). Then, in `app/`:

Run: `pnpm tauri android init`
Expected: creates `src-tauri/gen/android/`. 

Run: `pnpm tauri android build --debug --apk --target aarch64`
Expected: after a long first build, an APK path is printed (under `src-tauri/gen/android/app/build/outputs/apk/`). Record any failure verbatim. The most likely failure is the `ring` crate not finding the NDK compiler; if so, check `NDK_HOME` points at a real NDK folder and retry; if `ring` still fails, escalate (the documented fallback is switching the provider to `aws-lc-rs`, which needs more build tooling) instead of improvising.

- [ ] **Step 2: Put the APK on the phone**

Ask Haziq to enable Developer options and USB debugging on the phone and connect it, or to copy the APK to the phone and install it (allowing installs from that source is his phone setting). With USB debugging:

Run: `& "$env:ANDROID_HOME\platform-tools\adb.exe" devices`
Expected: the phone listed as `device`. Then `adb install -r <path to apk>`.

- [ ] **Step 3: First test on the home Wi-Fi (stepping stone, needs a server change: ASK HAZIQ FIRST)**

The server's API listens on `127.0.0.1` only. For a phone on the LAN it must listen on all interfaces. Add a `--lan` option in `deploy/deploy_server.py`: change `core()` to

```python
def core():
    """core [--lan] <host:port>: switch the apps' API on. --lan listens on all interfaces (home Wi-Fi testing);
    without it only localhost, which is what a tunnel agent on the same machine needs."""
    args = [a for a in sys.argv[2:] if a != "--lan"]
    addr = args[0] if args else ""
    host = "0.0.0.0" if "--lan" in sys.argv[2:] else "127.0.0.1"
    body = json.dumps({"enabled": True, "host": host, "port": 8443, "public_address": addr}).encode()
    ssh(f"mkdir -p {REMOTE}/app/data && cat > {REMOTE}/app/data/core.json", body)
    print(f"apps' API enabled on the server ({host}:8443). Run `service` to apply.")
```

and update the usage line in the module docstring. Tell Haziq this requires `python deploy/deploy_server.py core --lan 192.168.0.149:8443` then `service` (which RESTARTS the live Telegram bot for a few seconds) and get his explicit yes before running either. After the test, revert with `core ""` and `service` (again with his yes).

On the phone (home Wi-Fi), open the app, enter host `192.168.0.149:8443` and the fingerprint, press Test.
Expected: `OK {"ok":true,...}`. With one changed fingerprint character: `FAILED`.

- [ ] **Step 4: The real test: mobile data through Playit (Haziq does the Playit part)**

Haziq creates a Playit TCP tunnel to `127.0.0.1:8443` on the server (his Playit account) and gives the agent the public `host:port`. Re-run `python deploy/deploy_server.py core <host:port>` (localhost-only again) and `service` (restart: ask first). On the phone, turn Wi-Fi OFF (mobile data), enter the Playit `host:port` and the fingerprint, press Test.
Expected: `OK {"ok":true,...}`, and with a wrong fingerprint `FAILED`.

- [ ] **Step 5: Decide, using this table**

| Result | Decision |
|---|---|
| Steps 3 and 4 pass on the phone | Stack confirmed. Continue to Part B. |
| Build fails only on `ring` for Android | Report. Try `aws-lc-rs` (needs CMake + NASM for the Windows host build too); ask Haziq before downloading those. |
| Build works, phone OK on Wi-Fi, FAILS through Playit | The tunnel or its plan is the problem, not the app. Report exact error; try Tailscale as the transport (spec fallback) after asking Haziq. |
| Tauri Android cannot be made to build or run | Fallback from the spec: a minimal native Android shell around the same React UI. Stop and ask Haziq before continuing. |

- [ ] **Step 6: Checkpoint.** Write the outcome (pass / which row) at the top of `docs/app-dev.md`'s "Spike result" section in Task 13.

---

# Part B: the desktop Home

### Task 7: Pair-link parsing and persisted settings

**Files:**
- Create: `app/src-tauri/src/link.rs`, `app/src-tauri/src/settings.rs`
- Modify: `app/src-tauri/src/lib.rs`

- [ ] **Step 1: Write the failing tests inside `link.rs`**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const FP: &str = "e561069748c67beb0e05a9a0e8e5bd6a8f3a3f3d2f55c0c5b7a1b2c3d4e5f607";

    #[test]
    fn parses_a_good_link() {
        let l = parse(&format!("cyph://pair?host=abc.playit.gg:12345&fp={FP}&code=ABCD-EFGH")).unwrap();
        assert_eq!((l.host.as_str(), l.fp.as_str(), l.code.as_str()), ("abc.playit.gg:12345", FP, "ABCD-EFGH"));
    }

    #[test]
    fn rejects_bad_links() {
        for bad in [
            "".to_string(),
            "https://pair?host=a:1&fp=x&code=C".to_string(),
            format!("cyph://other?host=a:1&fp={FP}&code=C"),
            format!("cyph://pair?fp={FP}&code=C"),
            format!("cyph://pair?host=a:1&code=C"),
            format!("cyph://pair?host=a:1&fp={FP}"),
            format!("cyph://pair?host=a/b:1&fp={FP}&code=C"),
            "cyph://pair?host=a:1&fp=short&code=C".to_string(),
        ] {
            assert!(parse(&bad).is_err(), "{bad:?} should be rejected");
        }
    }

    #[test]
    fn surrounding_whitespace_is_fine() {
        assert!(parse(&format!("  cyph://pair?host=a:1&fp={FP}&code=C\n")).is_ok());
    }
}
```

And in `settings.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_defaults() {
        let dir = std::env::temp_dir().join(format!("cyph-settings-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        assert!(!Settings::load(&path).paired(), "a missing file means not paired");
        let s = Settings { host: "h:1".into(), fp: "f".into(), token: Some("t".into()), device_name: "PC".into(), device_id: "d".into() };
        s.save(&path).unwrap();
        let back = Settings::load(&path);
        assert!(back.paired());
        assert_eq!((back.host.as_str(), back.device_name.as_str()), ("h:1", "PC"));
        std::fs::write(&path, "{broken").unwrap();
        assert!(!Settings::load(&path).paired(), "a corrupt file means not paired, never a crash");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_token_never_leaves_in_the_public_view() {
        let s = Settings { host: "h:1".into(), fp: "f".into(), token: Some("secret".into()), device_name: "PC".into(), device_id: "d".into() };
        let json = serde_json::to_string(&s.public()).unwrap();
        assert!(!json.contains("secret"));
        assert!(json.contains("\"paired\":true"));
    }
}
```

- [ ] **Step 2: Declare modules and run to see them fail**

In `lib.rs` add `pub mod link;` and `pub mod settings;`.
Run: `cargo test --lib link:: settings::` (run each: `cargo test --lib link::` then `cargo test --lib settings::`)
Expected: FAIL to compile (`cannot find function parse` / `Settings`).

- [ ] **Step 3: Implement `link.rs`** (above its tests)

```rust
//! The pair link printed by `python -m cyph.core pair`:  cyph://pair?host=<host:port>&fp=<sha256>&code=<XXXX-XXXX>

use url::Url;

#[derive(Debug, PartialEq)]
pub struct PairLink {
    pub host: String,
    pub fp: String,
    pub code: String,
}

pub fn parse(link: &str) -> Result<PairLink, String> {
    let bad = || "that is not a Cyph pair link".to_string();
    let url = Url::parse(link.trim()).map_err(|_| bad())?;
    if url.scheme() != "cyph" || url.host_str() != Some("pair") {
        return Err(bad());
    }
    let get = |key: &str| url.query_pairs().find(|(k, _)| k == key).map(|(_, v)| v.into_owned());
    let (host, fp, code) = (get("host").ok_or_else(bad)?, get("fp").ok_or_else(bad)?, get("code").ok_or_else(bad)?);
    if host.is_empty() || host.contains('/') || host.contains(' ') || code.is_empty() {
        return Err(bad());
    }
    crate::pin::normalize_fp(&fp)?;
    Ok(PairLink { host, fp, code })
}
```

- [ ] **Step 4: Implement `settings.rs`** (above its tests)

```rust
//! What the app remembers between runs. v1 keeps the device token in this file in the per-user app data folder
//! (not encrypted); moving it to the OS keystore is a recorded follow-up.

use std::path::Path;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    pub host: String,
    pub fp: String,
    pub token: Option<String>,
    pub device_name: String,
    #[serde(default)]
    pub device_id: String,
}

/// What the UI may see: never the token or the fingerprint.
#[derive(Debug, Serialize)]
pub struct Public {
    pub paired: bool,
    pub host: String,
    pub device_name: String,
}

impl Settings {
    pub fn load(path: &Path) -> Settings {
        std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(self).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, path).map_err(|e| e.to_string())
    }

    pub fn paired(&self) -> bool {
        self.token.is_some() && !self.host.is_empty() && !self.fp.is_empty()
    }

    pub fn public(&self) -> Public {
        Public { paired: self.paired(), host: self.host.clone(), device_name: self.device_name.clone() }
    }
}
```

- [ ] **Step 5: Run the tests**

Run: `cargo test --lib`
Expected: all unit tests pass (pin 4, link 3, settings 2).

- [ ] **Step 6: Checkpoint**

---

### Task 8: Commands and the chat WebSocket session

**Files:**
- Create: `app/src-tauri/src/chat.rs`, `app/src-tauri/tests/chat_ws.rs`
- Modify: `app/src-tauri/src/commands.rs`, `app/src-tauri/src/lib.rs`

- [ ] **Step 1: Write the failing integration test** `app/src-tauri/tests/chat_ws.rs`

```rust
mod common;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use cyph_app_lib::chat::{session, Ended};
use cyph_app_lib::net::Server;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;

/// A TLS WebSocket server. `reject` = close with 4401 right after the auth frame.
async fn ws_server(reject: bool) -> common::TestServer {
    let (cert, key) = common::make_cert();
    let fp = common::fingerprint(&cert);
    let acceptor = common::acceptor(cert, key);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = listener.accept().await else { return };
            let acceptor = acceptor.clone();
            tokio::spawn(async move {
                let Ok(tls) = acceptor.accept(tcp).await else { return };
                let Ok(mut ws) = tokio_tungstenite::accept_async(tls).await else { return };
                let auth = ws.next().await.unwrap().unwrap().into_text().unwrap();
                let auth: Value = serde_json::from_str(auth.as_str()).unwrap();
                if reject || auth["token"] != "good" {
                    let _ = ws.send(Message::Close(Some(CloseFrame { code: CloseCode::Library(4401), reason: "".into() }))).await;
                    return;
                }
                ws.send(Message::text(json!({"type": "ready", "device": "t"}).to_string())).await.unwrap();
                while let Some(Ok(Message::Text(t))) = ws.next().await {
                    let v: Value = serde_json::from_str(t.as_str()).unwrap();
                    if v["type"] == "say" {
                        ws.send(Message::text(json!({"type": "delta", "text": "[happy] he"}).to_string())).await.unwrap();
                        ws.send(Message::text(json!({"type": "final", "text": format!("[happy] you said {}", v["text"].as_str().unwrap())}).to_string())).await.unwrap();
                    }
                }
            });
        }
    });
    common::TestServer { port, fp }
}

fn collector() -> (Arc<Mutex<Vec<Value>>>, impl Fn(Value) + Send + Sync) {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let sink = seen.clone();
    (seen, move |v| sink.lock().unwrap().push(v))
}

#[tokio::test]
async fn a_chat_turn_streams_back() {
    let s = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: s.fp.clone() };
    let (seen, emit) = collector();
    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (stop_tx, mut stop_rx) = watch::channel(false);
    let handle = tokio::spawn(async move { session(&server, "good", &mut out_rx, &mut stop_rx, &emit).await });
    out_tx.send("hello".into()).unwrap();
    for _ in 0..100 {
        if seen.lock().unwrap().iter().any(|v| v["type"] == "final") {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    stop_tx.send(true).unwrap();
    assert!(matches!(handle.await.unwrap(), Ended::Stopped));
    let types: Vec<String> = seen.lock().unwrap().iter().map(|v| v["type"].as_str().unwrap().to_string()).collect();
    assert_eq!(types, ["ready", "delta", "final"]);
    assert_eq!(seen.lock().unwrap()[2]["text"], "[happy] you said hello");
}

#[tokio::test]
async fn code_4401_means_unpaired() {
    let s = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: s.fp.clone() };
    let (_seen, emit) = collector();
    let (_out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (_stop_tx, mut stop_rx) = watch::channel(false);
    let ended = session(&server, "revoked", &mut out_rx, &mut stop_rx, &emit).await;
    assert!(matches!(ended, Ended::Unpaired));
}

#[tokio::test]
async fn a_wrong_pin_never_connects() {
    let s = ws_server(false).await;
    let other = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: other.fp.clone() };
    let (_seen, emit) = collector();
    let (_out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (_stop_tx, mut stop_rx) = watch::channel(false);
    let ended = session(&server, "good", &mut out_rx, &mut stop_rx, &emit).await;
    assert!(matches!(ended, Ended::Lost(_)));
}
```

(These tests call `common::acceptor`, which already calls `init_crypto`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --test chat_ws`
Expected: FAIL to compile (`unresolved import cyph_app_lib::chat`).

- [ ] **Step 3: Write `chat.rs`**

```rust
//! The WebSocket to /chat: one connection at a time, reconnecting with a growing pause, speaking the protocol in
//! docs/core-api.md. Messages from the server are handed to `emit` as JSON; the UI never sees the socket.

use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async_tls_with_config, Connector};

use crate::net::Server;

pub enum Ended {
    /// The server said our token is no longer valid (close code 4401).
    Unpaired,
    /// The connection dropped or never opened; worth retrying.
    Lost(String),
    /// We were asked to stop.
    Stopped,
}

type Emit<'a> = &'a (dyn Fn(Value) + Send + Sync);

pub async fn session(
    server: &Server,
    token: &str,
    out: &mut mpsc::UnboundedReceiver<String>,
    stop: &mut watch::Receiver<bool>,
    emit: Emit<'_>,
) -> Ended {
    let cfg = match crate::pin::client_config(&server.fp) {
        Ok(c) => c,
        Err(e) => return Ended::Lost(e),
    };
    let url = format!("wss://{}/chat", server.host);
    let connecting = connect_async_tls_with_config(url, None, false, Some(Connector::Rustls(Arc::new(cfg))));
    let (ws, _) = match tokio::time::timeout(Duration::from_secs(15), connecting).await {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => return Ended::Lost(e.to_string()),
        Err(_) => return Ended::Lost("connecting timed out".into()),
    };
    let (mut tx, mut rx) = ws.split();
    if tx.send(Message::text(json!({"type": "auth", "token": token}).to_string())).await.is_err() {
        return Ended::Lost("could not send the login".into());
    }
    loop {
        tokio::select! {
            _ = stop.changed() => {
                let _ = tx.close().await;
                return Ended::Stopped;
            }
            say = out.recv() => match say {
                Some(text) => {
                    let msg = json!({"type": "say", "text": text}).to_string();
                    if tx.send(Message::text(msg)).await.is_err() {
                        return Ended::Lost("sending failed".into());
                    }
                }
                None => return Ended::Stopped,
            },
            msg = rx.next() => match msg {
                Some(Ok(Message::Text(t))) => {
                    if let Ok(v) = serde_json::from_str::<Value>(t.as_str()) {
                        emit(v);
                    }
                }
                Some(Ok(Message::Close(frame))) => {
                    let code = frame.map(|f| u16::from(f.code)).unwrap_or(1005);
                    return if code == 4401 { Ended::Unpaired } else { Ended::Lost(format!("closed by the server ({code})")) };
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Ended::Lost(e.to_string()),
                None => return Ended::Lost("the connection closed".into()),
            }
        }
    }
}

/// Keeps a session alive until stopped or unpaired. Tells the UI about drops with {"type":"disconnected"}.
pub async fn supervise(
    server: Server,
    token: String,
    mut out: mpsc::UnboundedReceiver<String>,
    mut stop: watch::Receiver<bool>,
    emit: impl Fn(Value) + Send + Sync + 'static,
) {
    let mut pause = 1u64;
    loop {
        let started = Instant::now();
        match session(&server, &token, &mut out, &mut stop, &emit).await {
            Ended::Stopped => return,
            Ended::Unpaired => {
                emit(json!({"type": "unpaired"}));
                return;
            }
            Ended::Lost(reason) => {
                emit(json!({"type": "disconnected", "reason": reason}));
                if started.elapsed() > Duration::from_secs(10) {
                    pause = 1;
                }
                tokio::select! {
                    _ = stop.changed() => return,
                    _ = tokio::time::sleep(Duration::from_secs(pause)) => {}
                }
                pause = (pause * 2).min(30);
            }
        }
    }
}
```

In `lib.rs` add `pub mod chat;`.

- [ ] **Step 4: Run the chat tests**

Run: `cargo test --test chat_ws`
Expected: 3 passed.

- [ ] **Step 5: Replace `commands.rs` with the full command set**

```rust
use std::path::PathBuf;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};
use tokio::sync::{mpsc, watch, Mutex};

use crate::chat;
use crate::link;
use crate::net::{self, ApiError, Server};
use crate::settings::{Public, Settings};

pub struct ChatHandle {
    tx: mpsc::UnboundedSender<String>,
    stop: watch::Sender<bool>,
}

pub struct AppState {
    pub settings: Mutex<Settings>,
    pub path: PathBuf,
    pub chat: Mutex<Option<ChatHandle>>,
}

impl AppState {
    pub fn new(path: PathBuf) -> Self {
        AppState { settings: Mutex::new(Settings::load(&path)), path, chat: Mutex::new(None) }
    }
}

fn fail(detail: &str) -> ApiError {
    ApiError { status: 0, detail: detail.to_string() }
}

async fn creds(state: &AppState) -> Result<(Server, String), ApiError> {
    let s = state.settings.lock().await;
    match (&s.token, s.paired()) {
        (Some(token), true) => Ok((Server { host: s.host.clone(), fp: s.fp.clone() }, token.clone())),
        _ => Err(fail("not paired")),
    }
}

/// Spike: GET /health on a server with a pinned fingerprint. No token needed.
#[tauri::command]
pub async fn health(host: String, fp: String) -> Result<Value, ApiError> {
    net::request(&Server { host, fp }, "GET", "/health", None, None).await
}

#[tauri::command]
pub async fn get_settings(state: State<'_, AppState>) -> Result<Public, ApiError> {
    Ok(state.settings.lock().await.public())
}

#[tauri::command]
pub async fn pair(state: State<'_, AppState>, link: String, name: String) -> Result<Public, ApiError> {
    let l = link::parse(&link).map_err(|e| fail(&e))?;
    let server = Server { host: l.host.clone(), fp: l.fp.clone() };
    let name = if name.trim().is_empty() { "This computer".to_string() } else { name.trim().to_string() };
    let res = net::request(&server, "POST", "/pair", None, Some(json!({"code": l.code, "name": name}))).await?;
    let token = res["token"].as_str().ok_or_else(|| fail("the server sent no token"))?.to_string();
    let device_id = res["device_id"].as_str().unwrap_or_default().to_string();
    let mut s = state.settings.lock().await;
    *s = Settings { host: l.host, fp: l.fp, token: Some(token), device_name: name, device_id };
    s.save(&state.path).map_err(|e| fail(&e))?;
    Ok(s.public())
}

/// Best effort: stop the chat, ask the server to revoke this device, forget the token.
#[tauri::command]
pub async fn unpair(state: State<'_, AppState>) -> Result<Public, ApiError> {
    if let Some(h) = state.chat.lock().await.take() {
        let _ = h.stop.send(true);
    }
    if let Ok((server, token)) = creds(&state).await {
        let id = state.settings.lock().await.device_id.clone();
        if !id.is_empty() {
            let _ = net::request(&server, "DELETE", &format!("/devices/{id}"), Some(&token), None).await;
        }
    }
    let mut s = state.settings.lock().await;
    s.token = None;
    s.save(&state.path).map_err(|e| fail(&e))?;
    Ok(s.public())
}

/// The UI's only way to the REST API. Only fixed verbs and paths on our own server are allowed.
#[tauri::command]
pub async fn api(state: State<'_, AppState>, method: String, path: String, body: Option<Value>) -> Result<Value, ApiError> {
    let method = method.to_uppercase();
    let verb_ok = matches!(method.as_str(), "GET" | "POST" | "PATCH" | "PUT" | "DELETE");
    if !verb_ok || !path.starts_with('/') || path.contains("..") || path.contains("://") {
        return Err(fail("bad request"));
    }
    let (server, token) = creds(&state).await?;
    net::request(&server, &method, &path, Some(&token), body).await
}

#[tauri::command]
pub async fn chat_connect(app: AppHandle, state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut slot = state.chat.lock().await;
    if slot.is_some() {
        return Ok(());
    }
    let (server, token) = creds(&state).await?;
    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let (stop, stop_rx) = watch::channel(false);
    tauri::async_runtime::spawn(chat::supervise(server, token, rx, stop_rx, move |v| {
        let _ = app.emit("chat", v);
    }));
    *slot = Some(ChatHandle { tx, stop });
    Ok(())
}

#[tauri::command]
pub async fn chat_say(state: State<'_, AppState>, text: String) -> Result<(), ApiError> {
    match state.chat.lock().await.as_ref() {
        Some(h) => h.tx.send(text).map_err(|_| fail("the chat is not connected")),
        None => Err(fail("the chat is not connected")),
    }
}

#[tauri::command]
pub async fn chat_disconnect(state: State<'_, AppState>) -> Result<(), ApiError> {
    if let Some(h) = state.chat.lock().await.take() {
        let _ = h.stop.send(true);
    }
    Ok(())
}
```

- [ ] **Step 6: Register everything in `lib.rs`** (replace the `run()` function)

```rust
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    init_crypto();
    tauri::Builder::default()
        .setup(|app| {
            use tauri::Manager;
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            app.manage(commands::AppState::new(dir.join("settings.json")));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::health,
            commands::get_settings,
            commands::pair,
            commands::unpair,
            commands::api,
            commands::chat_connect,
            commands::chat_say,
            commands::chat_disconnect
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

- [ ] **Step 7: Run all Rust tests and a full build**

Run: `cargo test` then `cargo build`
Expected: unit tests (9) + `pinned_http` (5) + `chat_ws` (3) all pass; the build succeeds.

- [ ] **Step 8: Checkpoint**

---

### Task 9: Frontend types, IPC wrapper, chat state

**Files:**
- Create: `app/src/lib/types.ts`, `ipc.ts`, `mockIpc.ts`, `chat.ts`, `chatStore.ts`, `app/src/lib/chat.test.ts`

- [ ] **Step 1: Write the failing test** `app/src/lib/chat.test.ts`

```ts
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

  it("busy from the server is explained, and keeps the turn busy", () => {
    const s = server(chatReducer(initialChat, { kind: "user", text: "hi" }), { type: "busy" });
    expect(s.messages.at(-1)?.role).toBe("system");
    expect(s.busy).toBe(true);
  });
});
```


- [ ] **Step 2: Run it to verify it fails**

Run (in `app/`): `pnpm test`
Expected: FAIL: `Cannot find module './chat'`.

- [ ] **Step 3: Write `types.ts`**

```ts
export interface Settings {
  paired: boolean;
  host: string;
  device_name: string;
}

export interface PlanEvent {
  id: string;
  title: string;
  date: string; // YYYY-MM-DD
  time: string | null; // HH:MM, Singapore time
  start: string;
  duration_min: number | null;
  kind: string;
  status: string;
  notes: string;
  project: string;
  repeat: string;
}

export interface PlanResponse {
  start: string;
  days: number;
  events: PlanEvent[];
}

/** Messages from the server's /chat WebSocket (docs/core-api.md) plus two the Rust layer adds. */
export type ClientEvent =
  | { type: "ready"; device: string }
  | { type: "delta"; text: string }
  | { type: "event"; name: string; ok: boolean; summary: string }
  | { type: "final"; text: string }
  | { type: "error"; message: string }
  | { type: "busy" }
  | { type: "pong" }
  | { type: "disconnected"; reason: string }
  | { type: "unpaired" };

export interface ApiError {
  status: number;
  detail: string;
}
```

- [ ] **Step 4: Write `chat.ts`**

```ts
import type { ClientEvent } from "./types";

export interface Message {
  id: number;
  role: "cyph" | "user" | "event" | "system";
  text: string; // cyph text is raw, including its leading [mood] tag; use parseMood to display it
  streaming?: boolean;
  ok?: boolean;
}

export interface ChatState {
  messages: Message[];
  connected: boolean;
  busy: boolean;
  mood: string;
  nextId: number;
}

export type Action = { kind: "user"; text: string } | { kind: "server"; msg: ClientEvent };

export const initialChat: ChatState = { messages: [], connected: false, busy: false, mood: "rest", nextId: 1 };

const TAG = /^\s*\[([a-z]+)\]\s*/;

/** Splits the leading mood tag off Cyph's text. A tag that has not finished arriving shows nothing yet. */
export function parseMood(text: string): { mood?: string; body: string } {
  const m = TAG.exec(text);
  if (m) return { mood: m[1], body: text.slice(m[0].length) };
  if (/^\s*\[[a-z]*$/.test(text)) return { body: "" };
  return { body: text };
}

function push(s: ChatState, m: Omit<Message, "id">): ChatState {
  return { ...s, messages: [...s.messages, { ...m, id: s.nextId }], nextId: s.nextId + 1 };
}

function withMood(s: ChatState, text: string): ChatState {
  const { mood } = parseMood(text);
  return mood ? { ...s, mood } : s;
}

function setReply(s: ChatState, text: string, streaming: boolean): ChatState {
  const msgs = s.messages;
  const at = msgs.findLastIndex((m) => m.role === "cyph" && m.streaming);
  let next: ChatState;
  if (at >= 0) {
    const copy = msgs.slice();
    copy[at] = { ...copy[at], text, streaming };
    next = { ...s, messages: copy };
  } else {
    next = push(s, { role: "cyph", text, streaming });
  }
  return withMood(next, text);
}

export function chatReducer(s: ChatState, a: Action): ChatState {
  if (a.kind === "user") return { ...push(s, { role: "user", text: a.text }), busy: true };
  const m = a.msg;
  switch (m.type) {
    case "ready":
      return { ...s, connected: true };
    case "disconnected":
      return { ...s, connected: false, busy: false };
    case "unpaired":
      return { ...push(s, { role: "system", text: "This device was unpaired. Pair it again to keep chatting." }), connected: false, busy: false };
    case "delta": {
      const at = s.messages.findLastIndex((x) => x.role === "cyph" && x.streaming);
      const soFar = at >= 0 ? s.messages[at].text : "";
      return setReply(s, soFar + m.text, true);
    }
    case "event":
      return push(s, { role: "event", text: m.summary, ok: m.ok });
    case "final":
      return { ...setReply(s, m.text, false), busy: false };
    case "error": {
      const closed = s.messages.map((x) => (x.streaming ? { ...x, streaming: false } : x));
      return { ...push({ ...s, messages: closed }, { role: "system", text: m.message }), busy: false };
    }
    case "busy":
      return push(s, { role: "system", text: "Cyph is still answering the last thing." });
    default:
      return s;
  }
}
```

(`findLastIndex` needs a TS `lib` of ES2023; set `"lib": ["ES2023", "DOM", "DOM.Iterable"]` and `"target": "ES2022"` in `app/tsconfig.json` if the scaffold's lib is older.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm test`
Expected: all chat tests pass. If the test about tool events ordering fails, remember it asserts `["user","cyph","event"]` (the final replaces the streaming message in place).

- [ ] **Step 6: Write `ipc.ts`, `mockIpc.ts`, `chatStore.ts`** (thin glue; covered by the UI render test and the live run)

`app/src/lib/ipc.ts`:

```ts
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { mockIpc } from "./mockIpc";
import type { ClientEvent, Settings } from "./types";

export interface Ipc {
  getSettings(): Promise<Settings>;
  pair(link: string, name: string): Promise<Settings>;
  unpair(): Promise<Settings>;
  api<T>(method: string, path: string, body?: unknown): Promise<T>;
  chatConnect(): Promise<void>;
  chatSay(text: string): Promise<void>;
  chatDisconnect(): Promise<void>;
  onChat(cb: (e: ClientEvent) => void): Promise<() => void>;
}

const real: Ipc = {
  getSettings: () => invoke("get_settings"),
  pair: (link, name) => invoke("pair", { link, name }),
  unpair: () => invoke("unpair"),
  api: (method, path, body) => invoke("api", { method, path, body }),
  chatConnect: () => invoke("chat_connect"),
  chatSay: (text) => invoke("chat_say", { text }),
  chatDisconnect: () => invoke("chat_disconnect"),
  onChat: (cb) => listen<ClientEvent>("chat", (e) => cb(e.payload)),
};

/** `?mock` in the URL swaps in an in-memory fake, so the UI can be built and looked at in a plain browser. */
const useMock = typeof location !== "undefined" && new URLSearchParams(location.search).has("mock");
export const ipc: Ipc = useMock ? mockIpc() : real;
```

`app/src/lib/mockIpc.ts`:

```ts
import type { Ipc } from "./ipc";
import type { ClientEvent, PlanEvent } from "./types";

export function mockIpc(): Ipc {
  const subs = new Set<(e: ClientEvent) => void>();
  const emit = (e: ClientEvent) => subs.forEach((s) => s(e));
  let paired = new URLSearchParams(location.search).get("mock") !== "unpaired";
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore" }).format(new Date());
  const events: PlanEvent[] = [
    ["m1", "Maths revision", "09:00", 120, "block"],
    ["m2", "Cyph planner", "11:30", 90, "block"],
    ["m3", "NCDCC parade", "14:00", 180, "event"],
  ].map(([id, title, time, dur, kind]) => ({
    id: id as string, title: title as string, date: today, time: time as string, start: `${today}T${time}:00+08:00`,
    duration_min: dur as number, kind: kind as string, status: "planned", notes: "", project: "", repeat: "none",
  }));
  return {
    getSettings: async () => ({ paired, host: "mock:0", device_name: "Mock PC" }),
    pair: async () => ((paired = true), { paired, host: "mock:0", device_name: "Mock PC" }),
    unpair: async () => ((paired = false), { paired, host: "mock:0", device_name: "Mock PC" }),
    api: async <T,>(_m: string, path: string) => (path.startsWith("/plan") ? ({ start: today, days: 1, events } as T) : ({} as T)),
    chatConnect: async () => void setTimeout(() => emit({ type: "ready", device: "Mock PC" }), 200),
    chatSay: async (text) => {
      const reply = `[curious] You said "${text}". This is the mock Cyph, so I just echo.`;
      let at = 0;
      const tick = setInterval(() => {
        at = Math.min(reply.length, at + 6);
        emit({ type: "delta", text: reply.slice(at - 6 < 0 ? 0 : at - 6, at) });
        if (at >= reply.length) {
          clearInterval(tick);
          emit({ type: "final", text: reply });
        }
      }, 80);
    },
    chatDisconnect: async () => {},
    onChat: async (cb) => (subs.add(cb), () => subs.delete(cb)),
  };
}
```

`app/src/lib/chatStore.ts`:

```ts
import { useSyncExternalStore } from "react";
import { chatReducer, initialChat, type Action, type ChatState } from "./chat";
import { ipc } from "./ipc";

let state: ChatState = initialChat;
const listeners = new Set<() => void>();
const dispatch = (a: Action) => {
  state = chatReducer(state, a);
  listeners.forEach((l) => l());
};

const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));
export const useChatState = () => useSyncExternalStore(subscribe, () => state);

/** Hooks fired for every server message, so other parts of the UI (the day ruler) can react to tool events. */
export const onServerMessage = new Set<(e: import("./types").ClientEvent) => void>();

let off: (() => void) | null = null;
export async function startChat() {
  if (off) return;
  off = await ipc.onChat((msg) => {
    dispatch({ kind: "server", msg });
    onServerMessage.forEach((f) => f(msg));
  });
  await ipc.chatConnect();
}

export async function stopChat() {
  off?.();
  off = null;
  await ipc.chatDisconnect();
}

export async function say(text: string) {
  const t = text.trim();
  if (!t) return;
  dispatch({ kind: "user", text: t });
  try {
    await ipc.chatSay(t);
  } catch (e) {
    dispatch({ kind: "server", msg: { type: "error", message: String((e as { detail?: string })?.detail ?? e) } });
  }
}
```

- [ ] **Step 7: Type-check**

Run: `pnpm exec tsc --noEmit`
Expected: no errors. Fix real type errors; do not weaken types with `any`.

- [ ] **Step 8: Checkpoint**

---

### Task 10: Today's plan maths

**Files:**
- Create: `app/src/lib/day.ts`, `app/src/lib/plan.ts`, `app/src/lib/day.test.ts`

- [ ] **Step 1: Write the failing test** `app/src/lib/day.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { currentBlock, formatLeft, hhmm, layoutDay, nextBlock, nowPx, sgNow, toBlocks } from "./day";
import type { PlanEvent } from "./types";

const ev = (id: string, title: string, time: string | null, dur: number | null, kind = "block", date = "2026-10-24", status = "planned"): PlanEvent => ({
  id, title, date, time, start: "", duration_min: dur, kind, status, notes: "", project: "", repeat: "none",
});

const events = [
  ev("a", "Maths", "09:00", 120),
  ev("b", "Planner", "11:30", 90),
  ev("c", "Parade", "14:00", null, "event"),
  ev("d", "All day thing", null, null),
  ev("e", "Other day", "10:00", 60, "block", "2026-10-25"),
  ev("f", "Skipped", "16:00", 60, "block", "2026-10-24", "skipped"),
  ev("g", "Deadline", "17:00", null, "deadline"),
];

describe("toBlocks", () => {
  it("keeps only timed, non-skipped events of that day, sorted, with default lengths", () => {
    const b = toBlocks(events, "2026-10-24");
    expect(b.map((x) => x.id)).toEqual(["a", "b", "c", "g"]);
    expect(b[0]).toMatchObject({ startMin: 540, endMin: 660 });
    expect(b[2]).toMatchObject({ startMin: 840, endMin: 900 }); // no duration: one hour
    expect(b[3]).toMatchObject({ startMin: 1020, endMin: 1035 }); // deadlines are drawn as a short tick
  });
});

describe("layoutDay", () => {
  it("places blocks on a ruler from startHour with pxPerHour", () => {
    const blocks = toBlocks(events, "2026-10-24");
    const rows = layoutDay(blocks, { startHour: 8, endHour: 20, pxPerHour: 30 });
    expect(rows[0]).toMatchObject({ top: 30, height: 60 }); // 09:00 is 1h after 08:00, two hours long
    expect(rows.every((r) => r.top >= 0 && r.height >= 14)).toBe(true);
  });
  it("nowPx is null outside the ruler", () => {
    const o = { startHour: 8, endHour: 20, pxPerHour: 30 };
    expect(nowPx(9 * 60, o)).toBe(30);
    expect(nowPx(7 * 60, o)).toBeNull();
    expect(nowPx(21 * 60, o)).toBeNull();
  });
});

describe("currentBlock / nextBlock", () => {
  const blocks = toBlocks(events, "2026-10-24");
  it("finds the block we are in, with time left and progress", () => {
    const cur = currentBlock(blocks, 9 * 60 + 48);
    expect(cur?.block.id).toBe("a");
    expect(cur?.leftMin).toBe(72);
    expect(cur?.progress).toBeCloseTo(0.4);
  });
  it("is null between blocks and the next one is reported", () => {
    expect(currentBlock(blocks, 11 * 60 + 10)).toBeNull();
    expect(nextBlock(blocks, 11 * 60 + 10)?.id).toBe("b");
    expect(nextBlock(blocks, 23 * 60)).toBeNull();
  });
});

describe("formatting", () => {
  it("formats time left", () => {
    expect(formatLeft(72)).toBe("1h 12m");
    expect(formatLeft(60)).toBe("1h");
    expect(formatLeft(42)).toBe("42m");
    expect(formatLeft(0.4)).toBe("<1m");
    expect(hhmm(545)).toBe("09:05");
  });
  it("reads Singapore time whatever the machine's zone", () => {
    expect(sgNow(new Date("2026-10-24T01:30:00Z"))).toEqual({ date: "2026-10-24", minutes: 9 * 60 + 30 });
    expect(sgNow(new Date("2026-10-23T17:00:00Z"))).toEqual({ date: "2026-10-24", minutes: 60 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test`
Expected: FAIL: `Cannot find module './day'`.

- [ ] **Step 3: Write `day.ts`**

```ts
import type { PlanEvent } from "./types";

export interface Block {
  id: string;
  title: string;
  startMin: number; // minutes after midnight
  endMin: number;
  kind: string;
  status: string;
}

export interface Ruler {
  startHour: number;
  endHour: number;
  pxPerHour: number;
}

const DEFAULT_MIN = 60;
const POINT_KINDS = ["deadline", "reminder"];
const POINT_MIN = 15; // how long a deadline or reminder is drawn

const mins = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

export const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

export function toBlocks(events: PlanEvent[], date: string): Block[] {
  return events
    .filter((e) => e.date === date && e.time && e.status !== "skipped")
    .map((e) => {
      const startMin = mins(e.time as string);
      const length = e.duration_min ?? (POINT_KINDS.includes(e.kind) ? POINT_MIN : DEFAULT_MIN);
      return { id: e.id, title: e.title, startMin, endMin: startMin + length, kind: e.kind, status: e.status };
    })
    .sort((a, b) => a.startMin - b.startMin);
}

export function layoutDay(blocks: Block[], r: Ruler) {
  return blocks.map((block) => ({
    block,
    top: ((block.startMin - r.startHour * 60) / 60) * r.pxPerHour,
    height: Math.max(14, ((block.endMin - block.startMin) / 60) * r.pxPerHour),
  }));
}

export function nowPx(nowMin: number, r: Ruler): number | null {
  if (nowMin < r.startHour * 60 || nowMin > r.endHour * 60) return null;
  return ((nowMin - r.startHour * 60) / 60) * r.pxPerHour;
}

export function currentBlock(blocks: Block[], nowMin: number) {
  const block = blocks.find((b) => b.startMin <= nowMin && nowMin < b.endMin);
  if (!block) return null;
  return {
    block,
    leftMin: block.endMin - nowMin,
    progress: (nowMin - block.startMin) / (block.endMin - block.startMin),
  };
}

export function nextBlock(blocks: Block[], nowMin: number): Block | null {
  return blocks.find((b) => b.startMin > nowMin) ?? null;
}

export function formatLeft(min: number): string {
  if (min < 1) return "<1m";
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (h && m) return `${h}h ${m}m`;
  return h ? `${h}h` : `${m}m`;
}

/** The date and minute-of-day in Singapore, whatever zone this machine is set to. */
export function sgNow(now: Date = new Date()): { date: string; minutes: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(now).map((p) => [p.type, p.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}

export const KIND_COLOR: Record<string, string> = {
  block: "#5dff7a",
  task: "#8be37a",
  class: "#5b9fb8",
  test: "#f08a73",
  deadline: "#e0584c",
  event: "#5b9fb8",
  reminder: "#d9984a",
};
```

- [ ] **Step 4: Write `plan.ts`** (the data hook; covered by the live run)

```ts
import { useCallback, useEffect, useState } from "react";
import { ipc } from "./ipc";
import { sgNow } from "./day";
import type { ApiError, PlanEvent, PlanResponse } from "./types";

export function usePlan(onUnpaired: () => void) {
  const [events, setEvents] = useState<PlanEvent[]>([]);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(() => sgNow());

  const refresh = useCallback(async () => {
    try {
      const { date } = sgNow();
      const res = await ipc.api<PlanResponse>("GET", `/plan?start=${date}&days=1`);
      setEvents(res.events);
      setError("");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401) onUnpaired();
      else setError(err.detail ?? String(e));
    }
  }, [onUnpaired]);

  useEffect(() => {
    void refresh();
    const clock = setInterval(() => setTick(sgNow()), 30_000);
    const reload = setInterval(() => void refresh(), 5 * 60_000);
    return () => (clearInterval(clock), clearInterval(reload));
  }, [refresh]);

  return { events, error, now: tick, refresh };
}
```

- [ ] **Step 5: Run the tests**

Run: `pnpm test`
Expected: all pass (chat + day).

- [ ] **Step 6: Checkpoint**

---

### Task 11: Bake Cyph's face and draw it

**Files:**
- Create: `tools_bake_face.py`, `tests/test_bake_face.py`, `app/src/face/frames.json` (generated), `app/src/face/compose.ts`, `app/src/face/compose.test.ts`, `app/src/face/Face.tsx`

- [ ] **Step 1: Write the failing Python test** `tests/test_bake_face.py`

```python
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tools_bake_face as B
from cyph import face as F, theme as C


class TestBake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = B.bake()

    def test_every_mood_is_baked_with_both_frames(self):
        self.assertEqual(set(self.data["moods"]), set(C.FACES))
        w, h = self.data["w"], self.data["h"]
        for name, m in self.data["moods"].items():
            self.assertEqual(len(m["base"]), w * h, name)
            self.assertEqual(len(m["blink"]), w * h, name)
            self.assertRegex(m["color"], r"^#[0-9a-f]{6}$")
            self.assertEqual(len(m["glyph"]), 3)

    def test_the_palette_fits_one_character_per_cell(self):
        self.assertLessEqual(len(self.data["palette"]), 64)
        for m in self.data["moods"].values():
            for ch in m["base"] + m["blink"]:
                self.assertTrue(ch == "." or ch in B.CHARS)

    def test_blinking_actually_changes_the_eyes(self):
        for name in ("rest", "happy", "curious"):
            m = self.data["moods"][name]
            self.assertNotEqual(m["base"], m["blink"], name)

    def test_moods_differ_from_each_other(self):
        bases = {m["base"] for m in self.data["moods"].values()}
        self.assertGreater(len(bases), 15)

    def test_mouths_and_geometry_come_from_the_renderer(self):
        self.assertEqual(self.data["mouthBox"], list(F.MOUTH_BOX))
        self.assertEqual(self.data["mouthAt"], list(F.MOUTH_AT))
        self.assertIn("talk3", self.data["mouths"])
        self.assertEqual(self.data["mouths"]["cat"], F.MOUTHS["smile"])   # aliases are resolved
        self.assertEqual(self.data["talkStyles"], list(F.TALK_STYLES))

    def test_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "frames.json")
            B.write(out)
            self.assertEqual(json.load(open(out, encoding="utf-8"))["w"], F.W)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run (project root): `python -W ignore -m pytest tests/test_bake_face.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'tools_bake_face'`.

- [ ] **Step 3: Write `tools_bake_face.py`**

```python
"""Bakes Cyph's mood faces into app/src/face/frames.json for the desktop/phone app.

The app does not re-implement face.py: it draws these pre-made frames. Per mood there is a normal frame and a blink
frame (ears in the mood's resting pose; no gaze, no wind, no random ear flicks in v1), plus the mouth shapes the app
overlays while Cyph is talking.

Run:  python tools_bake_face.py            (writes app/src/face/frames.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cyph import face as F
from cyph import theme as C

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz+/"
OUT = Path(__file__).resolve().parent / "app" / "src" / "face" / "frames.json"


def _quiet_time() -> float:
    """A time at which neither ear is in the middle of a random flick, so ears are in their resting pose."""
    t = 50.0
    while F._flick(t, 0, 1.0) != 0.0 or F._flick(t, 1, 1.0) != 0.0:
        t += 0.05
    return t


def _grid(mood: str, blink: bool, t_quiet: float):
    el, er, mg = C.FACES[mood]
    left = "closed" if blink else F.EYE_GLYPH.get(el, "half")
    right = "closed" if blink else F.EYE_GLYPH.get(er, "half")
    c = F._Canvas()
    if mood != "rest" or blink:                      # rest keeps the artwork's own eyes
        F._eye(c, left, F.LEFT_EYE, 0, 0.0)
        F._eye(c, right, F.RIGHT_EYE, 1, 0.0)
    if mood != "rest":                               # ...and its own smirk
        F._mouth(c, F.MOUTH_GLYPH.get(mg, "smirk"))
    for x, y in F.NEON_CELLS:
        c.px(x, y, C.NEON)
    F._ear_state.update(t=None, l=0.0, r=0.0)        # no smoothing history, no mood-change pop
    F._trans.update(m=None, t=0.0, seen=False)
    F._ears_anim(c, mood, False, False, t_quiet)
    return c.g


def bake() -> dict:
    t_quiet = _quiet_time()
    palette: list[str] = []

    def code(color):
        if color is None:
            return "."
        if color not in palette:
            palette.append(color)
        if len(palette) > len(CHARS):
            raise SystemExit("more than 64 colours: widen the encoding")
        return CHARS[palette.index(color)]

    def encode(grid):
        return "".join(code(cell) for row in grid for cell in row)

    moods = {}
    for mood, (el, er, mg) in C.FACES.items():
        moods[mood] = {
            "color": C.MOOD_COLOR.get(mood, C.DIM),
            "glyph": el + er + mg,
            "base": encode(_grid(mood, False, t_quiet)),
            "blink": encode(_grid(mood, True, t_quiet)),
        }
    mouths = {name: rows for name, rows in F.MOUTHS.items()}
    for alias, real in F.MOUTH_ALIAS.items():
        mouths[alias] = F.MOUTHS[real]
    return {
        "w": F.W, "h": F.H, "palette": palette, "chars": CHARS, "moods": moods,
        "mouths": mouths, "talkStyles": list(F.TALK_STYLES),
        "mouthBox": list(F.MOUTH_BOX), "mouthAt": list(F.MOUTH_AT),
        "colors": {"fur": F.FUR, "line": F.LINE, "mouthIn": F.MOUTH_IN, "tongue": F.MOUTH_TONGUE},
        "blinkMoods": ["rest", "sniff", "curious", "think", "boop"],
    }


def write(path=OUT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bake(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("wrote", write())
```

- [ ] **Step 4: Run the Python tests, then generate the file**

Run: `python -W ignore -m pytest tests/test_bake_face.py -q`
Expected: 6 passed.

Run: `python -W ignore tools_bake_face.py`
Expected: `wrote ...\app\src\face\frames.json` (a few hundred KB).

- [ ] **Step 5: Write the failing TS test** `app/src/face/compose.test.ts`

```ts
import { describe, expect, it } from "vitest";
import frames from "./frames.json";
import { applyMouth, decode, pickFrame } from "./compose";

describe("decode", () => {
  it("turns a baked frame into a grid of colours with transparent holes", () => {
    const g = decode(frames.moods.happy.base);
    expect(g).toHaveLength(frames.h);
    expect(g[0]).toHaveLength(frames.w);
    const colours = new Set(g.flat().filter(Boolean));
    expect(colours.size).toBeGreaterThan(5);
    expect(g.flat().some((c) => c === null)).toBe(true);
    for (const c of colours) expect(c).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe("applyMouth", () => {
  it("clears the mouth box then draws the shape from the lip line", () => {
    const g = decode(frames.moods.rest.base);
    const open = applyMouth(g, "open");
    const [x0, y0] = frames.mouthAt;
    expect(open[y0][x0]).toBe(frames.colors.line); // 'L' = lip line start
    expect(open[y0 + 1].slice(x0, x0 + 8).some((c) => c === frames.colors.mouthIn)).toBe(true);
    expect(g).not.toBe(open); // the source frame is not modified
    expect(decode(frames.moods.rest.base)[y0][x0]).toBe(g[y0][x0]);
  });
  it("knows every talking style", () => {
    const g = decode(frames.moods.happy.base);
    for (const s of frames.talkStyles) expect(() => applyMouth(g, s)).not.toThrow();
  });
});

describe("pickFrame", () => {
  it("blinks only for the moods that blink on their own, and only briefly", () => {
    expect(pickFrame("curious", 0.05)).toBe("blink");
    expect(pickFrame("curious", 1.0)).toBe("base");
    expect(pickFrame("happy", 0.05)).toBe("base");
  });
  it("an unknown mood falls back to rest", () => {
    expect(pickFrame("not-a-mood", 1.0)).toBe("base");
  });
});
```

Add `"resolveJsonModule": true` to `app/tsconfig.json` `compilerOptions` if it is not already set.

- [ ] **Step 6: Run to verify it fails**

Run (in `app/`): `pnpm test`
Expected: FAIL: `Cannot find module './compose'`.

- [ ] **Step 7: Write `compose.ts`**

```ts
import frames from "./frames.json";

export type Grid = (string | null)[][];

export const FACE_W = frames.w;
export const FACE_H = frames.h;

export function decode(text: string): Grid {
  const rows: Grid = [];
  for (let y = 0; y < frames.h; y++) {
    const row: (string | null)[] = [];
    for (let x = 0; x < frames.w; x++) {
      const ch = text[y * frames.w + x];
      row.push(ch === "." ? null : frames.palette[frames.chars.indexOf(ch)]);
    }
    rows.push(row);
  }
  return rows;
}

/** A copy of the grid with the mouth area cleared and the mouth `style` drawn (port of face._mouth). */
export function applyMouth(grid: Grid, style: string): Grid {
  const out = grid.map((row) => row.slice());
  const [bx0, by0, bx1, by1] = frames.mouthBox;
  for (let y = by0; y <= by1; y++) for (let x = bx0; x <= bx1; x++) out[y][x] = frames.colors.fur;
  const [mx, my] = frames.mouthAt;
  const shape = (frames.mouths as Record<string, string[]>)[style] ?? frames.mouths.smirk;
  shape.forEach((row, dy) =>
    [...row].forEach((ch, dx) => {
      if (ch === "T") out[my + dy][mx + dx] = frames.colors.tongue;
      else if (ch === "I") out[my + dy][mx + dx] = frames.colors.mouthIn;
      else if (ch === "#" || ch === "L") out[my + dy][mx + dx] = frames.colors.line;
    }),
  );
  return out;
}

/** "blink" for a moment every 4.3 s for the moods that blink on their own, else "base". `t` is seconds. */
export function pickFrame(mood: string, t: number): "base" | "blink" {
  const known = mood in frames.moods ? mood : "rest";
  return frames.blinkMoods.includes(known) && t % 4.3 < 0.12 ? "blink" : "base";
}

export function moodInfo(mood: string) {
  return (frames.moods as Record<string, { color: string; glyph: string; base: string; blink: string }>)[mood] ?? frames.moods.rest;
}

export const MOOD_NAMES = Object.keys(frames.moods);
export const TALK_STYLES: string[] = frames.talkStyles;
```

- [ ] **Step 8: Write `Face.tsx`**

```tsx
import { useEffect, useRef } from "react";
import { applyMouth, decode, FACE_H, FACE_W, moodInfo, pickFrame, TALK_STYLES, type Grid } from "./compose";

interface Props {
  mood: string;
  talking?: boolean;
  scale?: number; // canvas pixels per face pixel
}

export function Face({ mood, talking = false, scale = 3 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const info = moodInfo(mood);
    const frames = { base: decode(info.base), blink: decode(info.blink) };
    const talk = TALK_STYLES.map((s) => applyMouth(frames.base, s));
    const draw = (g: Grid) => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      g.forEach((row, y) =>
        row.forEach((c, x) => {
          if (c) {
            ctx.fillStyle = c;
            ctx.fillRect(x * scale, y * scale, scale, scale);
          }
        }),
      );
    };
    const start = performance.now();
    let last = 0;
    const timer = setInterval(() => {
      const t = (performance.now() - start) / 1000;
      if (talking) {
        if (t - last > 0.11) {
          last = t;
          draw(talk[Math.floor(Math.random() * talk.length)]);
        }
      } else {
        draw(frames[pickFrame(mood, t)]);
      }
    }, 40);
    return () => clearInterval(timer);
  }, [mood, talking, scale]);

  return <canvas ref={ref} width={FACE_W * scale} height={FACE_H * scale} style={{ imageRendering: "pixelated" }} />;
}
```

- [ ] **Step 9: Run all tests**

Run (in `app/`): `pnpm test` and `pnpm exec tsc --noEmit`
Expected: all pass; no type errors.

- [ ] **Step 10: Checkpoint**

---

### Task 12: The Bench UI

**Files:**
- Create: `app/src/styles/bench.css`, `app/src/ui/App.tsx` (replaces the Task 5 version), `Pair.tsx`, `Home.tsx`, `TopBar.tsx`, `Stage.tsx`, `Tape.tsx`, `DayRuler.tsx`, `app/src/ui/render.test.tsx`
- Modify: `app/src/main.tsx` (scaffold), `app/src/App.tsx` (delete the scaffold's), `app/index.html` (title)

- [ ] **Step 1: Build the stylesheet from the approved mockup**

The approved look is in `C:\Users\haziq\Documents\Cyph - The Furry Engineer\.superpowers\brainstorm\1297-1792767682\content\home-v2-identity.html`. Read it. Create `app/src/styles/bench.css` as follows:
1. Copy the CSS between the comment `/* ========== A · BENCH` and the comment `/* ========== B · NOTEBOOK` into the file, replacing every `.bn` with `.bench` and every occurrence of the `.sp` sprite use is NOT needed (the face is a canvas now).
2. Do NOT copy `@import url(...)` fonts. At the top of `app/src/main.tsx` import the bundled fonts instead:

```ts
import "@fontsource/pixelify-sans/400.css";
import "@fontsource/pixelify-sans/600.css";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";
import "./styles/bench.css";
```

3. Append these rules (the mockup had fixed sizes; the app is responsive and has a few states the mockup did not):

```css
html, body, #root { height: 100%; margin: 0; background: #10150e; }
.bench { min-height: 100%; box-sizing: border-box; border-radius: 0; }
.bench .grid { min-height: calc(100vh - 80px); }
.sw span { cursor: pointer; }
.sw span.off { opacity: .45; cursor: not-allowed; }
.leds i.x { background: #3b432b; box-shadow: none; }
.stage canvas { position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%); filter: drop-shadow(0 6px 0 rgba(0,0,0,.4)); }
.moods span { cursor: pointer; }
.cmd input { flex: 1; background: transparent; border: 0; outline: 0; color: #e6efd8; font: inherit; }
.cmd input::placeholder { color: var(--dim); }
.tape { overflow-y: auto; scrollbar-width: thin; }
.cy.streaming:after { content: " ▍"; color: var(--ne); animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.sys { color: var(--am); font-size: 11px; padding-left: 70px; }
.ev.bad { color: #e0584c; }
.pair { max-width: 520px; margin: 12vh auto; padding: 22px; }
.pair textarea, .pair input { width: 100%; box-sizing: border-box; background: #070a05; color: #e6efd8; border: 1px solid #2c3820; border-radius: 6px; padding: 10px; font: 12px 'DM Mono', monospace; }
.pair button { margin-top: 12px; padding: 10px 18px; border: 0; border-radius: 6px; background: #5dff7a; color: #10150e; font: 600 13px 'Pixelify Sans'; letter-spacing: .1em; cursor: pointer; }
.pair .err { color: #e0584c; margin-top: 10px; font-size: 12px; }
```

If a class in the mockup CSS differs from what the components below use, change the component, not the visual design.

- [ ] **Step 2: Write the failing render test** `app/src/ui/render.test.tsx`

```tsx
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Tape } from "./Tape";
import { DayRuler } from "./DayRuler";
import { initialChat, chatReducer } from "../lib/chat";
import type { PlanEvent } from "../lib/types";

describe("Tape", () => {
  it("shows Cyph's mood tag in the margin and strips it from the text", () => {
    let s = chatReducer(initialChat, { kind: "user", text: "hi" });
    s = chatReducer(s, { kind: "server", msg: { type: "final", text: "[wave] Hello, Cypher!" } });
    s = chatReducer(s, { kind: "server", msg: { type: "event", name: "plan_add", ok: true, summary: "plan_add: Maths (2026-10-24 09:00)" } });
    const html = renderToString(<Tape messages={s.messages} />);
    expect(html).toContain("[wave]");
    expect(html).toContain("Hello, Cypher!");
    expect(html).not.toContain("[wave] Hello");
    expect(html).toContain("plan_add: Maths");
    expect(html).toContain("hi");
  });
});

describe("DayRuler", () => {
  const ev: PlanEvent = { id: "a", title: "Maths revision", date: "2026-10-24", time: "09:00", start: "", duration_min: 120, kind: "block", status: "planned", notes: "", project: "", repeat: "none" };
  it("draws today's blocks and the NOW needle", () => {
    const html = renderToString(<DayRuler events={[ev]} now={{ date: "2026-10-24", minutes: 9 * 60 + 30 }} />);
    expect(html).toContain("Maths revision");
    expect(html).toContain("NOW");
  });
  it("says so when nothing is planned", () => {
    const html = renderToString(<DayRuler events={[]} now={{ date: "2026-10-24", minutes: 600 }} />);
    expect(html.toLowerCase()).toContain("nothing planned");
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run (in `app/`): `pnpm test`
Expected: FAIL: `Cannot find module './Tape'`. (If vitest does not pick up `.tsx` tests, add `include: ["src/**/*.test.{ts,tsx}"]` under `test:` in `vite.config.ts`, and set `esbuild: { jsx: "automatic" }`.)

- [ ] **Step 4: Write the components**

`app/src/ui/Tape.tsx`:

```tsx
import { useEffect, useRef } from "react";
import { parseMood, type Message } from "../lib/chat";

export function Tape({ messages }: { messages: Message[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages]);
  return (
    <div className="tape">
      {messages.map((m) => {
        if (m.role === "event") return <div key={m.id} className={"ev" + (m.ok === false ? " bad" : "")}>{m.text}</div>;
        if (m.role === "system") return <div key={m.id} className="sys">{m.text}</div>;
        if (m.role === "user")
          return (
            <div key={m.id} className="ln">
              <div className="tag u"><b>HAZ</b></div>
              <div className="us">{m.text}</div>
            </div>
          );
        const { mood, body } = parseMood(m.text);
        return (
          <div key={m.id} className="ln">
            <div className="tag"><b>CYPH</b>{mood ? `[${mood}]` : ""}</div>
            <div className={"cy" + (m.streaming ? " streaming" : "")}>{body}</div>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
```

`app/src/ui/DayRuler.tsx`:

```tsx
import { KIND_COLOR, hhmm, layoutDay, nowPx, toBlocks } from "../lib/day";
import type { PlanEvent } from "../lib/types";

const RULER = { startHour: 7, endHour: 23, pxPerHour: 28 };

export function DayRuler({ events, now }: { events: PlanEvent[]; now: { date: string; minutes: number } }) {
  const rows = layoutDay(toBlocks(events, now.date), RULER);
  const needle = nowPx(now.minutes, RULER);
  const hours = Array.from({ length: (RULER.endHour - RULER.startHour) / 2 + 1 }, (_, i) => RULER.startHour + i * 2);
  return (
    <div>
      <div className="day" style={{ height: (RULER.endHour - RULER.startHour) * RULER.pxPerHour }}>
        {hours.map((h) => (
          <span key={h} className="h" style={{ top: (h - RULER.startHour) * RULER.pxPerHour }}>{String(h).padStart(2, "0")}</span>
        ))}
        {needle !== null && <div className="now" style={{ top: needle }} />}
        {rows.map(({ block, top, height }) => (
          <div key={block.id} className="b" title={`${hhmm(block.startMin)} ${block.title}`}
               style={{ top, height, background: KIND_COLOR[block.kind] ?? "#5b9fb8", opacity: block.status === "done" ? 0.5 : 1 }}>
            {block.title}
          </div>
        ))}
      </div>
      {rows.length === 0 && <div className="ev" style={{ paddingLeft: 0 }}>nothing planned today</div>}
    </div>
  );
}
```

`app/src/ui/TopBar.tsx`:

```tsx
export function TopBar({ connected, onUnpair }: { connected: boolean; onUnpair: () => void }) {
  return (
    <div className="top">
      <div className="logo">CYPH<em>/</em>BENCH</div>
      <div className="sw">
        <span className="on">GENERAL</span>
        <span className="off" title="Study mode is coming">STUDY</span>
        <span className="off" title="Architecture mode is coming">ARCHITECT</span>
      </div>
      <div className="leds">
        <div><i className={connected ? "" : "x"} />{connected ? "SERVER SYNCED" : "SERVER OFFLINE"}</div>
        <div><i className="x" />ARM · OFFLINE</div>
        <div><i className="x" />VOICE · SOON</div>
        <div><button onClick={onUnpair} style={{ all: "unset", cursor: "pointer", color: "#6b7a5e" }} title="Forget this device">UNPAIR</button></div>
      </div>
    </div>
  );
}
```

`app/src/ui/Stage.tsx`:

```tsx
import { useState } from "react";
import { Face } from "../face/Face";
import { MOOD_NAMES, moodInfo } from "../face/compose";

const KEYS = ["happy", "curious", "excited", "love", "sleepy", "smug", "think", "dance", "shy", "wave", "grumpy"];

export function Stage({ mood, talking }: { mood: string; talking: boolean }) {
  const [preview, setPreview] = useState<string | null>(null);
  const shown = preview ?? mood;
  const info = moodInfo(shown);
  const pick = (m: string) => {
    setPreview(m);
    setTimeout(() => setPreview(null), 2500);
  };
  return (
    <div className="pn">
      <div className="lbl">Cyph <b>live</b></div>
      <div className="stage">
        <div className="mood">MOOD: {shown.toUpperCase()}</div>
        <div className="face" style={{ color: info.color }}>{[...info.glyph].join(" ")}</div>
        <Face mood={shown} talking={talking && !preview} scale={3} />
      </div>
      <div className="lbl" style={{ margin: "10px 0 6px" }}>Moods <b>{MOOD_NAMES.length} · tap to preview</b></div>
      <div className="moods">
        {KEYS.map((m) => (
          <span key={m} className={m === shown ? "on" : ""} onClick={() => pick(m)}>
            {[...moodInfo(m).glyph].join("")}<small>{m}</small>
          </span>
        ))}
        <span title="all moods">+{MOOD_NAMES.length - KEYS.length}<small>more</small></span>
      </div>
    </div>
  );
}
```

`app/src/ui/Pair.tsx`:

```tsx
import { useState } from "react";
import { ipc } from "../lib/ipc";
import type { ApiError } from "../lib/types";

export function Pair({ onPaired }: { onPaired: () => void }) {
  const [link, setLink] = useState("");
  const [name, setName] = useState("My computer");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    setErr("");
    try {
      await ipc.pair(link, name);
      onPaired();
    } catch (e) {
      setErr((e as ApiError).detail ?? String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bench">
      <div className="pair">
        <div className="logo">CYPH<em>/</em>BENCH</div>
        <p style={{ color: "#aab596" }}>Pair this device. On the server run <code>python deploy/deploy_server.py pair</code> and paste the “Pair link” line here.</p>
        <textarea rows={3} value={link} onChange={(e) => setLink(e.target.value)} placeholder="cyph://pair?host=...&fp=...&code=..." />
        <input style={{ marginTop: 10 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="device name" />
        <button disabled={busy || !link.trim()} onClick={go}>{busy ? "PAIRING..." : "PAIR"}</button>
        {err && <div className="err">{err}</div>}
      </div>
    </div>
  );
}
```

`app/src/ui/Home.tsx`:

```tsx
import { useEffect, useState } from "react";
import { onServerMessage, say, startChat, stopChat, useChatState } from "../lib/chatStore";
import { usePlan } from "../lib/plan";
import { DayRuler } from "./DayRuler";
import { Stage } from "./Stage";
import { Tape } from "./Tape";
import { TopBar } from "./TopBar";
import { ipc } from "../lib/ipc";

export function Home({ onUnpaired }: { onUnpaired: () => void }) {
  const chat = useChatState();
  const plan = usePlan(onUnpaired);
  const [text, setText] = useState("");

  useEffect(() => {
    void startChat();
    const react = (e: { type: string; name?: string }) => {
      if (e.type === "unpaired") onUnpaired();
      if (e.type === "event" && e.name?.startsWith("plan_")) void plan.refresh();
      if (e.type === "final") void plan.refresh();
    };
    onServerMessage.add(react);
    return () => {
      onServerMessage.delete(react);
      void stopChat();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const talking = chat.messages.some((m) => m.role === "cyph" && m.streaming);
  const send = () => {
    void say(text);
    setText("");
  };

  return (
    <div className="bench">
      <div className="circ" />
      <TopBar connected={chat.connected} onUnpair={async () => (await ipc.unpair(), onUnpaired())} />
      <div className="grid">
        <Stage mood={chat.mood} talking={talking} />
        <div className="pn tapewrap">
          <div className="lbl">Tape <b>{plan.now.date}</b></div>
          <div className="slot" />
          <Tape messages={chat.messages} />
          <div className="cmd">
            <span className="p">▸</span>
            <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
                   placeholder="type to Cyph (voice comes later)" />
            <span className="k live" onClick={send} style={{ cursor: "pointer" }}>SEND</span>
          </div>
        </div>
        <div className="pn">
          <div className="lbl">Day <b>{plan.now.date.slice(8)}</b></div>
          <DayRuler events={plan.events} now={plan.now} />
          {plan.error && <div className="ev bad" style={{ paddingLeft: 0 }}>{plan.error}</div>}
        </div>
      </div>
    </div>
  );
}
```

`app/src/ui/App.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { ipc } from "../lib/ipc";
import { Home } from "./Home";
import { Pair } from "./Pair";
import { Spike } from "./Spike";

export default function App() {
  const [paired, setPaired] = useState<boolean | null>(null);
  const refresh = useCallback(() => void ipc.getSettings().then((s) => setPaired(s.paired)), []);
  useEffect(refresh, [refresh]);

  if (location.hash === "#spike") return <Spike />;
  if (paired === null) return null;
  return paired ? <Home onUnpaired={() => setPaired(false)} /> : <Pair onPaired={() => setPaired(true)} />;
}
```

Delete the scaffold's `app/src/App.tsx` and `App.css` if present; in `app/src/main.tsx` import `App` from `./ui/App`. Set `<title>Cyph</title>` in `app/index.html`. Keep `Spike.tsx` from Task 5 (change its `invoke` import path only if needed).

- [ ] **Step 5: Run the tests and the type check**

Run (in `app/`): `pnpm test` then `pnpm exec tsc --noEmit`
Expected: all tests pass (chat, day, face, render); no type errors.

- [ ] **Step 6: Look at it in a plain browser with the mock**

Run (in `app/`): `pnpm dev` (the Vite dev server; note the printed URL, normally http://localhost:1420). Open `http://localhost:1420/?mock` in the browser pane. Compare with the approved mockup: Bench panel with circuit traces and screws, stage window with Cyph on it, mood keypad, the tape with serif replies, the day ruler with a NOW needle. Type a message: the mock echoes it with streaming text and Cyph's mouth moves while it streams; the mood tag appears in the margin. Open `?mock=unpaired`: the pair screen shows. Take screenshots and fix visible differences (spacing, fonts not loading, canvas position) by editing `bench.css`, not by changing the design.

- [ ] **Step 7: Checkpoint**

---

### Task 13: Live run, acceptance, and notes

**Files:**
- Create: `docs/app-dev.md`

- [ ] **Step 1: Pair the desktop app with the real server through the SSH tunnel**

Open the tunnel (leave it running): `ssh -i "$env:USERPROFILE\.ssh\cyph_server" -o BatchMode=yes -N -L 18443:127.0.0.1:8443 hazserver@192.168.0.149`.
Make a pair link: `python deploy/deploy_server.py pair`. The printed `Pair link` has the server's own host (`(set public_address ...)` or the Playit address); REPLACE the host part with `127.0.0.1:18443` for this test. Show the link only to Haziq; never write it to a file.
Run (in `app/`): `pnpm tauri dev`. Paste the link into the pair screen, press PAIR.
Expected: the Home appears; the top bar shows SERVER SYNCED.

- [ ] **Step 2: Acceptance checklist (record each result)**

1. Type "what's on my plan for the next 3 days?": the tape shows `plan_list` as a dispatch line, Cyph's reply streams in serif with a mood tag in the margin, the face changes to that mood and the mouth moves while text streams. (This is one real Gemini 3.8 call.)
2. Type "add a maths revision tomorrow at 9am for 2 hours" then "what's on tomorrow": a `plan_add` line appears; the reply confirms. (Today's ruler only shows today; check tomorrow by asking, or by `curl` through the tunnel with the device token only if Haziq wants to.)
3. Close the app and reopen it: it opens straight to Home (settings persisted), not the pair screen.
4. Stop the SSH tunnel while the app runs: the LED turns to SERVER OFFLINE within a few seconds; restart the tunnel: it reconnects by itself (backoff up to 30 s) and the LED returns.
5. Revoke this device on the server (`python deploy/deploy_server.py` has no revoke command, so use `ssh ... "cd ~/cyph-server/app && ~/cyph-server/venv/bin/python -m cyph.core revoke <id>"` after `devices`): the app shows "This device was unpaired" and returns to the pair screen.
6. Type a message while the server is unreachable: the app shows an error line; it is not silently lost.

- [ ] **Step 3: Clean up**

Stop the app, close the SSH tunnel, make sure no test device remains: `ssh ... "cd ~/cyph-server/app && ~/cyph-server/venv/bin/python -m cyph.core devices"` and revoke any leftover.

- [ ] **Step 4: Write `docs/app-dev.md`**

```markdown
# Cyph app: development notes

Layout: `app/` is a Tauri 2 project (Rust layer in `src-tauri/`, React + TypeScript UI in `src/`). The Rust layer owns
all network I/O and the device token; the UI only calls the commands in `src/lib/ipc.ts`.

## Commands
- `pnpm dev` (in `app/`): the UI alone in a browser. Add `?mock` to the URL for an in-memory fake server (no Tauri needed).
- `pnpm tauri dev`: the real app on this PC.
- `pnpm test`, `pnpm exec tsc --noEmit`: frontend tests and types. `cargo test` in `app/src-tauri`: Rust tests (they start
  local TLS servers; no real server needed). `python -m pytest tests/test_bake_face.py`: the face bake.
- `python tools_bake_face.py`: regenerate `app/src/face/frames.json` after changing `cyph/face.py` or `cyph/theme.py`.
- `pnpm tauri android build --debug --apk --target aarch64`: the phone build (needs the Android SDK/NDK, see plan 2 task 1).

## Pairing
`python deploy/deploy_server.py pair` prints a one-time pair link (valid 10 minutes). Paste it into the app.
The link carries the server address, the certificate fingerprint to pin, and the code.

## Spike result
(fill in from plan 2 task 6: which rows of the decision table passed, with the date)

## Known limits (v1)
- The device token is stored unencrypted in the per-user app data folder. Follow-up: OS keystore.
- Face: per-mood frames, blink and talking mouth only. No gaze, ear twitching or wind yet.
- A message dequeued at the moment the connection drops can be lost; the UI shows the disconnect.
- Arm panel, voice, Study and Architect modes are placeholders.
```

- [ ] **Step 5: Checkpoint.** Report the acceptance results (pass/fail per item) and the spike outcome to Haziq.

---

## Self-review

**Spec coverage** (spec section to task):
- §2 stack decision, Android risk: Part A (Tasks 1-6), decision table in Task 6.
- §2/§4 pinned TLS, token auth, per-device tokens: Tasks 3, 4, 7, 8 (`pair`, token in settings, `unpair` revokes the device).
- §6 Home (Bench, chat-first): mode switches, LEDs, stage + moods, tape with mood tags and dispatch lines, day ruler with NOW, command line: Tasks 11, 12. Arm panel and voice are placeholders (stated); the arm gauges from the mockup are intentionally not built until the arm plan.
- §6 mood and face: baked frames (Task 11); the 26 moods exist; talking mouth during streaming; blink. Gaze/ears/wind explicitly deferred (decision 3).
- §6 cache and queue, offline: NOT in this plan: the Home shows an error line and SERVER OFFLINE, and chat sends are not queued across a drop (known limit in `docs/app-dev.md`). The cache/queue is the phone plan's job (plan 4) because the desktop is mostly online; this is a conscious cut.
- §6 corner widget, visualizer, hotkey: plan 3. §7 voice: plan 5. §8 arm: plan 8.
- Telegram/server: nothing changes on the server except the optional `core --lan` flag in Task 6.

**Placeholder scan:** no TBD/TODO. Intentional soft spots, each marked where it occurs: (1) flags of `create-tauri-app` and some crate API names depend on versions resolved at install time (listed under "Known unknowns", with the exact fallback to try); (2) `docs/app-dev.md` has a "Spike result" line that is filled in by whoever ran Task 6, because the result does not exist yet; (3) Task 12 step 1 copies CSS from a mockup file on disk instead of inlining ~90 lines (the file path is exact and the transformation is mechanical).

**Type consistency:** Rust `net::Server{host,fp}`, `net::request(server, method, path, token, body)`, `ApiError{status,detail}`, `chat::session(server, token, &mut out, &mut stop, &emit)`, `chat::Ended`, `Settings{host,fp,token,device_name,device_id}` and `Public{paired,host,device_name}` are used identically in `commands.rs`, `lib.rs` and the tests. Frontend: `ClientEvent` (types.ts) matches what `chat.ts`, the mock and the Rust layer emit (`ready`, `delta`, `event`, `final`, `error`, `busy`, `pong`, `disconnected`, `unpaired`); command names in `ipc.ts` (`get_settings`, `pair`, `unpair`, `api`, `chat_connect`, `chat_say`, `chat_disconnect`) match the `generate_handler!` list; `PlanEvent` matches `docs/core-api.md`; `frames.json` keys used in `compose.ts` (`w`, `h`, `palette`, `chars`, `moods`, `mouths`, `talkStyles`, `mouthBox`, `mouthAt`, `colors`, `blinkMoods`) all come from `tools_bake_face.py::bake`.

**Risks, in order:** (1) the Android build/network step (Task 6) is the real unknown; everything before it can be done on the PC. (2) Rust code in this plan was written against current docs but has not been compiled; expect a few compile fixes in Tasks 3, 4 and 8. (3) The chat test in Task 8 depends on tokio-tungstenite's `Message::text` and `Utf8Bytes` API from the 0.28 line.
