#!/usr/bin/env python3
"""Build a stunning Wanderlust demo page with REAL trip data for LinkedIn screenshots"""
import json

with open("real_trips.json") as f:
    data = json.load(f)

trips = data["trips"]
total_trips = data["total_trips"]
total_photos = data["total_photos"]
countries = data["countries"]

# Build map markers
markers_js = "[\n"
for t in trips:
    if t["lat"] and t["lng"]:
        markers_js += '  [{lat}, {lng}, "{city}, {country}", {photos}],\n'.format(**t)
markers_js += "]"

# Country flags
country_flags = {
    "Japan": "\U0001f1ef\U0001f1f5", "United Kingdom": "\U0001f1ec\U0001f1e7", 
    "France": "\U0001f1eb\U0001f1f7", "Spain": "\U0001f1ea\U0001f1f8",
    "Netherlands": "\U0001f1f3\U0001f1f1", "United Arab Emirates": "\U0001f1e6\U0001f1ea",
    "Italy": "\U0001f1ee\U0001f1f9", "Turkey": "\U0001f1f9\U0001f1f7",
    "Germany": "\U0001f1e9\U0001f1ea", "Portugal": "\U0001f1f5\U0001f1f9",
    "Belgium": "\U0001f1e7\U0001f1ea", "Greece": "\U0001f1ec\U0001f1f7",
}

# Season breakdown
seasons = {}
for t in trips:
    s = t.get("season", "unknown")
    seasons[s] = seasons.get(s, 0) + 1

# Build trip cards
trip_cards = ""
for t in trips[:12]:
    flag = country_flags.get(t["country"], "\U0001f30d")
    trip_cards += f'''
    <div class="trip-card">
      <div class="trip-flag">{flag}</div>
      <div class="trip-info">
        <div class="trip-place">{t["city"]}, {t["country"]}</div>
        <div class="trip-meta">{t["start"]} \u00b7 {t["days"]}d \u00b7 {t["photos"]} photos</div>
      </div>
    </div>'''

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wanderlust \u2014 Your Travel DNA</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9/dist/leaflet.css">
<style>
:root {{ --bg:#0a0a0f; --surface:#12121f; --border:#252535; --text:#e8e8f0; --dim:#7a7a8a; --accent:#00cc88; --accent2:#0088ff; --radius:16px; }}
* {{ box-sizing:border-box; margin:0; padding:0; font-family:'Inter',-apple-system,system-ui,sans-serif; }}
body {{ background:var(--bg); color:var(--text); min-height:100vh; }}
.container {{ max-width:1200px; margin:0 auto; padding:32px 24px; }}

.hero {{ background:linear-gradient(135deg,#0d1b2a,#1b2838 50%,#0d2818); border-radius:var(--radius); padding:48px 40px; text-align:center; margin-bottom:32px; border:1px solid var(--border); }}
.hero h1 {{ font-size:2.8em; margin-bottom:12px; font-weight:800; }}
.hero h1 span {{ background:linear-gradient(135deg,var(--accent),var(--accent2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
.hero p {{ color:var(--dim); font-size:1.15em; }}

.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:32px; }}
.stat {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:24px; text-align:center; }}
.stat-num {{ font-size:2.5em; font-weight:800; background:linear-gradient(135deg,var(--accent),var(--accent2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
.stat-label {{ color:var(--dim); font-size:0.85em; margin-top:4px; text-transform:uppercase; letter-spacing:1px; }}

.section {{ margin-bottom:32px; }}
.section h2 {{ font-size:1.5em; margin-bottom:16px; color:var(--text); }}
.section h2 span {{ color:var(--accent); }}

#map {{ height:400px; border-radius:var(--radius); border:1px solid var(--border); margin-bottom:32px; }}

.trips-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.trip-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px; display:flex; align-items:center; gap:12px; transition:all 0.2s; }}
.trip-card:hover {{ border-color:var(--accent); transform:translateY(-2px); }}
.trip-flag {{ font-size:2em; }}
.trip-place {{ font-weight:700; font-size:0.95em; }}
.trip-meta {{ color:var(--dim); font-size:0.8em; margin-top:2px; }}

.dna {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-top:16px; }}
.dna-card {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:20px; }}
.dna-label {{ color:var(--dim); font-size:0.8em; text-transform:uppercase; letter-spacing:1px; }}
.dna-value {{ font-size:1.3em; font-weight:700; margin-top:6px; color:var(--accent); }}

.tags {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
.tag {{ background:rgba(0,204,136,0.1); border:1px solid rgba(0,204,136,0.3); color:var(--accent); padding:6px 14px; border-radius:20px; font-size:0.8em; font-weight:600; }}

.footer {{ text-align:center; padding:32px; color:var(--dim); font-size:0.85em; }}
.footer a {{ color:var(--accent); text-decoration:none; }}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1>\U0001f30d <span>Wanderlust</span></h1>
    <p>Your photos already know where you should go next.</p>
  </div>

  <div class="stats">
    <div class="stat"><div class="stat-num">{countries}</div><div class="stat-label">Countries</div></div>
    <div class="stat"><div class="stat-num">{total_trips}</div><div class="stat-label">Trips Discovered</div></div>
    <div class="stat"><div class="stat-num">{total_photos:,}</div><div class="stat-label">Photos Analysed</div></div>
    <div class="stat"><div class="stat-num">{round(sum(t["days"] for t in trips) / len(trips))}</div><div class="stat-label">Avg Trip (days)</div></div>
  </div>

  <div class="section">
    <h2>\U0001f5fa <span>Your Trip Map</span></h2>
    <div id="map"></div>
  </div>

  <div class="section">
    <h2>\u2708\ufe0f <span>Top Trips</span></h2>
    <div class="trips-grid">{trip_cards}
    </div>
  </div>

  <div class="section">
    <h2>\U0001f9ec <span>Your Travel DNA</span></h2>
    <div class="dna">
      <div class="dna-card">
        <div class="dna-label">Favourite Season</div>
        <div class="dna-value">\u2600\ufe0f Summer</div>
        <div class="tags"><span class="tag">summer: {seasons.get("summer",0)}</span><span class="tag">winter: {seasons.get("winter",0)}</span><span class="tag">spring: {seasons.get("spring",0)}</span><span class="tag">autumn: {seasons.get("autumn",0)}</span></div>
      </div>
      <div class="dna-card">
        <div class="dna-label">Travel Style</div>
        <div class="dna-value">\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466 Family Explorer</div>
        <div class="tags"><span class="tag">Beach</span><span class="tag">Culture</span><span class="tag">Mountains</span><span class="tag">City breaks</span></div>
      </div>
      <div class="dna-card">
        <div class="dna-label">Top Countries</div>
        <div class="dna-value">\U0001f1ec\U0001f1e7 UK \u00b7 \U0001f1ea\U0001f1f8 Spain \u00b7 \U0001f1eb\U0001f1f7 France</div>
        <div class="tags"><span class="tag">\U0001f1ef\U0001f1f5 Japan</span><span class="tag">\U0001f1ee\U0001f1f9 Italy</span><span class="tag">\U0001f1e9\U0001f1ea Germany</span><span class="tag">\U0001f1f5\U0001f1f9 Portugal</span><span class="tag">\U0001f1ec\U0001f1f7 Greece</span></div>
      </div>
    </div>
  </div>

  <div class="footer">
    Scanned from Apple Photos \u00b7 100% local, nothing left this machine \u00b7 
    <a href="https://github.com/jhammant/wanderlust">Open Source on GitHub</a>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9/dist/leaflet.js"></script>
<script>
const map = L.map('map').setView([35, 10], 3);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}@2x.png', {{
  attribution: '\u00a9 OpenStreetMap \u00a9 CARTO'
}}).addTo(map);
const markers = {markers_js};
markers.forEach(m => {{
  L.circleMarker([m[0],m[1]], {{radius:Math.min(Math.sqrt(m[3])/2,15), color:'#00cc88', fillColor:'#00cc88', fillOpacity:0.6}})
   .bindPopup('<b>'+m[2]+'</b><br>'+m[3]+' photos')
   .addTo(map);
}});
</script>
</body>
</html>'''

with open("docs/index.html", "w") as f:
    f.write(html)
print("Built real data demo: docs/index.html")
print(f"{countries} countries, {total_trips} trips, {total_photos:,} photos")
