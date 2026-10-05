param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$CfPushArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command cf -ErrorAction SilentlyContinue)) {
  throw 'Cloud Foundry CLI (cf) was not found on PATH.'
}

& cf push @CfPushArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}