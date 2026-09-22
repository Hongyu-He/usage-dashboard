# Run on your Windows laptop in PowerShell; OpenSSH Client must be installed.
param(
  [Parameter(Mandatory=$true)][string]$SshAlias,
  [ValidateRange(1024,65535)][int]$LocalPort = 18763,
  [ValidateRange(1024,65535)][int]$RemotePort = 18763,
  # Project directory on the remote machine; ~ expands there.
  [string]$RemoteDir = '~/usage-dashboard'
)
$ErrorActionPreference = 'Stop'
if ($SshAlias -notmatch '^[a-zA-Z0-9_.@:-]+$' -or $SshAlias.StartsWith('-')) {
  throw 'Use a plain SSH alias or user@host; put ProxyJump and keys in SSH config.'
}
# RemoteDir is spliced into the remote shell command: plain paths only.
if ($RemoteDir -notmatch '^[a-zA-Z0-9_./~-]+$' -or $RemoteDir.StartsWith('-')) {
  throw 'RemoteDir must be a plain path without spaces or shell characters.'
}
Get-Command ssh -ErrorAction Stop | Out-Null
& ssh -- $SshAlias "python3 $RemoteDir/control.py start"
if ($LASTEXITCODE -ne 0) { throw 'Remote service did not start.' }
# A visible SSH window supports password, MFA and host-key prompts.
$dashboardArgs = @('-NT', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30',
  '-o', 'ServerAliveCountMax=3', '-L', "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}", '--', $SshAlias)
$dashboardTunnel = Start-Process ssh -ArgumentList $dashboardArgs -PassThru
$dashboardUrl = "http://127.0.0.1:$LocalPort/"
try {
  $dashboardReady = $false
  for ($dashboardTry = 0; $dashboardTry -lt 60; $dashboardTry++) {
    Start-Sleep -Seconds 1
    if ($dashboardTunnel.HasExited) { throw 'SSH tunnel exited. Check the SSH window or use a different LocalPort.' }
    try {
      $dashboardHealth = Invoke-RestMethod -Uri "${dashboardUrl}healthz" -TimeoutSec 2
      if ($dashboardHealth.service -eq 'usage-dashboard') { $dashboardReady = $true; break }
    } catch { }
  }
  if (-not $dashboardReady) { throw 'No dashboard response. Complete SSH authentication in the SSH window, then retry.' }
  Start-Process $dashboardUrl
  Write-Host "Dashboard: $dashboardUrl"
  Read-Host 'Keep this window open; press Enter to close ONLY this tunnel' | Out-Null
} finally {
  # This exact process was launched above by this invocation.
  if (-not $dashboardTunnel.HasExited) { $dashboardTunnel.Kill(); $dashboardTunnel.WaitForExit() }
}
