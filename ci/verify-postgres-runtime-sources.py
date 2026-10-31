#!/usr/bin/env python3
import hashlib
import json
import urllib.parse
import urllib.request
import urllib.error

path = "supply/licenses/postgres/18.6-bookworm/runtime-packages.json"
evidence = json.load(open(path, encoding="utf-8"))
checked = []
failures = []
for source in evidence["sources"]:
    for field, url in source.items():
        if not field.endswith("Url"):
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read()
                if response.status != 200 or not body:
                    raise RuntimeError(f"http-{response.status}")
        except urllib.error.HTTPError as error:
            failures.append((source['sourceName'], field, f"http-{error.code}"))
            continue
        except Exception:
            failures.append((source['sourceName'], field, "transport"))
            continue
        decoded = urllib.parse.unquote(url)
        if field == "sourceUrl" and source["sourceVersion"] not in decoded:
            raise SystemExit(f"source version is not fixed in URL: {source['sourceName']}")
        checked.append({"source": source["sourceName"], "field": field,
                        "sha256": hashlib.sha256(body).hexdigest()})
json.dump({"schemaVersion": 1, "checked": checked},
          open(".security/public/postgres-runtime-sources.json", "w"), indent=2)
print(f"verified {len(checked)} fixed source/license URLs")
for source, field, category in failures:
    print(f"::error file={path}::source-check-failed source={source} field={field} category={category}")
if failures:
    raise SystemExit("fixed source evidence check failed")
