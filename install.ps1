$ErrorActionPreference = "Stop"

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetDir = Join-Path $HOME ".codex\pets\hachiware"

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceDir "pet.json") -Destination (Join-Path $targetDir "pet.json") -Force
Copy-Item -LiteralPath (Join-Path $sourceDir "spritesheet.webp") -Destination (Join-Path $targetDir "spritesheet.webp") -Force

Write-Host "Installed Hachiware pet to $targetDir"
Write-Host "Restart Codex if it does not appear immediately."
