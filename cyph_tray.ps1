# Cyph background mode: starts the Telegram service and puts an icon in the system tray.
# Right-click the icon: open Cyph, open the log, or "Turn off background mode" (saves memory first).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$mutex = New-Object System.Threading.Mutex($false, "Local\CyphTray")
if (-not $mutex.WaitOne(0)) { exit }                       # already in the tray
$port = 47321                                              # the service holds this port while it runs
$stopFile = Join-Path $root "data\telegram.stop"
$log = Join-Path $root "data\telegram.log"

function Test-Alive {
    try { $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port); $l.Start(); $l.Stop(); return $false }
    catch { return $true }
}
function Start-Service-Hidden {
    $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command python).Source }
    Start-Process -FilePath $py -ArgumentList "-m", "cyph", "telegram" -WorkingDirectory $root -WindowStyle Hidden
}
function Stop-Service-Gently {
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    $end = (Get-Date).AddSeconds(45)
    while ((Test-Alive) -and (Get-Date) -lt $end) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 300 }
    if (Test-Alive) {                                      # did not exit in time: end it
        Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "-m cyph telegram" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item $stopFile -Force -ErrorAction SilentlyContinue
}

$ni = New-Object Windows.Forms.NotifyIcon
$ni.Icon = New-Object Drawing.Icon((Join-Path $root "cyph_outline.ico"))
$ni.Text = "Cyph background mode"
$menu = New-Object Windows.Forms.ContextMenuStrip
$status = $menu.Items.Add("Cyph: starting..."); $status.Enabled = $false
[void]$menu.Items.Add("-")
$openCyph = $menu.Items.Add("Open Cyph (arm, voice)")
$openLog = $menu.Items.Add("Open log")
$restart = $menu.Items.Add("Restart service")
[void]$menu.Items.Add("-")
$off = $menu.Items.Add("Turn off background mode")
$ni.ContextMenuStrip = $menu
$ni.Visible = $true

$openCyph.add_Click({ Start-Process wscript.exe -ArgumentList ('"' + (Join-Path $root "launch_cyph.vbs") + '"') })
$ni.add_DoubleClick({ Start-Process wscript.exe -ArgumentList ('"' + (Join-Path $root "launch_cyph.vbs") + '"') })
$openLog.add_Click({ if (Test-Path $log) { Start-Process notepad.exe -ArgumentList ('"' + $log + '"') } })
$restart.add_Click({
    $ni.ShowBalloonTip(2000, "Cyph", "Restarting the Telegram service...", "Info")
    Stop-Service-Gently; if (-not $onServer) { Start-Service-Hidden }
})
$off.add_Click({
    $ni.ShowBalloonTip(3000, "Cyph", "Saving memory and turning off...", "Info")
    Stop-Service-Gently
    $ni.Visible = $false; $ni.Dispose()
    [Windows.Forms.Application]::Exit()
})

$cfg = Join-Path $root "data\server.json"
$onServer = (Test-Path $cfg) -and ((Get-Content $cfg -Raw) -match '"telegram"\s*:\s*"server"')
if (-not $onServer -and -not (Test-Alive)) { Start-Service-Hidden }
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 3000
$timer.add_Tick({
    $alive = Test-Alive
    $status.Text = if ($onServer) { "Cyph: Telegram runs on the server" } elseif ($alive) { "Cyph: Telegram service running" } else { "Cyph: service stopped" }
    $ni.Text = if ($onServer) { "Cyph background mode (Telegram on server)" } elseif ($alive) { "Cyph background mode: on" } else { "Cyph background mode: service stopped" }
})
$timer.Start()
$ni.ShowBalloonTip(2500, "Cyph", "Background mode is on. Message me on Telegram.", "Info")
[Windows.Forms.Application]::Run()
$mutex.ReleaseMutex()
