$ErrorActionPreference = "Stop"

$sdk = $env:ANDROID_SDK_ROOT
if ([string]::IsNullOrWhiteSpace($sdk)) {
    $sdk = $env:ANDROID_HOME
}
if ([string]::IsNullOrWhiteSpace($sdk)) {
    $sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
}

if (-not (Test-Path $sdk)) {
    throw "Android SDK not found at $sdk. Set ANDROID_SDK_ROOT to the installed SDK directory and run again."
}

$sdkForGradle = $sdk -replace "\\", "/"
Set-Content -Path (Join-Path $PSScriptRoot "local.properties") -Value "sdk.dir=$sdkForGradle" -Encoding ASCII
Write-Host "Wrote local.properties with sdk.dir=$sdkForGradle"
