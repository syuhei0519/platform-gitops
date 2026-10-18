local hs = {status = "Progressing", message = "Waiting for Rollout status"}
if obj.status == nil then return hs end
local status = obj.status
if status.observedGeneration ~= nil and obj.metadata.generation ~= nil and
   tostring(status.observedGeneration) ~= tostring(obj.metadata.generation) then
  hs.message = "Waiting for observed generation"
  return hs
end
if status.abort == true or status.phase == "Degraded" then
  return {status = "Degraded", message = status.message or "Rollout aborted or degraded"}
end
if status.pauseConditions ~= nil and #status.pauseConditions > 0 then
  return {status = "Suspended", message = "Manual promotion required"}
end
if status.phase == "Healthy" then
  return {status = "Healthy", message = status.message or "Rollout healthy"}
end
hs.message = status.message or "Rollout progressing"
return hs
