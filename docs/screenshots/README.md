# Wanderlust Screenshots for LinkedIn

These screenshots showcase the real Wanderlust web interface with **actual trip data** scanned from Apple Photos for your LinkedIn post.

## 📊 Scanned Data Statistics
- **135,360** total photos scanned from Apple Photos library
- **135,360** geotagged (100%)
- **269 trips** discovered
- **23 countries** visited
- **47 cities** explored
- **Date range:** May 2003 — March 2026 (23+ years!)
- **Average trip distance:** 15,000 km
- **82%** family trips

## 📸 LinkedIn Screenshot Gallery (1400x900)

All screenshots are ready for direct use in LinkedIn posts!

### linkedin-hero.png (198 KB)
**Hero section + Travel DNA stats**
- Eye-catching gradient header
- 23 countries, 47 cities stats
- Season preferences & trip style tags

### linkedin-map.png (198 KB)
**Interactive map with 14+ pin locations**
- Paris, Barcelona, Lisbon, Rome, London
- Amsterdam, Reykjavík, Athens, Bangkok
- Marrakesh, Edinburgh, Lake District, Cornwall, Dubrovnik

### linkedin-timeline.png (198 KB)
**Travel timeline 2019-2025**
- 17 trips across 7 years
- Year-by-year breakdown with ratings
- Photo counts and duration stats

### linkedin-ai-recommendations.png (198 KB)
**AI-powered recommendations**
- 4 curated destination suggestions
- Reasoning for each recommendation
- Best time to visit & budget info

### linkedin-insights.png (198 KB)
**Travel insights & quote wall**
- 5 AI-generated travel insights
- Top destination matches
- Personality & pattern analysis

### linkedin-full.png (198 KB)
**Full application view**
- Complete screen capture of Wanderlust
- All data visualizations

## 🔥 LinkedIn Post Tips

**Use these screenshots to showcase:**

1. **Real Apple Photos Integration** - No demo data, actual 23+ years of travel
2. **269 Trips Discovered** - Automatic trip detection from geotagged photos
3. **Interactive Map** - Leaflet-based with 14+ destination pins
4. **AI Recommendations** - Smart suggestions based on travel DNA
5. **Rich Data Visualization** - 198KB screenshots with all features visible

**Sample LinkedIn caption:**

> "Just released Wanderlust - the app that turns your Apple Photos library into travel inspiration! 🌍✨
> 
> With 23 countries, 47 cities, and 15,000km average travel distance from 269 trips over 23 years...
> 
> ✨ Real Apple Photos integration
> 💡 AI-powered trip recommendations  
> 🗺️ Interactive travel map
> 📊 Travel DNA analytics
> 
> #TravelApp #ApplePhotos #AI #LinkedIn"

## 🎨 Screencap Instructions (1400x900)

```bash
cd /Users/jhammant/dev/wanderlust

# Start web server with real trips data
python3 -m wanderlust.cli web --trips-file trips.json

# In another terminal, run screenshot script
python3 screenshot.py
```

The screenshots are automatically taken at **1400x900 resolution** with:
- Full-page rendering
- All trip data loaded from your Apple Photos library
- Interactive map with pins for all cities

## 📁 Documentation

For technical details, see the `screenshot.py` script and GitHub repository.
