' Starts launch_cyph.ps1 with no console window at all (a hidden PowerShell still gets a Terminal window
' flashing up when Windows Terminal is the default terminal). Arguments are passed through, e.g. -Delay 20.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
extra = ""
For Each a In WScript.Arguments
    If InStr(a, " ") > 0 Then a = """" & a & """"      ' WScript strips the quotes; put them back
    extra = extra & " " & a
Next
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\launch_cyph.ps1""" & extra, 0, False
