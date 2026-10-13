param(
  [string]$VarsFile,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$CfPushArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command cf -ErrorAction SilentlyContinue)) {
  throw 'Cloud Foundry CLI (cf) was not found on PATH.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $VarsFile) {
  $VarsFile = Join-Path $repoRoot '.cf-vars.yml'
}

if (-not (Test-Path $VarsFile)) {
  $exampleVarsFile = Join-Path $repoRoot '.cf-vars.example.yml'
  Copy-Item $exampleVarsFile $VarsFile
  throw "Created $VarsFile from the example. Set the provider and credentials, then run deploy.ps1 again."
}

$resolvedVarsFile = (Resolve-Path $VarsFile).Path
$requiredVars = @(
  'llm-provider', 'aws-region', 'bedrock-model', 'aws-bearer-token-bedrock',
  'google-api-key', 'gemini-model', 'anthropic-api-key', 'anthropic-model',
  'deepseek-api-key', 'deepseek-model', 's4-base-url', 's4-username',
  's4-password', 's4-api-timeout-seconds'
)
$varsContent = Get-Content -Raw $resolvedVarsFile
$missingVars = @($requiredVars | Where-Object {
  $varsContent -notmatch "(?m)^\s*$([regex]::Escape($_))\s*:"
})
if ($missingVars.Count -gt 0) {
  throw "Add these missing keys to $resolvedVarsFile, then deploy again: $($missingVars -join ', ')"
}

Push-Location $repoRoot
try {
  $pushArgs = @('--vars-file', $resolvedVarsFile, '--redact-env') + $CfPushArgs
  & cf push @pushArgs
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}
finally {
  Pop-Location
}