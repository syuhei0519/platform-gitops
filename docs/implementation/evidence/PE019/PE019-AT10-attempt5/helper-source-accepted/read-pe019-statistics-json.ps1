function ConvertFrom-PE019StatisticsJSON([string]$Text){
 $options=[Text.Json.JsonDocumentOptions]::new();$options.MaxDepth=1024
 try{$document=[Text.Json.JsonDocument]::Parse($Text,$options)}catch{
  $inner=$_.Exception
  while($inner.InnerException){$inner=$inner.InnerException}
  $category=if($inner.Message -match 'maximum.*depth|depth.*exceed'){'depth-limit'}elseif($inner.Message -match 'UTF|surrogate|escape'){'encoding-or-escape'}elseif($inner.Message -match 'end of|incomplete|not enough|expected.*end'){'incomplete-or-delimiter'}else{'other-json-syntax'}
  $safe=[InvalidOperationException]::new('Resource JSON parsing failed; safe metadata only',$inner)
  $safe.Data['PE019JSONMetadata']=@{type=$inner.GetType().FullName;category=$category;lineNumber=$inner.LineNumber;bytePositionInLine=$inner.BytePositionInLine;maximumDepth=1024;inputUTF8Bytes=[Text.Encoding]::UTF8.GetByteCount($Text);rawMessageOrValuesRecorded=$false}
  throw $safe
 }
 function ReadElement([Text.Json.JsonElement]$Element){
  switch($Element.ValueKind){
   'Object' {
    $object=[ordered]@{}
    foreach($property in $Element.EnumerateObject()){
     if($object.Contains($property.Name)){throw 'Duplicate statistics property refused'}
     $object[$property.Name]=ReadElement $property.Value
    }
    return $object
   }
   'Array' {
    $items=[Collections.Generic.List[object]]::new()
    foreach($item in $Element.EnumerateArray()){$items.Add((ReadElement $item))}
    return ,$items.ToArray()
   }
   'String' {return $Element.GetString()}
   'Number' {
    $signed=[long]0;$unsigned=[ulong]0
    if($Element.TryGetInt64([ref]$signed)){return $signed}
    if($Element.TryGetUInt64([ref]$unsigned)){return $unsigned}
    return $Element.GetDouble()
   }
   'True' {return $true}
   'False' {return $false}
   'Null' {return $null}
   default {throw 'Unsupported statistics JSON type'}
  }
 }
 try{return ReadElement $document.RootElement}finally{$document.Dispose()}
}
