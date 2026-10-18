' Starts the Cyph Telegram service with no window. Log: data\telegram.log. Stop it: end "pythonw" in Task Manager.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run "pythonw -m cyph telegram", 0, False
