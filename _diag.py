import json, time
from urllib import request, error
from pathlib import Path
key = (Path.home()/".config"/"wellabs"/"ceda_api_key.txt").read_text().strip()
body = json.dumps({"commodity_id":25,"state_id":24,"from_date":"2024-01-01","to_date":"2024-01-07"}).encode()
req = request.Request("https://api.ceda.ashoka.edu.in/v1/agmarknet/prices", data=body, method="POST",
    headers={"Authorization": f"Bearer {key}", "Content-Type":"application/json"})
try:
    t0=time.time()
    with request.urlopen(req, timeout=90) as r:
        d=json.load(r)
        print(f"OK - {len(d['output']['data'])} rows in {time.time()-t0:.1f}s")
except error.HTTPError as e:
    print("HTTPError", e.code, e.read()[:200])
except Exception as e:
    print("OTHER", type(e).__name__, repr(e)[:200])
