' Cyph background mode: tray icon + Telegram service, no window. Turn it off from the tray icon.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
q = Chr(34)
sh.Run "powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File " & q & here & "\cyph_tray.ps1" & q, 0, False
