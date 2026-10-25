/** A crash must never leave a blank window: show what broke, so it can be read and reported. */
export function showFatal(err: unknown) {
  const el = document.getElementById("fatal");
  if (!el) return;
  const e = err as { stack?: string; message?: string };
  el.style.display = "block";
  el.textContent = "Cyph hit an error and stopped drawing.\n\n" + (e?.stack || e?.message || String(err)) + "\n\nPress Ctrl+R to reload.";
}
addEventListener("error", (e) => showFatal(e.error ?? e.message));
addEventListener("unhandledrejection", (e) => showFatal(e.reason));
