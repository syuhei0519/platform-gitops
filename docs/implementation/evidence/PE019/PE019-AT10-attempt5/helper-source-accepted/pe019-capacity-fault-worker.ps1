param([string]$EvidenceDirectory='output/core-platform-v1-phase1-phase2-20261002/evidence')
$ErrorActionPreference='Stop'
. './output/core-platform-v1-phase1-phase2-20261002/acceptance/private-pe019-kubectl.ps1'
$ev=(Resolve-Path $EvidenceDirectory).Path
$window=Get-Content -Raw "$ev/PE019-AT10-window.json"|ConvertFrom-Json
$start=[DateTime]$window.startUtc;$paused=$false;$scaled=$false
$app='lab-local-otel-collector';$faultStart=$null;$restoreAt=$null;$receivedAt=$null;$exportedAt=$null
function J([string[]]$a){Invoke-PrivateFinalKubectl $a|ConvertFrom-Json}
function Check-Abort {if(Test-Path "$ev/PE019-AT10-abort.json"){throw 'Owning capacity window aborted; restore Collector safely'}}
$frontend=(J @('-n','account','get','pods','-l','app.kubernetes.io/name=frontend','-o','json')).items[0].metadata.name
function Metrics {
 $text=Invoke-PrivateFinalKubectl @('-n','account','exec',$frontend,'--','wget','-q','-O','-','http://otel-collector.observability.svc.cluster.local:8888/metrics')
 return @($text -split "`n"|Where-Object {$_ -match '^otelcol_(receiver_accepted_spans|exporter_(sent_spans|send_failed_spans|enqueue_failed_spans|queue_size|queue_capacity))'})
}
try{
 Metrics|Set-Content "$ev/PE019-AT10-collector-before.txt"
 if($window.purpose -eq 'auxiliary-measurement-preflight'){
  while([DateTime]::UtcNow -lt [DateTime]$window.endUtc){Check-Abort;Start-Sleep -Milliseconds 250}
  Metrics|Set-Content "$ev/PE019-AT10-collector-after-short.txt"
  @{utc=[DateTime]::UtcNow.ToString('o');pass=$true;shortObservationCompleted=$true;faultNotDueWithinShortWindow=$true;actualCollectorFaultAccepted=$false;wholeSpanLossClaim=$false}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-fault-worker.json"
  return
 }
 while([DateTime]::UtcNow -lt $start.AddSeconds(120)){Check-Abort;Start-Sleep -Milliseconds 250}
 $null=Invoke-PrivateFinalKubectl @('-n','argocd','annotate','application',$app,'argocd.argoproj.io/skip-reconcile=true');$paused=$true
 $null=Invoke-PrivateFinalKubectl @('-n','observability','scale','deployment','otel-collector','--replicas=0');$scaled=$true
 $deadline=[DateTime]::UtcNow.AddSeconds(60)
 do{
  $slices=J @('-n','observability','get','endpointslices','-l','kubernetes.io/service-name=otel-collector','-o','json')
  if(@($slices.items|ForEach-Object {$_.endpoints}|Where-Object {$_.conditions.ready -eq $true}).Count -eq 0){$faultStart=[DateTime]::UtcNow;break}
  Start-Sleep -Milliseconds 500
 }while([DateTime]::UtcNow -lt $deadline)
 if(-not $faultStart){throw 'Actual complete Collector unavailability not observed'}
 while([DateTime]::UtcNow -lt $faultStart.AddSeconds(60)){Check-Abort;Start-Sleep -Milliseconds 250}
 $null=Invoke-PrivateFinalKubectl @('-n','observability','scale','deployment','otel-collector','--replicas=1');$scaled=$false;$restoreAt=[DateTime]::UtcNow
 $null=Invoke-PrivateFinalKubectl @('-n','argocd','annotate','application',$app,'argocd.argoproj.io/skip-reconcile-');$paused=$false
 do{
  Check-Abort
  $pods=J @('-n','observability','get','pods','-l','app.kubernetes.io/name=otel-collector','-o','json')
  if(@($pods.items|Where-Object {$_.status.containerStatuses[0].ready}).Count -eq 1){
   $metrics=Metrics
   if($metrics -match '^otelcol_receiver_accepted_spans\{[^\n]*\}\s+([1-9][0-9]*)'){$receivedAt=[DateTime]::UtcNow}
   if($metrics -match '^otelcol_exporter_sent_spans\{[^\n]*\}\s+([1-9][0-9]*)'){$exportedAt=[DateTime]::UtcNow}
   if($receivedAt -and $exportedAt){$metrics|Set-Content "$ev/PE019-AT10-collector-recovery.txt";break}
  }
  Start-Sleep -Seconds 2
 }while([DateTime]::UtcNow -lt $restoreAt.AddSeconds(120))
 if(-not $receivedAt -or -not $exportedAt){throw 'Actual Collector receive/export recovery not observed within120s'}
 @{utc=[DateTime]::UtcNow.ToString('o');pass=$true;faultStart=$faultStart.ToString('o');restoreAt=$restoreAt.ToString('o');receivedAt=$receivedAt.ToString('o');exportedAt=$exportedAt.ToString('o');faultSec=($restoreAt-$faultStart).TotalSeconds;recoverySec=($receivedAt-$restoreAt).TotalSeconds;exportRecoverySec=($exportedAt-$restoreAt).TotalSeconds;wholeSpanLossClaim=$false;NRUIStoredTraceNotYetProven=$true}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-fault-worker.json"
}catch{
 $failureMeta=@{utc=[DateTime]::UtcNow.ToString('o');stage='collector-observation-or-fault';type=$_.Exception.GetType().FullName;id=$_.FullyQualifiedErrorId;scriptStack=$_.ScriptStackTrace;exitCode=1}
 $failureMeta|ConvertTo-Json -Depth 6|Set-Content "$ev/PE019-AT10-fault-failure-location.json"
 @{utc=[DateTime]::UtcNow.ToString('o');pass=$false;faultStart=$faultStart;restoreAt=$restoreAt;receivedAt=$receivedAt;exportedAt=$exportedAt;wholeSpanLossClaim=$false}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-fault-worker.json"
 throw 'Final Collector fault or recovery threshold failed; preserve actual proof'
}finally{
 if($scaled){$null=Invoke-PrivateFinalKubectl @('-n','observability','scale','deployment','otel-collector','--replicas=1')}
 if($paused){$null=Invoke-PrivateFinalKubectl @('-n','argocd','annotate','application',$app,'argocd.argoproj.io/skip-reconcile-')}
}

