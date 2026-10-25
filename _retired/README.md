# Retired: the terminal Cyph

The desktop app (`app/`) plus the PC's arm agent (`python -m cyph.arm_agent`) replaced the Windows Terminal Cyph.
These launchers started it at logon (and a tray icon + a local Telegram service, which the server's bot replaced).
They are kept here, not deleted. `python -m cyph tui` still runs the old UI; `cyph/ui.py` was left in place because it
imports the package relatively. The two Startup-folder shortcuts were moved to `data/disabled_startup/`: put them back
in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` to re-enable (their targets now live in this folder).
