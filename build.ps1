param(
    [Parameter(Mandatory)][string]$MeitouDir,
    [string]$Dotnet = 'dotnet',
    [string]$CoopDir,
    [string]$MaestroCore
)
$ErrorActionPreference = 'Stop'
$MeitouDir = (Resolve-Path -LiteralPath $MeitouDir).Path
$game = Get-Item -LiteralPath (Join-Path $MeitouDir 'MeitouClient.dll')
if ($game.VersionInfo.ProductVersion -notmatch '^0\.20\.[01](\+|$)') { throw "Expected Meitou 0.20.0 or 0.20.1, found $($game.VersionInfo.ProductVersion)." }
$release = $game.VersionInfo.ProductVersion.Split('+')[0]
$opts = @('-c','Release','--nologo','--disable-build-servers','-p:NuGetAudit=false',"-p:MeitouDir=$MeitouDir")
function Build-Project([string]$Project, [string[]]$Extra = @()) {
    & $Dotnet build $Project @opts @Extra
    if ($LASTEXITCODE) { throw "Build failed for $Project" }
}
function Pack-Mod([string]$Source, [string]$Name, [string]$Assembly) {
    $manifest = & $Dotnet $probe target-manifest (Join-Path $Source 'pack/meitou.json') $game.VersionInfo.ProductVersion
    if ($LASTEXITCODE) { throw "Cannot package $Name for Meitou $release." }
    $out = Join-Path $PSScriptRoot "artifacts/mods/$Name"
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -Path (Join-Path $Source 'pack/*') -Exclude 'meitou.json' -Destination $out
    Copy-Item -LiteralPath (Join-Path $Source "src/bin/Release/net11.0/$Assembly.dll") -Destination $out
    [IO.File]::WriteAllLines((Join-Path $out 'meitou.json'), [string[]]$manifest)
    Write-Host "Packaged $out for Meitou $release"
}
$probe = Join-Path $PSScriptRoot 'tools/Probe/bin/Release/net11.0/Probe.dll'
$extra = @()
if ($MaestroCore) { $extra += "-p:MaestroCore=$((Resolve-Path -LiteralPath $MaestroCore).Path)" }
Build-Project (Join-Path $PSScriptRoot 'tools/Probe/Probe.csproj') $extra
Build-Project (Join-Path $PSScriptRoot 'src/MeitouP2P.csproj')
Pack-Mod $PSScriptRoot 'MeitouP2P' 'MeitouP2P'
if ($CoopDir) {
    $CoopDir = (Resolve-Path -LiteralPath $CoopDir).Path
    Build-Project (Join-Path $CoopDir 'src/MeitouP2P.Coop.csproj') @("-p:ConnectorProject=$PSScriptRoot/src/MeitouP2P.csproj")
    Pack-Mod $CoopDir 'MeitouP2P-Coop' 'MeitouP2P.Coop'
}
