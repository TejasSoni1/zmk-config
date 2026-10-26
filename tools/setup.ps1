# One-time setup. Run from the repo folder:
#   powershell -ExecutionPolicy Bypass -File tools\setup.ps1
# Creates github.com/<you>/zmk-config, pushes this folder, and starts the sync helper at login.
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

foreach ($t in @(@{cmd = "git"; id = "Git.Git" }, @{cmd = "gh"; id = "GitHub.cli" })) {
  if (-not (Get-Command $t.cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Installing $($t.cmd)..."
    winget install --id $t.id -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
  }
}

gh auth status *> $null
if ($LASTEXITCODE -ne 0) { gh auth login --web --git-protocol https }
$login = gh api user --jq .login

if (-not (Test-Path .git)) { git init -b main | Out-Null }
if (-not (git config user.name))  { git config user.name $login }
if (-not (git config user.email)) { git config user.email "$login@users.noreply.github.com" }

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m "Corne config: HRM, thumb layers, LaTeX-first symbols" | Out-Null }

if (-not (git remote | Select-String -SimpleMatch "origin")) {
  gh repo create zmk-config --public --source . --remote origin --push
} else {
  git push -u origin main
}

# Sync helper at login, plus a desktop shortcut to open it any time
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if ($pyw) {
  $ws = New-Object -ComObject WScript.Shell
  $script = Join-Path $repo "tools\keymap_sync.pyw"
  $startup = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "Corne keymap sync.lnk"))
  $startup.TargetPath = $pyw; $startup.Arguments = "`"$script`""; $startup.WorkingDirectory = $repo; $startup.Save()
  $panel = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Corne keymap.lnk"))
  $panel.TargetPath = $pyw; $panel.Arguments = "`"$script`" --panel"; $panel.WorkingDirectory = $repo; $panel.Save()
  Start-Process $pyw "`"$script`""
  Write-Host "Sync helper running. It starts with Windows from now on."
} else {
  Write-Host "Python isn't installed, so the sync helper is off. Install it with:"
  Write-Host "  winget install Python.Python.3.12"
  Write-Host "then run this script again."
}

gh repo view --web
