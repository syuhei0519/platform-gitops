param([string]$ProfilePath='output/core-platform-v1-phase1-phase2-20261002/evidence/PE019-final-profile.json',[string]$Output='output/core-platform-v1-phase1-phase2-20261002/evidence/PE019-resource-preflight.json')
$ErrorActionPreference='Stop'
$ev='./output/core-platform-v1-phase1-phase2-20261002/evidence/'
$profile=Get-Content $ProfilePath -Raw|ConvertFrom-Json
$short=($profile.purpose -eq 'auxiliary-measurement-preflight' -and $profile.durationSec -in @(60,120))
if((-not $short -and $profile.durationSec -ne 900) -or $profile.requestsPerSecond -ne 1 -or $profile.freeMemoryMinPercent -ne 20 -or $profile.additionalCostCapJPY -ne 0){throw 'Predeclared final profile must not be relaxed'}
$os=Get-CimInstance Win32_OperatingSystem
$mem=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$linux=docker exec platform-lab-control-plane cat /proc/meminfo
if($LASTEXITCODE -ne 0){throw 'WSL/kind resource observation failed'}
$lm=@{};foreach($line in $linux){if($line -match '^(MemTotal|MemAvailable):\s+(\d+) kB'){$lm[$matches[1]]=[long]$matches[2]}}
if(-not $lm.MemTotal -or -not $lm.MemAvailable){throw 'Actual memory measurements required'}
$hostFree=100*[double]$mem.AvailableKBytes/[double]$os.TotalVisibleMemorySize
$linuxFree=100*[double]$lm.MemAvailable/[double]$lm.MemTotal
$diskFree=100*[double]$disk.FreeSpace/[double]$disk.Size
$oneDrive=@(Get-Process OneDrive -ErrorAction SilentlyContinue|ForEach-Object {[long]$_.WorkingSet64})
$p=@{unit='PE019';utc=[DateTime]::UtcNow.ToString('o');hostTotalKiB=$os.TotalVisibleMemorySize;hostAvailableKiB=$mem.AvailableKBytes;hostFreePercent=$hostFree;minimumPercent=$profile.freeMemoryMinPercent;hostRequiredAvailableGiB=.2*[double]$os.TotalVisibleMemorySize/1MB;WSLAvailableKiB=$lm.MemAvailable;WSLTotalKiB=$lm.MemTotal;WSLFreePercent=$linuxFree;windowsFreeDiskPercent=$diskFree;OneDriveWorkingSetGiB=($oneDrive|Measure-Object -Sum).Sum/1GB;readyToStart=($hostFree -ge 20 -and $linuxFree -ge 20 -and $diskFree -ge 20);newClusterCreated=(Test-Path ($ev+'PE019-kind-created.json'));loadStarted=[bool]$profile.testStarted;existingClusterPreserved=$true;unrelatedProcessesStopped=$false;secretValuesRecorded=$false;all19Complete=$false}
$p|ConvertTo-Json|Set-Content $Output -Encoding utf8
$p|Select-Object readyToStart,hostFreePercent,hostRequiredAvailableGiB,WSLFreePercent,windowsFreeDiskPercent,OneDriveWorkingSetGiB|ConvertTo-Json -Compress
