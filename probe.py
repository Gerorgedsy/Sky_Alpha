import json, os, urllib.request, urllib.parse, collections
key = os.environ["K"]
def get(**q):
    q.update(api_key=key, limit=50)
    return json.loads(urllib.request.urlopen("https://airlabs.co/api/v9/schedules?" + urllib.parse.urlencode(q), timeout=30).read())
for off in (0, 50, 150, 300):
    r = get(dep_iata="JFK", offset=off)
    rows = r.get("response") or []
    req = r.get("request", {})
    times = [x.get("dep_time") for x in rows]
    print("offset", off, "n", len(rows), "has_more", req.get("has_more"), "keys", sorted(req.keys())[:20])
    print("  first", times[:3], "last", times[-3:], "sorted", times == sorted(times))
    print("  status", collections.Counter(x.get("status") for x in rows), "cs", sum(1 for x in rows if x.get("cs_flight_iata")))
    if r.get("error"): print(r["error"]); break
