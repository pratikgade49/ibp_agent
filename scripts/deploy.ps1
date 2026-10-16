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
$manifestFile = Join-Path $repoRoot 'manifest.yaml'
$requiredServices = @(
  @{ Offering = 'connectivity'; Plan = 'lite'; Name = 'ibp-connectivity' },
  @{ Offering = 'destination'; Plan = 'lite'; Name = 'ibp-destination' }
)
$requiredVars = @(
  'llm-provider', 'aws-region', 'bedrock-model', 'aws-bearer-token-bedrock',
  'google-api-key', 'gemini-model', 'anthropic-api-key', 'anthropic-model',
  'deepseek-api-key', 'deepseek-model', 's4-api-timeout-seconds'
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
  $servicesOutput = & cf services 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Could not list Cloud Foundry services: $($servicesOutput -join [Environment]::NewLine)"
  }

  foreach ($service in $requiredServices) {
    $servicePattern = '^\s*' + [regex]::Escape($service.Name) + '\s{2,}'
    $serviceExists = @($servicesOutput | Where-Object { $_ -match $servicePattern }).Count -gt 0
    if ($serviceExists) {
      Write-Host "Service instance '$($service.Name)' already exists."
      continue
    }

    Write-Host "Creating $($service.Offering)/$($service.Plan) service instance '$($service.Name)'..."
    & cf create-service $service.Offering $service.Plan $service.Name --wait
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to create service instance '$($service.Name)'. Check marketplace availability, quota, and CF target."
    }
  }

  $pushArgs = @('--vars-file', $resolvedVarsFile, '--redact-env') + $CfPushArgs
  if (Test-Path $manifestFile) {
    $pushArgs = @('--manifest', $manifestFile) + $pushArgs
  }
  & cf push @pushArgs
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}
finally {
  Pop-Location
}