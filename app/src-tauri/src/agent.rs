//! Starts the arm agent (`python -m cyph.arm_agent`) when the app starts, so the arm is simply on.
//!
//! The agent is the PC's one owner of the arm's serial port. It is only started once it has been paired (its token is in
//! cyph/.env), and it watches this process: when the app ends it rests the arm and exits by itself. Its output goes to
//! `arm_agent.log` in the app's data folder. Set CYPH_NO_ARM=1 to keep it from starting, CYPH_PYTHON to pick the Python.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

use tauri::{AppHandle, Manager};

/// There is no Python (and no arm) on a phone: the PC's agent owns the arm.
#[cfg(not(desktop))]
pub fn start(_app: &AppHandle) {}

#[cfg(desktop)]
pub fn start(app: &AppHandle) {
    if std::env::var_os("CYPH_NO_ARM").is_some() {
        return;
    }
    // app/src-tauri -> app -> cyph (the folder that holds the `cyph` package and its .env)
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let paired = std::fs::read_to_string(root.join(".env"))
        .map(|t| t.lines().any(|l| l.trim_start().starts_with("CYPH_ARM_TOKEN=")))
        .unwrap_or(false);
    if !paired || !root.join("cyph").join("arm_agent.py").exists() {
        return;
    }
    let Ok(dir) = app.path().app_data_dir() else { return };
    // appended, not truncated: the run before a crash is exactly the one worth reading (the agent's own, fuller log is
    // cyph/data/arm_debug.log)
    let Ok(mut log) = OpenOptions::new().create(true).append(true).open(dir.join("arm_agent.log")) else { return };
    let _ = writeln!(log, "
===== app started, agent launching (parent pid {}) =====", std::process::id());
    let Ok(log_err) = log.try_clone() else { return };
    let python = std::env::var("CYPH_PYTHON").unwrap_or_else(|_| "python".into());
    let mut cmd = Command::new(python);
    cmd.args(["-u", "-m", "cyph.arm_agent", "--parent-pid", &std::process::id().to_string()])
        .current_dir(&root)
        .stdin(Stdio::null())
        .stdout(log)
        .stderr(log_err);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    if let Err(e) = cmd.spawn() {
        eprintln!("arm agent did not start: {e}");
    }
}
