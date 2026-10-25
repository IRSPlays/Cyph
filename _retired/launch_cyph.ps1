# Starts Cyph in a Windows Terminal window pinned to the top-right of the screen, TUI only (focus mode:
# no tab bar). Used by the desktop shortcut and the Startup-folder shortcut. Problems go to data\launch.log.
#   -Delay N        wait N seconds first (at logon, so audio, camera and USB are up)
#   -Width/-Height  window size in pixels      -FontSize  text size (Terminal default is 12)
#   -Tabs           keep the normal tab bar and title bar
param([int]$Delay = 0, [int]$Width = 1100, [int]$Height = 800, [double]$FontSize = 9,
      [string]$CyphArgs = "--brain local", [switch]$Tabs)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# not "Cyph": a console started from the shortcut is titled with the shortcut's name, and would be mistaken
# for the running app
$title = "CyphTUI"
New-Item -ItemType Directory -Force (Join-Path $here "data") | Out-Null
$log = Join-Path $here "data\launch.log"
function Log($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) }

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CyphWin {
    delegate bool CB(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(CB cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int h2, uint flags);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    // every Windows Terminal window, not just the one Process.MainWindowHandle happens to report
    public static IntPtr Find(string title) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            var c = new StringBuilder(256); GetClassName(h, c, 256);
            if (c.ToString() == "CASCADIA_HOSTING_WINDOW_CLASS" && IsWindowVisible(h)) {
                var s = new StringBuilder(256); GetWindowText(h, s, 256);
                if (s.ToString() == title) { found = h; return false; }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

try {
    if ($Delay -gt 0) { Start-Sleep -Seconds $Delay }

    $have = [CyphWin]::Find($title)
    if ($have -ne [IntPtr]::Zero) {                      # already running: bring it to the front
        [CyphWin]::ShowWindow($have, 9) | Out-Null
        [CyphWin]::SetForegroundWindow($have) | Out-Null
        Log "already running, focused it"
        exit 0
    }

    $python = (& python -c "import sys;print(sys.executable)").Trim()
    # never inherit NO_COLOR (the whole UI is colour) and make sure truecolor is on
    # /c: the window closes when Cyph quits (q). If it exits with an error, pause so the message can be read.
    $cmd = "set NO_COLOR=&& set COLORTERM=truecolor&& `"$python`" -m cyph $CyphArgs || pause"

    # smaller text than the default: a hidden Windows Terminal profile "Cyph" (a fragment, so your own
    # settings are not edited). Terminal loads fragments when it starts.
    $frag = Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\Fragments\Cyph"
    New-Item -ItemType Directory -Force $frag | Out-Null
    $prof = '{ "profiles": [ { "name": "Cyph", "guid": "{6b6e2f0a-3c1d-4d6e-9a55-2f1c7a9d0c11}", "hidden": true, ' +
            '"font": { "face": "Cascadia Mono", "size": ' + $FontSize + ' }, "padding": "4" } ] }'
    Set-Content -Path (Join-Path $frag "cyph.json") -Value $prof -Encoding utf8

    $wtArgs = @("-w", "new")
    if (-not $Tabs) { $wtArgs += "--focus" }
    $wtArgs += @("-p", "Cyph", "-d", "`"$here`"", "--title", $title, "--suppressApplicationTitle", "cmd", "/c", $cmd)
    Start-Process wt.exe -ArgumentList $wtArgs
    Log "started: python -m cyph $CyphArgs"

    $win = [IntPtr]::Zero
    for ($i = 0; $i -lt 80 -and $win -eq [IntPtr]::Zero; $i++) { Start-Sleep -Milliseconds 250; $win = [CyphWin]::Find($title) }
    if ($win -eq [IntPtr]::Zero) { Log "WARNING: the Cyph window did not appear within 20 s"; exit 1 }

    # top right of the primary screen's work area
    Add-Type -AssemblyName System.Windows.Forms
    $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $w = [Math]::Min($Width, $wa.Width)
    $h = [Math]::Min($Height, $wa.Height)
    # SWP_NOZORDER 0x4 | SWP_SHOWWINDOW 0x40
    [CyphWin]::SetWindowPos($win, [IntPtr]::Zero, $wa.Right - $w, $wa.Top, $w, $h, 0x44) | Out-Null
    Log "placed at $($wa.Right - $w),$($wa.Top) size ${w}x${h}"
} catch {
    Log ("ERROR: " + $_.Exception.Message)
    throw
}
