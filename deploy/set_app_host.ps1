# Points the desktop app at a different server address. Only the "host" line changes; the token is never read or shown.
# Close the Cyph app first, run this, then start the app again.
param([string]$HostPort = "192.168.0.149:8443")
$p = Join-Path $env:APPDATA "com.cyph.app\settings.json"
$t = [IO.File]::ReadAllText($p)
$n = [regex]::Replace($t, '"host"\s*:\s*"[^"]*"', '"host": "' + $HostPort + '"')
[IO.File]::WriteAllText($p, $n, (New-Object Text.UTF8Encoding $false))
Select-String -Path $p -Pattern '"host"'
