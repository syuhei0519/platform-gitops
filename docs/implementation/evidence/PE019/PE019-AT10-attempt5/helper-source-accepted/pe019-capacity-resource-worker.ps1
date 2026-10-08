param([string]$EvidenceDirectory='output/core-platform-v1-phase1-phase2-20261002/evidence')
$ErrorActionPreference='Stop'
$ev=(Resolve-Path $EvidenceDirectory).Path
$window=Get-Content -Raw "$ev/PE019-AT10-window.json"|ConvertFrom-Json
$end=[DateTime]$window.endUtc;$start=[DateTime]$window.startUtc
$dir=Join-Path $ev 'PE019-AT10-resources';$null=New-Item -ItemType Directory -Path $dir -Force
$index=0
$maxCIWorkingSet=0
$stage='dependency-initialization'
try{
 . './output/core-platform-v1-phase1-phase2-20261002/acceptance/initialize-pe019-cim.ps1'
 Initialize-PE019Cim (Join-Path $ev 'PE019-AT10-resource-startup.json')
 do{
  if(Test-Path "$ev/PE019-AT10-abort.json"){
   @{utc=[DateTime]::UtcNow.ToString('o');completed=$false;abortedByParent=$true;samples=$index;maxCombinedCIWorkingSetBytes=$maxCIWorkingSet;thresholdsPassed=$false}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-resource-worker.json"
   return
  }
  if($window.safeSimulatedError -and $index -eq 4){$stage='safe-simulated-error';throw [InvalidOperationException]::new('PE019_SAFE_RESOURCE_FAILURE_SIMULATION')}
  $path=Join-Path $dir ('snapshot-{0:D4}.json' -f $index)
  $stage='snapshot'
  & './output/core-platform-v1-phase1-phase2-20261002/acceptance/read-pe019-resource-snapshot.ps1' -Output ($path+'.partial')
  Move-Item -LiteralPath ($path+'.partial') -Destination $path
  $snapshot=Get-Content -Raw $path|ConvertFrom-Json
  $snapshot|Add-Member -NotePropertyName scheduledUtc -NotePropertyValue $start.AddSeconds(15*$index).ToString('o')
  $snapshot|Add-Member -NotePropertyName sampleIndex -NotePropertyValue $index
  $snapshot|Add-Member -NotePropertyName finalBoundarySample -NotePropertyValue ([DateTime]::UtcNow -ge $end)
  $snapshot|ConvertTo-Json -Depth 22|Set-Content -LiteralPath $path -Encoding utf8
  $stage='frozen-thresholds'
  $ciMemory=(@($snapshot.clusters|ForEach-Object {$_.pods}|Where-Object {$_.namespace -eq 'build'}|ForEach-Object {$_.containers}|ForEach-Object {[long]$_.memory.workingSetBytes})|Measure-Object -Sum).Sum
  $maxCIWorkingSet=[Math]::Max($maxCIWorkingSet,[long]$ciMemory)
  if($ciMemory -gt 2GB){throw 'Combined actual CI job/helper/sidecar exceeded declared two GiB budget'}
  if($snapshot.hostMemoryFreePercent -lt 20 -or $snapshot.WSLMemoryFreePercent -lt 20 -or $snapshot.hostDiskFreePercent -lt 20 -or @($snapshot.clusters|Where-Object {$_.kindDiskFreePercent -lt 20}).Count){throw 'Fixed free-resource threshold failed'}
  foreach($cluster in $snapshot.clusters){foreach($pod in $cluster.states){
   if($pod.reason -eq 'Evicted' -or @($pod.containers|Where-Object {($_.lastReason -eq 'OOMKilled' -and [DateTime]$_.lastFinish -ge $start) -or $_.currentReason -eq 'OOMKilled'}).Count){throw 'New OOM or eviction observed'}
   if($pod.phase -eq 'Pending' -and ([DateTime]$snapshot.utc-[DateTime]$pod.created).TotalSeconds -gt 60){throw 'Pending exceeded sixty seconds'}
  }}
  $index++;$next=$start.AddSeconds(15*$index)
  while([DateTime]::UtcNow -lt $next -and [DateTime]::UtcNow -lt $end){Start-Sleep -Milliseconds 250}
 }while([DateTime]::UtcNow -lt $end -or $index -le [Math]::Floor([int]$window.durationSec/15))
 @{utc=[DateTime]::UtcNow.ToString('o');completed=$true;samples=$index;scope='Windows WSL both kind clusters and every Pod/container';maxCombinedCIWorkingSetBytes=$maxCIWorkingSet;CIMemoryBudgetBytes=2GB;thresholdsPassed=$true}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-resource-worker.json"
}catch{
 $failureMeta=@{utc=[DateTime]::UtcNow.ToString('o');stage=$stage;type=$_.Exception.GetType().FullName;id=$_.FullyQualifiedErrorId;scriptStack=$_.ScriptStackTrace;exitCode=1;safeSimulatedError=($stage -eq 'safe-simulated-error')}
 $inner=$_.Exception
 while($inner){if($inner.Data.Contains('PE019JSONMetadata')){$failureMeta.jsonParser=$inner.Data['PE019JSONMetadata'];break};$inner=$inner.InnerException}
 $failureMeta|ConvertTo-Json -Depth 6|Set-Content "$ev/PE019-AT10-resource-failure-location.json"
 @{utc=[DateTime]::UtcNow.ToString('o');completed=$false;samples=$index;maxCombinedCIWorkingSetBytes=$maxCIWorkingSet;thresholdsPassed=$false;inspectSnapshots=$true}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-resource-worker.json"
 exit 1
}

