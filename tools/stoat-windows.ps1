param(
    [Parameter(Mandatory)][string]$Meitou,
    [Parameter(Mandatory)][string]$Kenshi,
    [string]$Dotnet = 'dotnet',
    [int]$Port = 42871
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$Meitou = (Resolve-Path -LiteralPath $Meitou).Path
$exe = (Get-Item -LiteralPath (Join-Path $Meitou 'MeitouClient.exe')).FullName
$clientVersion = (Get-Item -LiteralPath (Join-Path $Meitou 'MeitouClient.dll')).VersionInfo.ProductVersion
if ($clientVersion -notmatch '^0\.20\.[01](\+|$)') { throw "The Stoat fixture requires Meitou 0.20.0 or 0.20.1. Found $clientVersion." }
$data = (Resolve-Path -LiteralPath (Join-Path $Kenshi 'data')).Path
$probe = (Resolve-Path -LiteralPath "$PSScriptRoot\Probe\bin\Release\net11.0\Probe.dll").Path
$probeGame = Get-Item -LiteralPath (Join-Path (Split-Path $probe) 'MeitouClient.dll')
if ($probeGame.VersionInfo.ProductVersion -ne $clientVersion) { throw "Rebuild the probe and both mods with -MeitouDir '$Meitou' before launching." }
$release = $clientVersion.Split('+')[0]
$mods = @('MeitouP2P','MeitouP2P-Coop')
foreach ($mod in $mods) {
    $manifest = Get-Content -LiteralPath "$root\artifacts\mods\$mod\meitou.json" -Raw | ConvertFrom-Json
    if ($manifest.gameVersion -ne "=$release") { throw "Rebuild $mod for Meitou $release before launching. Its staged manifest declares '$($manifest.gameVersion)'." }
}
$run = Join-Path "$root\artifacts" ('stoat-live-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$install = New-Item -ItemType Directory -Path "$run\install\mods" | ForEach-Object { $_.Parent.FullName }
New-Item -ItemType Junction -Path "$install\data" -Target $data | Out-Null
foreach ($name in 'resources.cfg','controls.cfg','fcs.def','screens.cfg','settings.cfg','kenshi.cfg','__tutorials.data','currentVersion.txt') {
    $source = Join-Path $Kenshi $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $install }
}
foreach ($mod in $mods) {
    Copy-Item -LiteralPath "$root\artifacts\mods\$mod" -Destination "$install\mods" -Recurse
}
& $Dotnet $probe stoat-start "$install\mods\Stoat Walk"
if ($LASTEXITCODE -ne 0) { throw 'Could not create the trial start.' }
$env:KENSHI_PATH = $install
$env:MEITOU_STOAT_WALK_PORT = "$Port"
$env:DOTNET_PROCESSOR_COUNT = '4'
$env:MEITOU_HEADLESS = $null
foreach ($side in 'host','guest') {
    $profilePath = Join-Path $run $side
    New-Item -ItemType Directory -Path "$profilePath\control","$profilePath\temp" | Out-Null
    @('MeitouP2P.mod','MeitouP2P-Coop.mod','Stoat Walk.mod') | Set-Content -LiteralPath "$profilePath\mods.cfg" -Encoding ascii
    $config = @{version=2; fullScreen=$false; vSync=$false; showNames=$true; splashScreens=$false; tutorials=$false; pauseOnLoad=$false; edgeScroll=$false; uiMode=1; gameplay=@{autoSave=$false}}
    $config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath "$profilePath\meitou.user.json" -Encoding ascii
    $env:MEITOU_MODS_CFG = "$profilePath\mods.cfg"
    $env:MEITOU_STOAT_WALK_ROLE = $side
    $env:TEMP = "$profilePath\temp"
    $env:TMP = $env:TEMP
    $arguments = '--windowed --size 860x600 --new-game "Stoat Scout Walk" --seed 17 --time 15 --fps-limit 24 --no-audio --no-gi --no-precompile --no-foliage --no-objects --shadow-quality 0 --texture-quality 1 --water-reflection 0 --control-dir "' + "$profilePath\control" + '"'
    $client = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $profilePath -RedirectStandardOutput "$profilePath\client.log" -RedirectStandardError "$profilePath\errors.log" -WindowStyle Normal -PassThru
    $client.Id | Set-Content -LiteralPath "$profilePath\client.pid" -Encoding ascii
    Write-Output "$side PID $($client.Id)"
}
$run | Set-Content -LiteralPath "$root\artifacts\stoat-current.txt" -Encoding ascii
Write-Output $run
