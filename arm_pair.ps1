# Pairs this PC's arm agent with the server, once. The pair link is read from the clipboard and never printed.
#   1. In the Cyph app: Settings > Make a code, then COPY LINK.
#   2. Run this script. Afterwards the app starts the agent by itself.
Set-Location $PSScriptRoot
$link = (Get-Clipboard -Raw).Trim()
python -m cyph.arm_agent --pair $link
