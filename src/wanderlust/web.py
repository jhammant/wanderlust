"""
Wanderlust Web — Interactive trip discovery and recommendation UI.

A lightweight Flask app with:
- Trip history dashboard + map
- Trip review/rating (what did you enjoy?)
- Conversational recommendation engine
- Exclusion zones (don't suggest places you've been or nearby)
"""

import json
import os
import re
import time
from pathlib import Path
from datetime import datetime

# Fix SSL cert verification on macOS with Homebrew Python
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from flask import Flask, render_template_string, jsonify, request, send_from_directory

from .clusterer import Trip, haversine_km
from .profiler import build_profile, TravelProfile
from .recommender import build_recommendation_prompt


app = Flask(__name__)

# In-memory state (loaded from JSON files)
STATE = {
    "trips": [],
    "reviews": {},       # trip_id -> {rating, liked, disliked, notes}
    "preferences": {},   # Accumulated from reviews
    "profile": None,
    "conversations": [], # Chat history for recommendation session
}

DATA_DIR = Path.home() / ".wanderlust"
TRIPS_FILE = DATA_DIR / "trips.json"
REVIEWS_FILE = DATA_DIR / "reviews.json"
PLANNED_TRIPS_FILE = DATA_DIR / "planned_trips.json"
AVOID_PLACES_FILE = DATA_DIR / "avoid_places.json"


def load_state():
    """Load trips and reviews from disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if TRIPS_FILE.exists():
        data = json.loads(TRIPS_FILE.read_text())
        STATE["trips"] = _deserialize_trips(data)
        STATE["profile"] = build_profile(STATE["trips"])

    if REVIEWS_FILE.exists():
        STATE["reviews"] = json.loads(REVIEWS_FILE.read_text())


def save_reviews():
    """Persist reviews to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REVIEWS_FILE.write_text(json.dumps(STATE["reviews"], indent=2))


def _deserialize_trips(data):
    trips = []
    for d in data:
        trip = Trip(
            id=d["id"],
            start_date=datetime.fromisoformat(d["start_date"]),
            end_date=datetime.fromisoformat(d["end_date"]),
            center_lat=d["center"][0],
            center_lon=d["center"][1],
            country=d.get("country"),
            city=d.get("city"),
            place_name=d.get("place_name"),
            people=d.get("people", []),
            people_counts=d.get("people_counts", {}),
            is_family_trip=d.get("is_family_trip", False),
            trip_type=d.get("trip_type", "stay"),
            spread_km=d.get("spread_km", 0.0),
            stops=d.get("stops", []),
            photo_count=d.get("photo_count", 0),
            favorite_count=d.get("favorite_count", 0),
        )
        trips.append(trip)
    return trips


def get_exclusion_zones(radius_km=100):
    """Build exclusion zones from trip history — don't suggest these areas."""
    zones = []
    for trip in STATE["trips"]:
        zones.append({
            "lat": trip.center_lat,
            "lon": trip.center_lon,
            "radius_km": radius_km,
            "name": trip.place_name or f"({trip.center_lat:.1f}, {trip.center_lon:.1f})",
            "last_visited": trip.end_date.strftime("%b %Y"),
        })
    return zones


def is_in_exclusion_zone(lat, lon, radius_km=100):
    """Check if a coordinate is too close to somewhere already visited."""
    for trip in STATE["trips"]:
        if haversine_km(lat, lon, trip.center_lat, trip.center_lon) < radius_km:
            return True, trip.place_name
    return False, None


def build_review_questions(trip):
    """Generate review questions for a specific trip."""
    questions = [
        {
            "id": "overall",
            "text": f"How would you rate your trip to {trip.place_name}?",
            "type": "rating",  # 1-5 stars
        },
        {
            "id": "highlights",
            "text": "What were the highlights?",
            "type": "multi_select",
            "options": [
                "🏖️ Beach / Relaxation",
                "🏛️ Culture / History",
                "🍽️ Food / Restaurants",
                "🏔️ Nature / Outdoors",
                "🎢 Activities / Adventure",
                "👨‍👩‍👧‍👦 Family time",
                "🌆 City exploring",
                "🎵 Nightlife / Entertainment",
                "🛍️ Shopping",
                "☀️ Weather",
            ],
        },
        {
            "id": "would_return",
            "text": "Would you go back?",
            "type": "select",
            "options": ["Definitely", "Maybe", "Probably not", "No way"],
        },
        {
            "id": "best_for",
            "text": "This trip was best for...",
            "type": "multi_select",
            "options": [
                "Couples getaway",
                "Family with young kids",
                "Family with older kids",
                "Adventure seekers",
                "Relaxation",
                "Foodies",
                "Culture vultures",
                "Budget travellers",
            ],
        },
        {
            "id": "notes",
            "text": "Anything else you'd want to remember?",
            "type": "text",
        },
    ]
    return questions


def build_chat_context():
    """Build context for the recommendation chat from trips + reviews."""
    lines = []

    # Trip history
    lines.append("## Travel History")
    for trip in STATE["trips"]:
        review = STATE["reviews"].get(str(trip.id), {})
        line = f"- {trip.place_name}: {trip.start_date.strftime('%b %Y')}, {trip.duration_days} days"
        if trip.people:
            line += f" (with {', '.join(trip.people[:3])})"
        if review.get("overall"):
            line += f" — rated {review['overall']}/5"
        if review.get("highlights"):
            line += f", enjoyed: {', '.join(review['highlights'])}"
        if review.get("would_return"):
            line += f", would return: {review['would_return']}"
        lines.append(line)

    # Exclusion zones
    lines.append("\n## Places NOT to suggest (already visited or nearby)")
    zones = get_exclusion_zones()
    for z in zones:
        lines.append(f"- {z['name']} (last: {z['last_visited']}) — exclude within {z['radius_km']}km")

    # Derived preferences from reviews
    all_highlights = []
    for review in STATE["reviews"].values():
        all_highlights.extend(review.get("highlights", []))
    if all_highlights:
        from collections import Counter
        top = Counter(all_highlights).most_common(5)
        lines.append("\n## Top enjoyment factors")
        for item, count in top:
            lines.append(f"- {item} ({count} trips)")

    # Profile
    if STATE["profile"]:
        p = STATE["profile"]
        lines.append(f"\n## Travel DNA")
        lines.append(f"- Avg trip: {p.avg_trip_days:.0f} days")
        lines.append(f"- Preferred seasons: {', '.join(p.preferred_seasons)}")
        lines.append(f"- Style: {p.preferences.get('style', '?')}")
        lines.append(f"- Range: {p.avg_distance_km:.0f}km avg, {p.max_distance_km:.0f}km max")
        lines.append(f"- Countries: {', '.join(p.countries_visited)}")

    # Avoid list
    if AVOID_PLACES_FILE.exists():
        try:
            avoid_list = json.loads(AVOID_PLACES_FILE.read_text())
            if avoid_list:
                lines.append("\n## Places to NEVER suggest")
                for place in avoid_list:
                    lines.append(f"- {place}")
        except (json.JSONDecodeError, TypeError):
            pass

    return "\n".join(lines)


# === ROUTES ===

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, trips=STATE["trips"])


@app.route("/api/trips")
def api_trips():
    trips = []
    for t in STATE["trips"]:
        review = STATE["reviews"].get(str(t.id), {})
        trips.append({
            "id": t.id,
            "name": t.place_name,
            "city": t.city,
            "country": t.country,
            "lat": t.center_lat,
            "lon": t.center_lon,
            "start": t.start_date.isoformat(),
            "end": t.end_date.isoformat(),
            "days": t.duration_days,
            "photos": t.photo_count,
            "people": t.people,
            "people_counts": getattr(t, 'people_counts', {}),
            "family": t.is_family_trip,
            "trip_type": getattr(t, 'trip_type', 'stay'),
            "spread_km": getattr(t, 'spread_km', 0),
            "stops": getattr(t, 'stops', []),
            "season": t.season,
            "rating": review.get("overall"),
            "reviewed": bool(review),
        })
    return jsonify(trips)


@app.route("/api/trip/<int:trip_id>/questions")
def api_trip_questions(trip_id):
    trip = next((t for t in STATE["trips"] if t.id == trip_id), None)
    if not trip:
        return jsonify({"error": "Trip not found"}), 404
    return jsonify({
        "trip": {"id": trip.id, "name": trip.place_name},
        "questions": build_review_questions(trip),
        "existing_review": STATE["reviews"].get(str(trip_id), {}),
    })


@app.route("/api/photo/<uuid>/open")
def api_photo_open(uuid):
    """Open a photo in Apple Photos app."""
    import subprocess
    # Ensure Photos is running
    subprocess.Popen(['open', '-a', 'Photos'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import time
    time.sleep(1)
    # Spotlight the photo
    proc = subprocess.Popen([
        'osascript', '-e',
        f'tell application "Photos" to spotlight media item id "{uuid}/L0/001"'
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait(timeout=10)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        proc.kill()
        return jsonify({"ok": False, "error": "timeout"}), 504


@app.route("/api/trip/<int:trip_id>/review", methods=["POST"])
def api_save_review(trip_id):
    data = request.json
    STATE["reviews"][str(trip_id)] = data
    save_reviews()
    return jsonify({"ok": True})


@app.route("/api/exclusions")
def api_exclusions():
    return jsonify(get_exclusion_zones())


@app.route("/api/photo/<uuid>")
def api_photo(uuid):
    """Serve a photo thumbnail by UUID, pulling from iCloud if needed."""
    from flask import send_file

    thumb_dir = Path.home() / ".wanderlust" / "thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{uuid}.jpg"

    # Return cached thumbnail if exists
    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype="image/jpeg")

    # Try PyObjC Photos framework (handles iCloud transparently)
    try:
        from Photos import PHAsset, PHImageManager, PHImageRequestOptions, PHFetchOptions, PHImageContentModeAspectFill
        from AppKit import NSSize, NSBitmapImageRep, NSJPEGFileType
        from Foundation import NSPredicate

        pred = NSPredicate.predicateWithFormat_('localIdentifier BEGINSWITH %@', uuid)
        fetch_opts = PHFetchOptions.alloc().init()
        fetch_opts.setPredicate_(pred)
        result = PHAsset.fetchAssetsWithOptions_(fetch_opts)

        if result.count() > 0:
            asset = result.objectAtIndex_(0)
            manager = PHImageManager.defaultManager()
            req_opts = PHImageRequestOptions.alloc().init()
            req_opts.setSynchronous_(True)
            req_opts.setNetworkAccessAllowed_(True)
            req_opts.setDeliveryMode_(1)  # Fast

            img_holder = [None]
            def handler(img, info):
                img_holder[0] = img

            manager.requestImageForAsset_targetSize_contentMode_options_resultHandler_(
                asset, NSSize(300, 300), PHImageContentModeAspectFill, req_opts, handler
            )

            img = img_holder[0]
            if img:
                rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
                jpeg = rep.representationUsingType_properties_(NSJPEGFileType, {})
                with open(str(thumb_path), 'wb') as f:
                    f.write(jpeg)
                return send_file(str(thumb_path), mimetype="image/jpeg")
    except Exception:
        pass

    # Fallback: try local originals with sips
    import subprocess
    library_path = os.environ.get("PHOTOS_LIBRARY",
        str(Path.home() / "Pictures" / "Photos Library.photoslibrary"))
    prefix = uuid[0].upper()
    originals_dir = Path(library_path) / "originals" / prefix

    photo_path = None
    if originals_dir.exists():
        for f in originals_dir.iterdir():
            if f.stem == uuid:
                photo_path = f
                break

    if photo_path and photo_path.exists():
        try:
            subprocess.run(
                ["sips", "-z", "300", "300", "-s", "format", "jpeg",
                 str(photo_path), "--out", str(thumb_path)],
                capture_output=True, timeout=10
            )
            if thumb_path.exists():
                return send_file(str(thumb_path), mimetype="image/jpeg")
        except Exception:
            pass

    return "", 204


@app.route("/api/trip/<int:trip_id>/photos")
def api_trip_photos(trip_id):
    """Return photo UUIDs for a trip by querying the Photos database directly."""
    import sqlite3
    from .scanner import find_photos_db, core_data_to_datetime, CORE_DATA_EPOCH

    trip = next((t for t in STATE["trips"] if t.id == trip_id), None)
    if not trip:
        return jsonify([])

    try:
        db_path = find_photos_db()
    except FileNotFoundError:
        return jsonify([])

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Convert trip dates to Core Data timestamps
    from datetime import timedelta
    start_ts = (trip.start_date - CORE_DATA_EPOCH).total_seconds()
    end_ts = (trip.end_date - CORE_DATA_EPOCH + timedelta(days=1)).total_seconds()

    # Query photos in date range, near trip center (within ~50km ≈ 0.5 degrees)
    rows = conn.execute("""
        SELECT ZUUID, ZDATECREATED, ZFAVORITE, ZLATITUDE, ZLONGITUDE
        FROM ZASSET
        WHERE ZDATECREATED BETWEEN ? AND ?
          AND ZLATITUDE BETWEEN ? AND ?
          AND ZLONGITUDE BETWEEN ? AND ?
          AND ZTRASHEDSTATE = 0
        ORDER BY ZFAVORITE DESC, ZDATECREATED
    """, (
        start_ts, end_ts,
        trip.center_lat - 2, trip.center_lat + 2,
        trip.center_lon - 2, trip.center_lon + 2,
    )).fetchall()
    conn.close()

    # Sample up to 20 photos (favorites first)
    selected = rows[:20]

    return jsonify([{
        "uuid": r["ZUUID"],
        "timestamp": core_data_to_datetime(r["ZDATECREATED"]).isoformat() if r["ZDATECREATED"] else None,
        "favorite": bool(r["ZFAVORITE"]),
        "faces": [],
    } for r in selected])


@app.route("/api/trip/<int:trip_id>/detail")
def api_trip_detail(trip_id):
    """Return rich trip detail for the drill-down panel."""
    trip = next((t for t in STATE["trips"] if t.id == trip_id), None)
    if not trip:
        return jsonify({"error": "Trip not found"}), 404

    review = STATE["reviews"].get(str(trip_id), {})

    # Day-by-day breakdown
    from collections import defaultdict
    by_day = defaultdict(int)
    if hasattr(trip, 'photos') and trip.photos:
        for p in trip.photos:
            by_day[p.timestamp.strftime("%Y-%m-%d")] += 1

    days = [{"date": d, "photos": c} for d, c in sorted(by_day.items())]

    return jsonify({
        "id": trip.id,
        "name": trip.place_name,
        "city": trip.city,
        "country": trip.country,
        "lat": trip.center_lat,
        "lon": trip.center_lon,
        "start": trip.start_date.isoformat(),
        "end": trip.end_date.isoformat(),
        "days": trip.duration_days,
        "photos": trip.photo_count,
        "favorites": trip.favorite_count,
        "people": trip.people,
        "people_counts": getattr(trip, 'people_counts', {}),
        "family": trip.is_family_trip,
        "trip_type": getattr(trip, 'trip_type', 'stay'),
        "spread_km": getattr(trip, 'spread_km', 0),
        "stops": getattr(trip, 'stops', []),
        "season": trip.season,
        "rating": review.get("overall"),
        "review": review,
        "daily_breakdown": days,
    })


@app.route("/api/trip/<int:trip_id>/enrich", methods=["POST"])
def api_enrich_trip(trip_id):
    """Generate an AI narrative for a trip from its photo metadata."""
    from .enricher import enrich_trip
    trip = next((t for t in STATE["trips"] if t.id == trip_id), None)
    if not trip:
        return jsonify({"error": "Trip not found"}), 404

    provider = request.json.get("provider", "ollama") if request.json else "ollama"
    model = request.json.get("model") if request.json else None

    narrative = enrich_trip(trip, provider=provider, model=model)
    return jsonify({"narrative": narrative, "trip_id": trip_id})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat endpoint for recommendation conversation."""
    data = request.json
    user_message = data.get("message", "")
    history = data.get("history", [])

    context = build_chat_context()

    # Build the system prompt
    system = f"""You are Wanderlust, a personal travel advisor. You have access to the user's
complete travel history from their photo library, including where they've been,
who they travelled with, what they enjoyed, and their travel patterns.

Your job: Help them find their next perfect holiday through conversation.

RULES:
1. NEVER suggest places they've already been (check the exclusion list)
2. NEVER suggest places within 100km of somewhere they've visited
3. Reference their actual past trips when explaining recommendations
4. Ask clarifying questions before jumping to suggestions
5. Be specific — name actual towns/regions, not just countries
6. Consider their kids (Clara age 10, Zoe age 7, Ethan age 4) for family trips
7. Factor in their travel patterns (seasons, duration, style)
8. When suggesting destinations, always include: distance from London, best season, which past trip it's similar to, and why it works for Clara (10), Zoe (7), Ethan (4).

{context}
"""

    # For now, return the prompt for the LLM to process
    # In production, this would call OpenAI/Ollama
    messages = [{"role": "system", "content": system}]
    for msg in history:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

    # Try OpenAI first, then Ollama, then return prompt for manual use
    response_text = _get_ai_response(messages)

    return jsonify({
        "response": response_text,
        "exclusion_count": len(get_exclusion_zones()),
    })


def _get_ai_response(messages):
    """Try available AI providers in order."""
    # Try OpenRouter first (cheapest, multi-model)
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if or_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=or_key, base_url="https://openrouter.ai/api/v1")
            resp = client.chat.completions.create(
                model="anthropic/claude-sonnet-4",
                messages=messages,
                temperature=0.8,
                max_tokens=1500,
            )
            return resp.choices[0].message.content
        except Exception as e:
            pass

    # Try OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.8,
                max_tokens=1500,
            )
            return resp.choices[0].message.content
        except Exception as e:
            pass

    # Try Ollama
    try:
        import requests
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={"model": "qwen2.5:14b", "messages": messages, "stream": False},
            timeout=120,
        )
        if resp.ok:
            return resp.json()["message"]["content"]
    except Exception:
        pass

    return ("I'm not connected to an AI provider right now. "
            "Set OPENROUTER_API_KEY, OPENAI_API_KEY, or start Ollama to get personalised recommendations. "
            "In the meantime, review your trips and rate them — that helps me learn your preferences!")


@app.route("/api/photo-locations")
def api_photo_locations():
    """Return all photo GPS points for heatmap display."""
    points = []
    for trip in STATE["trips"]:
        # If we have individual photo locations, use those
        if hasattr(trip, 'locations') and trip.locations:
            for lat, lon in trip.locations:
                points.append([lat, lon, 0.5])  # lat, lon, intensity
        else:
            # Use trip center with photo count as weight
            intensity = min(1.0, trip.photo_count / 100)
            points.append([trip.center_lat, trip.center_lon, intensity])
    return jsonify(points)


@app.route("/api/frequency")
def api_frequency():
    """Analyse visit frequency by region — how often we go back."""
    from collections import defaultdict

    # Group trips by region (country + rough area)
    regions = defaultdict(list)
    for trip in STATE["trips"]:
        # Group by country, then by proximity (within 150km = same region)
        key = trip.country or "Unknown"
        matched = False
        for region_key, region_trips in regions.items():
            if region_key.startswith(key):
                # Check if close to existing trips in this region
                for rt in region_trips:
                    if haversine_km(trip.center_lat, trip.center_lon, rt["lat"], rt["lon"]) < 150:
                        region_trips.append({
                            "name": trip.place_name, "lat": trip.center_lat, "lon": trip.center_lon,
                            "year": trip.start_date.year, "month": trip.start_date.month,
                            "days": trip.duration_days, "photos": trip.photo_count,
                        })
                        matched = True
                        break
                if matched:
                    break

        if not matched:
            region_name = f"{key}: {trip.city or trip.place_name or 'Unknown'}"
            regions[region_name].append({
                "name": trip.place_name, "lat": trip.center_lat, "lon": trip.center_lon,
                "year": trip.start_date.year, "month": trip.start_date.month,
                "days": trip.duration_days, "photos": trip.photo_count,
            })

    # Build frequency analysis
    result = []
    for region, visits in sorted(regions.items(), key=lambda x: -len(x[1])):
        years = sorted(set(v["year"] for v in visits))
        total_days = sum(v["days"] for v in visits)
        total_photos = sum(v["photos"] for v in visits)
        avg_lat = sum(v["lat"] for v in visits) / len(visits)
        avg_lon = sum(v["lon"] for v in visits) / len(visits)

        year_range = max(years) - min(years) + 1 if years else 1
        visits_per_year = len(visits) / year_range if year_range > 0 else len(visits)

        result.append({
            "region": region,
            "visit_count": len(visits),
            "visits_per_year": round(visits_per_year, 1),
            "years": years,
            "total_days": total_days,
            "total_photos": total_photos,
            "lat": avg_lat,
            "lon": avg_lon,
            "visits": visits,
            "frequency_label": (
                "Annual favourite" if visits_per_year >= 0.8 else
                "Regular" if visits_per_year >= 0.4 else
                "Occasional" if len(visits) >= 2 else
                "One-off"
            ),
        })

    return jsonify(result)


@app.route("/api/timeline")
def api_timeline():
    """Return trips as timeline data grouped by year."""
    from collections import defaultdict
    by_year = defaultdict(list)
    for trip in STATE["trips"]:
        by_year[trip.start_date.year].append({
            "name": trip.place_name,
            "month": trip.start_date.month,
            "days": trip.duration_days,
            "family": trip.is_family_trip,
            "country": trip.country,
        })

    return jsonify({
        year: sorted(trips, key=lambda t: t["month"])
        for year, trips in sorted(by_year.items())
    })


@app.route("/api/profile")
def api_profile():
    if not STATE["profile"]:
        return jsonify({"error": "No trips loaded"}), 404
    p = STATE["profile"]
    return jsonify({
        "total_trips": p.total_trips,
        "total_days": p.total_days_away,
        "total_photos": p.total_photos,
        "date_range": p.date_range,
        "avg_trip_days": round(p.avg_trip_days, 1),
        "preferred_seasons": p.preferred_seasons,
        "countries": p.countries_visited,
        "avg_distance_km": round(p.avg_distance_km),
        "max_distance_km": round(p.max_distance_km),
        "farthest": p.farthest_trip,
        "family_pct": round(p.family_trip_pct),
        "most_travelled_with": p.most_travelled_with,
        "preferences": p.preferences,
    })


# === ROAD TRIP PLANNER ENDPOINTS ===


@app.route("/api/roadtrip/generate", methods=["POST"])
def api_roadtrip_generate():
    """Generate an AI-powered road trip plan for a specific country."""
    data = request.json
    country = data.get("country", "")
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    who = data.get("who", "")
    interests = data.get("interests", "")
    budget = data.get("budget", "")
    avoid_places = data.get("avoid_places", [])
    notes = data.get("notes", "")

    context = build_chat_context()

    # Extract past trip stops in the target country with coordinates
    country_trips = []
    for trip in STATE["trips"]:
        if trip.country and trip.country.lower() == country.lower():
            trip_info = {
                "place_name": trip.place_name,
                "lat": trip.center_lat,
                "lon": trip.center_lon,
                "dates": f"{trip.start_date.strftime('%b %Y')} - {trip.end_date.strftime('%b %Y')}",
                "days": trip.duration_days,
            }
            # Include stops if available
            if hasattr(trip, 'stops') and trip.stops:
                trip_info["stops"] = trip.stops
            country_trips.append(trip_info)

            # Also include review highlights if available
            review = STATE["reviews"].get(str(trip.id), {})
            if review.get("highlights"):
                trip_info["highlights"] = review["highlights"]
            if review.get("overall"):
                trip_info["rating"] = review["overall"]

    past_visits_section = ""
    if country_trips:
        past_visits_section = f"\n\nPAST VISITS TO {country.upper()}:\n"
        for ct in country_trips:
            past_visits_section += f"- {ct['place_name']} ({ct['dates']}, {ct['days']} days) at ({ct['lat']:.3f}, {ct['lon']:.3f})"
            if ct.get("highlights"):
                past_visits_section += f" — enjoyed: {', '.join(ct['highlights'])}"
            if ct.get("rating"):
                past_visits_section += f" — rated {ct['rating']}/5"
            past_visits_section += "\n"
            if ct.get("stops"):
                for stop in ct["stops"]:
                    stop_name = stop.get("name", stop.get("place_name", "Unknown"))
                    stop_lat = stop.get("lat", stop.get("center_lat", 0))
                    stop_lon = stop.get("lon", stop.get("center_lon", 0))
                    past_visits_section += f"  - Stop: {stop_name} at ({stop_lat}, {stop_lon})\n"

    avoid_section = ""
    if avoid_places:
        avoid_section = "\n\nADDITIONAL PLACES TO AVOID:\n"
        for place in avoid_places:
            avoid_section += f"- {place}\n"

    system_prompt = f"""You are Wanderlust Road Trip Planner, an expert at creating family road trip itineraries.

FAMILY:
- Jon (dad), Anne (mum), Clara (10), Zoe (7), Ethan (4)
- Ethan is 4 years old — needs nap stops, shorter driving legs
- Keep daily driving to 4-5 hours MAX (with young kids, less is better)
- Include kid-friendly activities at every stop

{context}
{past_visits_section}
{avoid_section}

CRITICAL RULES:
- Avoid suggesting stops within 30km of these visited locations listed above
- Reference their travel DNA from the profile when choosing destinations
- Reference review highlights for trips in {country} when explaining choices
- Give realistic driving times accounting for breaks with young children
- Return ONLY valid JSON — no markdown fences, no explanation text before or after
- The JSON must match this exact schema:

{{
  "title": "string — catchy trip name",
  "summary": "string — 2-3 sentence overview",
  "why_these_places": "string — explain the route logic",
  "total_driving_km": number,
  "stops": [
    {{
      "day": number,
      "days_here": number,
      "name": "string — specific town/village name",
      "lat": number,
      "lon": number,
      "activities": ["string array of specific activities"],
      "why_special": "string — why this place works for the family",
      "accommodation_type": "string — eg Gite, Airbnb, Hotel, Camping",
      "drive_from_previous_km": number,
      "drive_from_previous_hours": number,
      "similar_to_past": "string — reference a past trip if relevant"
    }}
  ],
  "exclusion_reasoning": "string — explain what was avoided and why"
}}"""

    user_msg = f"Plan a road trip to {country} from {start_date} to {end_date}."
    if who:
        user_msg += f" Travelling with: {who}."
    if interests:
        user_msg += f" Interests: {interests}."
    if budget:
        user_msg += f" Budget: {budget}."
    if notes:
        user_msg += f" Additional notes: {notes}."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    response_text = _get_ai_response(messages)

    # Try to parse JSON response
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        # Try extracting JSON between outermost { and }
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return jsonify({"error": "Failed to parse AI response", "raw": response_text}), 500
        else:
            return jsonify({"error": "No JSON found in AI response", "raw": response_text}), 500

    return jsonify(result)


@app.route("/api/roadtrip/save", methods=["POST"])
def api_roadtrip_save():
    """Save a planned road trip."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = request.json

    # Add ID and creation date
    data["id"] = f"plan_{int(time.time() * 1000)}"
    data["created"] = datetime.now().isoformat()

    # Load existing plans
    plans = []
    if PLANNED_TRIPS_FILE.exists():
        try:
            plans = json.loads(PLANNED_TRIPS_FILE.read_text())
        except (json.JSONDecodeError, TypeError):
            plans = []

    plans.append(data)
    PLANNED_TRIPS_FILE.write_text(json.dumps(plans, indent=2))

    return jsonify({"ok": True, "id": data["id"]})


@app.route("/api/roadtrip/plans")
def api_roadtrip_plans():
    """Return all saved road trip plans."""
    if PLANNED_TRIPS_FILE.exists():
        try:
            plans = json.loads(PLANNED_TRIPS_FILE.read_text())
            return jsonify(plans)
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify([])


@app.route("/api/roadtrip/plan/<plan_id>", methods=["DELETE"])
def api_roadtrip_delete(plan_id):
    """Delete a saved road trip plan by ID."""
    if not PLANNED_TRIPS_FILE.exists():
        return jsonify({"error": "No plans found"}), 404

    try:
        plans = json.loads(PLANNED_TRIPS_FILE.read_text())
    except (json.JSONDecodeError, TypeError):
        return jsonify({"error": "Failed to read plans"}), 500

    original_count = len(plans)
    plans = [p for p in plans if p.get("id") != plan_id]

    if len(plans) == original_count:
        return jsonify({"error": "Plan not found"}), 404

    PLANNED_TRIPS_FILE.write_text(json.dumps(plans, indent=2))
    return jsonify({"ok": True})


@app.route("/api/avoid-list", methods=["GET"])
def api_avoid_list_get():
    """Return the avoid-places list."""
    if AVOID_PLACES_FILE.exists():
        try:
            avoid_list = json.loads(AVOID_PLACES_FILE.read_text())
            return jsonify(avoid_list)
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify([])


@app.route("/api/avoid-list", methods=["POST"])
def api_avoid_list_save():
    """Save the avoid-places list."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = request.json
    AVOID_PLACES_FILE.write_text(json.dumps(data, indent=2))
    return jsonify({"ok": True})


@app.route("/api/calendar")
def api_calendar():
    """Return calendar data: past trips + UK school holidays 2026-2027."""
    # Past trips as date ranges
    past_trips = []
    for trip in STATE["trips"]:
        past_trips.append({
            "id": trip.id,
            "name": trip.place_name,
            "country": trip.country,
            "start": trip.start_date.isoformat(),
            "end": trip.end_date.isoformat(),
            "days": trip.duration_days,
            "family": trip.is_family_trip,
        })

    # UK school holidays 2026-2027
    school_holidays = [
        {"name": "Easter 2026", "start": "2026-03-30", "end": "2026-04-13"},
        {"name": "May Half Term 2026", "start": "2026-05-25", "end": "2026-05-29"},
        {"name": "Summer 2026", "start": "2026-07-17", "end": "2026-09-01"},
        {"name": "October Half Term 2026", "start": "2026-10-26", "end": "2026-10-30"},
        {"name": "Christmas 2026", "start": "2026-12-18", "end": "2027-01-02"},
        {"name": "February Half Term 2027", "start": "2027-02-15", "end": "2027-02-19"},
        {"name": "Easter 2027", "start": "2027-04-05", "end": "2027-04-18"},
        {"name": "May Half Term 2027", "start": "2027-05-31", "end": "2027-06-04"},
        {"name": "Summer 2027", "start": "2027-07-23", "end": "2027-09-03"},
    ]

    return jsonify({
        "past_trips": past_trips,
        "school_holidays": school_holidays,
    })


# === HTML TEMPLATE ===

INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wanderlust — Where Next?</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9/dist/leaflet.css">
<style>
  :root {
    --bg: #0a0a0f; --surface: #14141f; --border: #2a2a3a;
    --text: #e0e0e8; --dim: #6a6a7a; --accent: #00cc88;
    --accent2: #0088ff; --warn: #ff6644; --gold: #ffd700; --radius: 12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh;
         font-size: 14px; line-height: 1.5; }

  .app { display: grid; grid-template-columns: 1fr 400px; grid-template-rows: auto 1fr; height: 100vh; }

  .header { grid-column: 1 / -1; padding: 14px 24px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header h1 span { color: var(--accent); }
  .header .stats { margin-left: auto; display: flex; gap: 20px; font-size: 12px; color: var(--dim); }
  .header .stats b { color: var(--accent); font-size: 15px; }

  .main { display: flex; flex-direction: column; overflow: hidden; }

  .main > div:first-child { flex: 1; position: relative; min-height: 300px; }
  #map { position: absolute; inset: 0; }

  /* Trip cards - bottom bar */
  .trips-bar { padding: 8px 10px; border-top: 1px solid var(--border); overflow-x: auto;
               display: flex; gap: 6px; flex-shrink: 0; }
  .trip-card { flex-shrink: 0; background: var(--surface); border: 1px solid var(--border);
               border-radius: 8px; padding: 7px 10px; cursor: pointer; min-width: 130px;
               transition: border-color 0.2s, transform 0.15s;
               border-left: 3px solid var(--warn); }
  .trip-card.reviewed { border-left-color: var(--accent); }
  .trip-card:hover { border-color: var(--accent); transform: translateY(-1px); }
  .trip-card.active { border-color: var(--accent); }
  .trip-card .name { font-weight: 600; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 140px; }
  .trip-card .meta { font-size: 11px; color: var(--dim); margin-top: 1px; }
  .trip-card .rating { color: var(--gold); font-size: 11px; letter-spacing: -1px; }
  .trip-card .unreviewed { color: var(--dim); font-size: 10px; font-style: italic; }

  .sidebar { border-left: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }

  .tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .tab { flex: 1; padding: 10px; text-align: center; font-size: 12px; font-weight: 500;
         cursor: pointer; border-bottom: 2px solid transparent; color: var(--dim);
         transition: color 0.2s, border-color 0.2s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .panel { flex: 1; overflow-y: auto; display: none; }
  .panel.active { display: flex; flex-direction: column; }

  /* Chat panel */
  .chat-messages { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 90%; padding: 10px 14px; border-radius: var(--radius); font-size: 13px; line-height: 1.65; }
  .msg.user { align-self: flex-end; background: var(--accent2); color: white; border-bottom-right-radius: 4px; }
  .msg.ai { align-self: flex-start; background: var(--surface); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
  .msg.ai p { margin-bottom: 6px; }
  .msg.ai p:last-child { margin-bottom: 0; }
  .msg.ai ul, .msg.ai ol { margin: 4px 0 6px 18px; }
  .msg.ai li { margin-bottom: 2px; }
  .msg.ai strong { color: var(--accent); }
  .msg.ai em { color: var(--dim); }

  /* Pulsing dots thinking indicator */
  .thinking-dots { display: inline-flex; gap: 4px; align-items: center; padding: 4px 0; }
  .thinking-dots span { width: 6px; height: 6px; border-radius: 50%; background: var(--dim);
    animation: pulse-dot 1.4s infinite ease-in-out; }
  .thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
  .thinking-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes pulse-dot {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1.1); }
  }

  .chat-input { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border); flex-shrink: 0; }
  .chat-input input { flex: 1; background: var(--surface); border: 1px solid var(--border);
                       border-radius: 8px; padding: 9px 14px; color: var(--text); font-size: 13px; outline: none;
                       transition: border-color 0.2s; }
  .chat-input input:focus { border-color: var(--accent); }
  .chat-input button { background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
                        padding: 9px 14px; font-weight: 600; cursor: pointer; font-size: 13px;
                        transition: opacity 0.2s; }
  .chat-input button:disabled { opacity: 0.4; cursor: default; }

  /* Review / Detail panel */
  .review-content { padding: 0; }
  .review-content h3 { margin-bottom: 8px; font-size: 16px; }
  .detail-section { margin-top: 14px; }
  .detail-section-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
                          color: var(--dim); margin-bottom: 6px; }
  .inline-stars { display: flex; gap: 2px; }
  .inline-star { font-size: 22px; cursor: pointer; color: var(--border); transition: color 0.15s; line-height: 1; }
  .inline-star.filled { color: var(--gold); }
  .inline-star:hover { color: var(--gold); }
  .highlight-chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .highlight-chip { padding: 4px 10px; border-radius: 16px; border: 1px solid var(--border);
                    font-size: 12px; cursor: pointer; transition: all 0.15s; user-select: none; }
  .highlight-chip.selected { background: rgba(0,204,136,0.15); color: var(--accent); border-color: var(--accent); }
  .highlight-chip:hover { border-color: var(--accent); }
  .return-btns { display: flex; gap: 6px; }
  .return-btn { padding: 5px 12px; border-radius: 16px; border: 1px solid var(--border);
                font-size: 12px; cursor: pointer; transition: all 0.15s; user-select: none; background: none; color: var(--text); }
  .return-btn.selected { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .return-btn:hover { border-color: var(--accent); }
  textarea.inline-notes { width: 100%; background: var(--surface); border: 1px solid var(--border);
                          border-radius: 8px; padding: 8px 10px; color: var(--text); font-size: 12px;
                          resize: vertical; min-height: 50px; outline: none; font-family: inherit;
                          transition: border-color 0.2s; }
  textarea.inline-notes:focus { border-color: var(--accent); }
  .more-like-btn { background: none; border: 1px solid var(--accent2); color: var(--accent2); border-radius: 8px;
                   padding: 6px 14px; font-size: 12px; cursor: pointer; transition: all 0.2s; font-weight: 500; }
  .more-like-btn:hover { background: var(--accent2); color: white; }
  .autosave-indicator { font-size: 10px; color: var(--dim); font-style: italic; transition: opacity 0.3s; }

  .stars { display: flex; gap: 4px; }
  .star { font-size: 24px; cursor: pointer; color: var(--border); transition: color 0.2s; }
  .star.filled { color: var(--gold); }
  .star:hover { color: var(--gold); }
  .options { display: flex; flex-wrap: wrap; gap: 6px; }
  .opt { padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border);
         font-size: 13px; cursor: pointer; transition: all 0.2s; user-select: none; }
  .opt.selected { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .opt:hover { border-color: var(--accent); }
  textarea.review-text { width: 100%; background: var(--surface); border: 1px solid var(--border);
                          border-radius: 8px; padding: 10px; color: var(--text); font-size: 14px;
                          resize: vertical; min-height: 60px; outline: none; }
  .save-btn { background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
              padding: 8px 16px; font-weight: 600; cursor: pointer; margin-top: 10px; font-size: 12px; }

  /* Profile panel */
  .profile-content { padding: 16px; }
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
  .stat-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
              padding: 10px; text-align: center; }
  .stat-box .value { font-size: 22px; font-weight: 700; color: var(--accent); }
  .stat-box .label { font-size: 11px; color: var(--dim); margin-top: 3px; }

  /* Map controls */
  .map-controls { position: absolute; top: 12px; left: 12px; z-index: 1000; display: flex; gap: 6px; }
  .map-btn { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
             color: var(--text); font-size: 12px; cursor: pointer; transition: all 0.2s; backdrop-filter: blur(8px); }
  .map-btn:hover, .map-btn.active { border-color: var(--accent); color: var(--accent); }

  /* Frequency panel in sidebar */
  .freq-item { padding: 10px 14px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.2s; }
  .freq-item:hover { background: var(--surface); }
  .freq-region { font-weight: 600; font-size: 14px; }
  .freq-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-left: 6px; }
  .freq-badge.annual { background: var(--accent); color: var(--bg); }
  .freq-badge.regular { background: var(--accent2); color: white; }
  .freq-badge.occasional { background: #886600; color: white; }
  .freq-badge.oneoff { background: var(--border); color: var(--dim); }
  .freq-meta { font-size: 12px; color: var(--dim); margin-top: 3px; }
  .freq-years { display: flex; gap: 4px; margin-top: 4px; }
  .freq-year { padding: 1px 6px; border-radius: 4px; background: var(--border); font-size: 11px; color: var(--text); }

  /* Timeline */
  .timeline { padding: 16px; }
  .timeline-year { margin-bottom: 16px; }
  .timeline-year h4 { color: var(--accent); font-size: 14px; margin-bottom: 6px; }
  .timeline-trips { display: flex; flex-wrap: wrap; gap: 4px; }
  .timeline-trip { padding: 4px 8px; border-radius: 6px; font-size: 12px; border: 1px solid var(--border); }
  .timeline-trip.family { border-color: var(--accent); color: var(--accent); }

  .quick-prompts { display: flex; flex-wrap: wrap; gap: 5px; padding: 6px 12px; border-top: 1px solid var(--border); flex-shrink: 0; }
  .quick-prompt { padding: 3px 9px; border-radius: 14px; border: 1px solid rgba(42,42,58,0.6);
                  font-size: 11px; cursor: pointer; color: var(--dim); transition: all 0.2s;
                  background: none; }
  .quick-prompt:hover { border-color: var(--accent); color: var(--text); background: rgba(0,204,136,0.06); }

  .stop-number { background: none !important; border: none !important; box-shadow: none !important;
                  color: var(--bg) !important; font-size: 10px !important; font-weight: 700 !important; padding: 0 !important; }

  /* Dark Leaflet popups */
  .leaflet-popup-content-wrapper { background: var(--surface) !important; color: var(--text) !important; border: 1px solid var(--border) !important; border-radius: var(--radius) !important; box-shadow: 0 4px 16px rgba(0,0,0,0.4) !important; }
  .leaflet-popup-tip { background: var(--surface) !important; border: 1px solid var(--border) !important; }
  .leaflet-popup-close-button { color: var(--dim) !important; }

  /* Scrollbar styling */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* Trip card polish */
  .trip-card { box-shadow: 0 1px 3px rgba(0,0,0,0.2); }
  .trip-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.35); }
  .trip-card.reviewed { border-left-color: var(--accent); }

  /* Chat message entrance animation */
  @keyframes msg-fade-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .msg { animation: msg-fade-in 0.3s ease-out; }

  /* Planner */
  .plan-form { padding: 16px; }
  .plan-form label { display: block; font-size: 12px; font-weight: 600; color: var(--dim); margin: 12px 0 4px; }
  .plan-form select, .plan-form input[type=date], .plan-form input[type=number] {
    width: 100%; background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px; font-size: 13px; outline: none;
  }
  .plan-form select:focus, .plan-form input:focus { border-color: var(--accent); }
  .plan-interests { display: flex; flex-wrap: wrap; gap: 4px; }
  .plan-interest { padding: 4px 10px; border-radius: 14px; border: 1px solid var(--border);
    font-size: 12px; cursor: pointer; transition: all 0.2s; }
  .plan-interest.selected { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .plan-who { display: flex; flex-wrap: wrap; gap: 4px; }
  .plan-who label { display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
    padding: 4px 10px; border-radius: 14px; border: 1px solid var(--border); cursor: pointer; margin: 0; }
  .plan-who input:checked + span { color: var(--accent); }
  .generate-btn { width: 100%; padding: 12px; background: var(--accent); color: var(--bg);
    border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; margin-top: 16px; }
  .generate-btn:disabled { opacity: 0.5; cursor: default; }

  /* Route display */
  .route-stop { padding: 12px; border-left: 3px solid var(--accent); margin-left: 12px; margin-bottom: 0; position: relative; }
  .route-stop::before { content: attr(data-day); position: absolute; left: -24px; top: 12px;
    background: var(--accent); color: var(--bg); width: 20px; height: 20px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; }
  .route-stop h4 { font-size: 14px; margin: 0 0 4px; }
  .route-stop .drive-info { font-size: 11px; color: var(--dim); margin-bottom: 6px; }
  .route-stop .activities { font-size: 12px; }
  .route-stop .why { font-size: 12px; color: var(--accent); font-style: italic; margin-top: 4px; }
  .route-stop .similar { font-size: 11px; color: var(--dim); margin-top: 2px; }
  .route-stop .stop-actions { display: flex; gap: 4px; margin-top: 6px; }
  .route-stop .stop-actions button { padding: 2px 8px; font-size: 11px; border: 1px solid var(--border);
    border-radius: 4px; background: var(--surface); color: var(--dim); cursor: pointer; }

  .plan-loading { padding: 40px 16px; text-align: center; }
  .plan-loading .pulse { animation: pulse 1.5s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }

  /* Calendar */
  .cal-grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 2px; padding: 8px; }
  .cal-month { font-size: 10px; text-align: center; padding: 4px; border-radius: 4px; }
  .cal-month.has-trip { background: rgba(0,204,136,0.2); border: 1px solid var(--accent); }
  .cal-month.holiday { background: rgba(255,170,0,0.15); border: 1px solid #aa7700; }
  .cal-month.gap { background: rgba(0,136,255,0.1); border: 1px dashed var(--accent2); cursor: pointer; }
  .cal-year-label { font-size: 11px; font-weight: 600; color: var(--dim); padding: 4px 0; }

  /* Saved plans */
  .saved-plan { padding: 10px; border: 1px solid var(--border); border-radius: 8px;
    margin-bottom: 8px; cursor: pointer; transition: border-color 0.2s; }
  .saved-plan:hover { border-color: var(--accent); }
  .saved-plan h4 { font-size: 13px; margin: 0 0 2px; }
  .saved-plan .meta { font-size: 11px; color: var(--dim); }
</style>
</head>
<body>

<div class="app">
  <div class="header">
    <h1>🌍 <span>Wanderlust</span></h1>
    <div class="stats">
      <div><b id="tripCount">0</b> trips</div>
      <div><b id="countryCount">0</b> countries</div>
      <div><b id="reviewCount">0</b> reviewed</div>
      <button class="map-btn" style="margin-left:8px" onclick="quickReview()">⚡ Quick review</button>
    </div>
  </div>

  <div class="main">
    <div style="position:relative">
      <div id="map"></div>
      <div class="map-controls">
        <button class="map-btn active" onclick="toggleLayer('markers',this)">📍 Trips</button>
        <button class="map-btn" onclick="toggleLayer('heat',this)">🔥 Heatmap</button>
        <button class="map-btn" onclick="toggleLayer('freq',this)">🔄 Frequency</button>
        <span style="width:1px;background:var(--border);margin:0 4px"></span>
        <button class="map-btn" onclick="filterType('all',this)">All</button>
        <button class="map-btn" onclick="filterType('stay',this)">🏨 Stays</button>
        <button class="map-btn" onclick="filterType('road trip',this)">🚗 Road trips</button>
        <button class="map-btn" onclick="filterType('day trip',this)">📍 Day trips</button>
        <span style="width:1px;background:var(--border);margin:0 4px"></span>
        <div style="position:relative;display:inline-block" id="peopleFilterWrap">
          <button class="map-btn" onclick="togglePeopleDropdown()" id="peopleFilterBtn">👥 People</button>
          <div id="peopleDropdown" style="display:none;position:absolute;top:100%;left:0;margin-top:4px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px;min-width:200px;max-height:300px;overflow-y:auto;z-index:2000">
          </div>
        </div>
        <div style="position:relative;display:inline-block" id="yearFilterWrap">
          <button class="map-btn" onclick="toggleYearDropdown()" id="yearFilterBtn">📅 Years</button>
          <div id="yearDropdown" style="display:none;position:absolute;top:100%;left:0;margin-top:4px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px;min-width:120px;max-height:300px;overflow-y:auto;z-index:2000">
          </div>
        </div>
        <button class="map-btn" id="soloFilterBtn" onclick="toggleSoloFilter(this)">👤 Hide solo</button>
        <button class="map-btn" id="unreviewedFilterBtn" onclick="toggleUnreviewedFilter(this)">⚠ Unreviewed</button>
      </div>
    </div>
    <div class="trips-bar" id="tripsBar"></div>
  </div>

  <div class="sidebar">
    <div class="tabs">
      <div class="tab active" data-panel="chat">💬 Ask</div>
      <div class="tab" data-panel="plan">🗺️ Plan</div>
      <div class="tab" data-panel="review">⭐ Review</div>
      <div class="tab" data-panel="timeline">📅 Timeline</div>
      <div class="tab" data-panel="frequency">🔄 Freq</div>
      <div class="tab" data-panel="profile">📊 Profile</div>
    </div>

    <div class="panel active" id="panel-chat">
      <div class="chat-messages" id="chatMessages">
        <div class="msg ai">
          Hi! I know where you've been — tell me what kind of trip you're thinking about
          and I'll find somewhere new. 🌍<br><br>
          <em style="color:var(--dim)">Try: "Family beach holiday in October" or
          "Somewhere we haven't been, good food, 5 days"</em>
        </div>
      </div>
      <div class="quick-prompts">
        <span class="quick-prompt" onclick="sendQuick(this)">🏖️ Beach with kids</span>
        <span class="quick-prompt" onclick="sendQuick(this)">🏔️ Mountain adventure</span>
        <span class="quick-prompt" onclick="sendQuick(this)">🍽️ Foodie weekend</span>
        <span class="quick-prompt" onclick="sendQuick(this)">☀️ Easter half term</span>
        <span class="quick-prompt" onclick="sendQuick(this)">🎿 Ski trip 2027</span>
        <span class="quick-prompt" onclick="sendQuick(this)">🏛️ Culture + history</span>
      </div>
      <div class="chat-input">
        <input id="chatInput" placeholder="Where should we go next?" onkeydown="if(event.key==='Enter')sendChat()">
        <button onclick="sendChat()" id="sendBtn">→</button>
      </div>
    </div>

    <div class="panel" id="panel-plan" style="overflow-y:auto">
      <div style="display:flex;border-bottom:1px solid var(--border)">
        <button class="tab" style="flex:1" onclick="showPlanView('planner')" id="planViewPlanner">🗺️ Planner</button>
        <button class="tab" style="flex:1" onclick="showPlanView('calendar')" id="planViewCalendar">📅 Calendar</button>
        <button class="tab" style="flex:1" onclick="showPlanView('saved')" id="planViewSaved">💾 Saved</button>
      </div>
      <div id="planContent"></div>
    </div>

    <div class="panel" id="panel-review">
      <div class="review-content" id="reviewContent">
        <p style="color:var(--dim);padding:20px;text-align:center">
          Click a trip on the map or in the bar below to review it
        </p>
      </div>
    </div>

    <div class="panel" id="panel-timeline" style="overflow-y:auto">
      <div id="timelineContent" style="padding:16px">
        <p style="color:var(--dim)">Loading timeline...</p>
      </div>
    </div>

    <div class="panel" id="panel-frequency">
      <div id="frequencyContent" style="overflow-y:auto;flex:1">
        <p style="color:var(--dim);padding:20px;text-align:center">Loading frequency data...</p>
      </div>
    </div>

    <div class="panel" id="panel-profile">
      <div class="profile-content" id="profileContent">
        <p style="color:var(--dim)">Loading profile...</p>
      </div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2/dist/leaflet-heat.js"></script>
<script>
// === STATE ===
let trips = [];
let chatHistory = [];
let selectedTrip = null;
let map, markers = {};
let markerLayer, heatLayer, freqLayer;
let layers = { markers: true, heat: false, freq: false };
let currentPlan = null;
let plannerState = 'setup';
let plannedRouteLayer = null;
let exclusionZoneLayer = null;

// === INIT ===
async function init() {
  // Init map
  map = L.map('map', { zoomControl: false }).setView([30, 0], 3);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '© CartoDB', maxZoom: 19
  }).addTo(map);
  L.control.zoom({ position: 'topright' }).addTo(map);

  // Load trips
  const resp = await fetch('api/trips');
  trips = await resp.json();

  // Update stats
  document.getElementById('tripCount').textContent = trips.length;
  document.getElementById('countryCount').textContent = [...new Set(trips.map(t => t.country).filter(Boolean))].length;
  document.getElementById('reviewCount').textContent = trips.filter(t => t.reviewed).length;

  // Marker layer
  markerLayer = L.layerGroup().addTo(map);
  const bounds = [];
  trips.forEach(t => {
    const color = t.family ? '#00cc88' : '#0088ff';
    const radius = Math.max(8, Math.min(16, t.photos / 10));
    const marker = L.circleMarker([t.lat, t.lon], {
      radius, color, fillColor: color, fillOpacity: 0.6, weight: 2
    }).addTo(markerLayer);

    const rating = t.rating ? '⭐'.repeat(t.rating) : '<span style="color:#ff6644">Not reviewed</span>';
    const peopleHtml = t.people.length
      ? '<br>👥 ' + t.people.map(p => {
          const count = t.people_counts?.[p];
          return count ? `${p} <span style="opacity:0.6">(${count} photos)</span>` : p;
        }).join(', ')
      : '';
    const familyBadge = t.family ? ' <span style="color:#00cc88">👨‍👩‍👧 Family</span>' : '';
    marker.bindPopup(`<b>${t.name || 'Unknown'}</b>${familyBadge}<br>${new Date(t.start).toLocaleDateString('en-GB', {month:'short',year:'numeric'})} — ${new Date(t.end).toLocaleDateString('en-GB', {month:'short',year:'numeric'})}<br>${t.days} days, ${t.photos} photos${peopleHtml}<br>${rating}`);
    marker.on('click', () => selectTrip(t.id));
    marker.on('mouseover', function() { this.bringToFront(); });
    markers[t.id] = marker;
    bounds.push([t.lat, t.lon]);
  });

  if (bounds.length) map.fitBounds(bounds, { padding: [30, 30] });

  // Load heatmap data
  const heatResp = await fetch('api/photo-locations');
  const heatPoints = await heatResp.json();
  heatLayer = L.heatLayer(heatPoints, {
    radius: 25, blur: 20, maxZoom: 10,
    gradient: {0.2: '#0044ff', 0.4: '#00ccff', 0.6: '#00ff88', 0.8: '#ffcc00', 1.0: '#ff4400'}
  });

  // Load frequency data for map overlay
  const freqResp = await fetch('api/frequency');
  const freqData = await freqResp.json();
  freqLayer = L.layerGroup();
  freqData.forEach(f => {
    const colors = { 'Annual favourite': '#00cc88', 'Regular': '#0088ff', 'Occasional': '#886600', 'One-off': '#4a4a5a' };
    const color = colors[f.frequency_label] || '#4a4a5a';
    const circle = L.circle([f.lat, f.lon], {
      radius: Math.max(20000, f.visit_count * 25000),
      color, fillColor: color, fillOpacity: 0.2, weight: 2, dashArray: f.visit_count > 1 ? '' : '5,5'
    });
    circle.bindPopup(
      `<b>${f.region}</b><br>` +
      `Visited <b>${f.visit_count}x</b> (${f.frequency_label})<br>` +
      `${f.total_days} total days, ${f.total_photos} photos<br>` +
      `Years: ${f.years.join(', ')}`
    );
    circle.addTo(freqLayer);
  });

  // Render frequency sidebar
  renderFrequency(freqData);

  // Trip cards
  renderTripCards();

  // Populate people filter dropdown
  populatePeopleFilter();

  // Render timeline
  renderTimeline(trips);

  // Load profile
  loadProfile();

  // Init planner layers
  plannedRouteLayer = L.layerGroup();
  exclusionZoneLayer = L.layerGroup();
  renderPlanSetup();
}

function renderTripCards() {
  const bar = document.getElementById('tripsBar');
  bar.innerHTML = trips.map(t => {
    const rating = t.rating
      ? '<span class="rating">' + '<span style="color:var(--gold)">★</span>'.repeat(t.rating) + '<span style="color:var(--border)">★</span>'.repeat(5-t.rating) + '</span>'
      : '<span class="unreviewed">⚠ Not reviewed</span>';
    const ppl = t.people.length ? `<div class="meta" style="color:#00cc88">${t.family ? '👨‍👩‍👧 ' : '👥 '}${t.people.slice(0,3).join(', ')}</div>` : '';
    const typeBadge = t.trip_type === 'road trip' ? ' 🚗' : '';
    const reviewedClass = t.reviewed ? ' reviewed' : '';
    return `<div class="trip-card${reviewedClass}" data-id="${t.id}" data-people='${JSON.stringify(t.people)}' data-type="${t.trip_type || 'stay'}" onclick="selectTrip(${t.id})">
      <div class="name">${t.name || 'Unknown'}${typeBadge}</div>
      <div class="meta">${new Date(t.start).toLocaleDateString('en-GB', {month:'short',year:'numeric'})} · ${t.days}d · ${t.photos} 📷</div>
      ${ppl}
      ${rating}
    </div>`;
  }).join('');
}

let _selectingTrip = false;
function selectTrip(id) {
  if (_selectingTrip) return; // prevent re-entrant calls
  _selectingTrip = true;

  selectedTrip = trips.find(t => t.id === id);
  if (!selectedTrip) { _selectingTrip = false; return; }

  // Highlight card
  document.querySelectorAll('.trip-card').forEach(c => c.classList.toggle('active', parseInt(c.dataset.id) === id));

  // Pan map — don't change zoom if already zoomed in enough
  const currentZoom = map.getZoom();
  map.setView([selectedTrip.lat, selectedTrip.lon], Math.max(currentZoom, 6));

  // Switch to review tab and load detail
  switchTab('review');
  loadTripDetail(id);

  _selectingTrip = false;
}

async function loadTripDetail(tripId) {
  const [detailResp, photosResp] = await Promise.all([
    fetch(`api/trip/${tripId}/detail`),
    fetch(`api/trip/${tripId}/photos`)
  ]);
  const detail = await detailResp.json();
  const photos = await photosResp.json();

  const container = document.getElementById('reviewContent');
  const typIcon = detail.trip_type === 'road trip' ? '🚗' : detail.trip_type === 'day trip' ? '📍' : '🏨';
  const familyBadge = detail.family ? '<span style="color:var(--accent)">👨‍👩‍👧 Family trip</span>' : '';

  // Photo gallery
  let photoHtml = '';
  if (photos.length) {
    photoHtml = `<div style="display:flex;gap:4px;overflow-x:auto;padding:8px 0;flex-shrink:0">
      ${photos.map(p => `<img src="api/photo/${p.uuid}" loading="lazy" onclick="openInPhotos('${p.uuid}')" style="height:80px;width:80px;object-fit:cover;border-radius:6px;flex-shrink:0;cursor:pointer;border:${p.favorite ? '2px solid #ffd700' : '1px solid var(--border)'};transition:transform 0.15s" onmouseover="this.style.transform='scale(1.1)'" onmouseout="this.style.transform=''" onerror="this.style.display='none'" title="Click to open in Photos">`).join('')}
    </div>`;
  }

  // People
  const peopleHtml = detail.people.length ? detail.people.map(p => {
    const count = detail.people_counts?.[p] || 0;
    return `<span style="display:inline-block;padding:3px 10px;border-radius:14px;font-size:12px;background:rgba(0,204,136,0.12);color:var(--accent);margin:2px">${p} <span style="opacity:0.6">${count}</span></span>`;
  }).join('') : '<span style="color:var(--dim)">No people detected</span>';

  // Stops with mini route map
  let stopsHtml = '';
  if (detail.stops?.length > 1) {
    stopsHtml = `<div style="margin-top:12px">
      <div style="font-size:12px;font-weight:600;margin-bottom:6px">📌 ${detail.stops.length} stops</div>
      <div id="miniRouteMap" style="height:180px;border-radius:8px;border:1px solid var(--border);margin-bottom:8px"></div>
      ${detail.stops.map((s, i) => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;cursor:pointer" onclick="map.setView([${s.lat},${s.lon}],12)">
        <span style="background:var(--accent);color:var(--bg);width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0">${i+1}</span>
        <span style="flex:1">${s.name || `${s.lat.toFixed(2)}, ${s.lon.toFixed(2)}`}</span>
        <span style="color:var(--dim)">${s.photo_count}</span>
      </div>`).join('')}
    </div>`;
  }

  // Daily breakdown sparkline
  let dailyHtml = '';
  if (detail.daily_breakdown?.length > 1) {
    const maxPhotos = Math.max(...detail.daily_breakdown.map(d => d.photos));
    dailyHtml = `<div style="margin-top:12px">
      <div style="font-size:12px;font-weight:600;margin-bottom:6px">📊 Daily photos</div>
      <div style="display:flex;align-items:end;gap:2px;height:40px">
        ${detail.daily_breakdown.map(d => {
          const h = Math.max(4, (d.photos / maxPhotos) * 36);
          const day = new Date(d.date).toLocaleDateString('en-GB', {weekday:'short'});
          return `<div title="${day}: ${d.photos} photos" style="flex:1;background:var(--accent2);border-radius:2px 2px 0 0;height:${h}px;min-width:4px;opacity:0.7"></div>`;
        }).join('')}
      </div>
    </div>`;
  }

  // Inline review widgets
  const review = detail.review || {};
  const currentRating = review.overall || 0;
  const currentHighlights = review.highlights || [];
  const currentReturn = review.would_return || '';
  const currentNotes = review.notes || '';

  const highlightOptions = [
    '🏖️ Beach', '🏛️ Culture', '🍽️ Food', '🏔️ Nature',
    '🎢 Adventure', '👨‍👩‍👧 Family time', '🌆 City', '☀️ Weather'
  ];

  const returnOptions = ['Definitely', 'Maybe', 'Probably not', 'No way'];

  const starsHtml = `<div class="detail-section">
    <div class="detail-section-title">Rating</div>
    <div class="inline-stars" id="inlineStars" data-trip="${tripId}">
      ${[1,2,3,4,5].map(i => `<span class="inline-star ${i <= currentRating ? 'filled' : ''}" data-val="${i}" onclick="setInlineStar(${tripId},${i})">★</span>`).join('')}
    </div>
  </div>`;

  const chipsHtml = `<div class="detail-section">
    <div class="detail-section-title">Highlights</div>
    <div class="highlight-chips" id="inlineChips" data-trip="${tripId}">
      ${highlightOptions.map(h => `<span class="highlight-chip ${currentHighlights.includes(h) ? 'selected' : ''}" onclick="toggleChip(this,${tripId})">${h}</span>`).join('')}
    </div>
  </div>`;

  const returnHtml = `<div class="detail-section">
    <div class="detail-section-title">Would you return?</div>
    <div class="return-btns" id="inlineReturn" data-trip="${tripId}">
      ${returnOptions.map(r => `<button class="return-btn ${currentReturn === r ? 'selected' : ''}" onclick="selectReturn(this,${tripId})">${r}</button>`).join('')}
    </div>
  </div>`;

  const notesHtml = `<div class="detail-section">
    <div class="detail-section-title">Notes</div>
    <textarea class="inline-notes" id="inlineNotes" data-trip="${tripId}" placeholder="Anything you want to remember...">${currentNotes}</textarea>
  </div>`;

  const tripDate = new Date(detail.start).toLocaleDateString('en-GB', {month:'short', year:'numeric'});
  const highlightsText = currentHighlights.length ? currentHighlights.join(', ') : 'various activities';
  const moreLikeHtml = `<div class="detail-section" style="display:flex;align-items:center;gap:10px">
    <button class="more-like-btn" onclick="findMoreLike('${(detail.name||'').replace(/'/g,"\\'")}','${tripDate}','${highlightsText.replace(/'/g,"\\'")}')">🔍 Find more like this</button>
    <span class="autosave-indicator" id="autosaveIndicator" style="opacity:0"></span>
  </div>`;

  container.innerHTML = `
    <div style="padding:16px">
      <h3 style="margin-bottom:4px">${typIcon} ${detail.name || 'Unknown'}</h3>
      <div style="color:var(--dim);font-size:13px;margin-bottom:12px">
        ${new Date(detail.start).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'})} — ${new Date(detail.end).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'})}
        · ${detail.days} days · ${detail.photos} photos${detail.favorites ? ` · ${detail.favorites} ⭐` : ''}
        ${familyBadge}
      </div>
      ${photoHtml}
      <div style="margin-top:12px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">👥 People</div>
        ${peopleHtml}
      </div>
      ${stopsHtml}
      ${dailyHtml}
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
        ${starsHtml}
        ${chipsHtml}
        ${returnHtml}
        ${notesHtml}
        ${moreLikeHtml}
      </div>
    </div>
  `;

  // Debounced autosave for notes
  const notesEl = document.getElementById('inlineNotes');
  if (notesEl) {
    let notesTimer;
    notesEl.addEventListener('input', () => {
      clearTimeout(notesTimer);
      notesTimer = setTimeout(() => autoSaveReview(tripId), 800);
    });
    notesEl.addEventListener('blur', () => {
      clearTimeout(notesTimer);
      autoSaveReview(tripId);
    });
  }

  // Init mini route map if stops exist
  if (detail.stops?.length > 1) {
    setTimeout(() => {
      const mapEl = document.getElementById('miniRouteMap');
      if (!mapEl) return;
      const miniMap = L.map(mapEl, { zoomControl: false, attributionControl: false });
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19 }).addTo(miniMap);

      const coords = detail.stops.map(s => [s.lat, s.lon]);
      // Route line
      L.polyline(coords, { color: '#00cc88', weight: 3, opacity: 0.8 }).addTo(miniMap);
      // Numbered markers
      detail.stops.forEach((s, i) => {
        L.circleMarker([s.lat, s.lon], {
          radius: 10, color: '#00cc88', fillColor: '#00cc88', fillOpacity: 1, weight: 0
        }).bindTooltip(`${i+1}`, { permanent: true, direction: 'center', className: 'stop-number' }).addTo(miniMap);
      });
      miniMap.fitBounds(coords, { padding: [20, 20] });
    }, 100);
  }
}

function setInlineStar(tripId, val) {
  const container = document.getElementById('inlineStars');
  container.querySelectorAll('.inline-star').forEach(s => {
    s.classList.toggle('filled', parseInt(s.dataset.val) <= val);
  });
  autoSaveReview(tripId);
}

function toggleChip(el, tripId) {
  el.classList.toggle('selected');
  autoSaveReview(tripId);
}

function selectReturn(el, tripId) {
  el.parentElement.querySelectorAll('.return-btn').forEach(b => b.classList.remove('selected'));
  el.classList.add('selected');
  autoSaveReview(tripId);
}

function findMoreLike(name, date, highlights) {
  switchTab('chat');
  const msg = `Find me a trip similar to ${name} — we went there in ${date} and enjoyed ${highlights}`;
  document.getElementById('chatInput').value = msg;
  sendChat();
}

async function autoSaveReview(tripId) {
  const review = {};

  // Read stars
  const starsEl = document.getElementById('inlineStars');
  if (starsEl) {
    const filled = starsEl.querySelectorAll('.inline-star.filled');
    review.overall = filled.length;
  }

  // Read highlight chips
  const chipsEl = document.getElementById('inlineChips');
  if (chipsEl) {
    review.highlights = [...chipsEl.querySelectorAll('.highlight-chip.selected')].map(c => c.textContent);
  }

  // Read would-return
  const returnEl = document.getElementById('inlineReturn');
  if (returnEl) {
    const sel = returnEl.querySelector('.return-btn.selected');
    review.would_return = sel ? sel.textContent : '';
  }

  // Read notes
  const notesEl = document.getElementById('inlineNotes');
  if (notesEl && notesEl.value.trim()) {
    review.notes = notesEl.value.trim();
  }

  await fetch(`api/trip/${tripId}/review`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(review)
  });

  // Update trip state client-side
  const trip = trips.find(t => t.id === tripId);
  if (trip) { trip.reviewed = true; trip.rating = review.overall; }
  document.getElementById('reviewCount').textContent = trips.filter(t => t.reviewed).length;
  renderTripCards();

  // Show autosave indicator
  const indicator = document.getElementById('autosaveIndicator');
  if (indicator) {
    indicator.textContent = 'Saved ✓';
    indicator.style.opacity = '1';
    setTimeout(() => { indicator.style.opacity = '0'; }, 2000);
  }
}

// === MARKDOWN RENDERER ===
function renderMarkdown(text) {
  if (!text) return '';
  // Split into lines for processing
  const lines = text.split('\n');
  let html = '';
  let inUl = false, inOl = false;

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // Headings: ## heading
    if (/^#{1,3}\s+/.test(line)) {
      if (inUl) { html += '</ul>'; inUl = false; }
      if (inOl) { html += '</ol>'; inOl = false; }
      const heading = line.replace(/^#{1,3}\s+/, '');
      html += `<h4 style="margin:8px 0 4px;color:var(--accent)">${heading}</h4>`;
      continue;
    }

    // Unordered list: - item or * item
    if (/^[\-\*]\s+/.test(line)) {
      if (inOl) { html += '</ol>'; inOl = false; }
      if (!inUl) { html += '<ul>'; inUl = true; }
      let item = line.replace(/^[\-\*]\s+/, '');
      item = item.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      item = item.replace(/\*(.+?)\*/g, '<em>$1</em>');
      html += `<li>${item}</li>`;
      continue;
    }

    // Ordered list: 1. item
    if (/^\d+\.\s+/.test(line)) {
      if (inUl) { html += '</ul>'; inUl = false; }
      if (!inOl) { html += '<ol>'; inOl = true; }
      let item = line.replace(/^\d+\.\s+/, '');
      item = item.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      item = item.replace(/\*(.+?)\*/g, '<em>$1</em>');
      html += `<li>${item}</li>`;
      continue;
    }

    // Close lists if needed
    if (inUl) { html += '</ul>'; inUl = false; }
    if (inOl) { html += '</ol>'; inOl = false; }

    // Empty line = paragraph break
    if (line.trim() === '') {
      html += '<br>';
      continue;
    }

    // Inline formatting
    line = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    line = line.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Check if next line is also a regular text line (single newline -> <br>)
    const nextLine = i + 1 < lines.length ? lines[i + 1] : '';
    const nextIsText = nextLine.trim() !== '' && !/^[\-\*#]/.test(nextLine) && !/^\d+\.\s/.test(nextLine);
    html += line + (nextIsText ? '<br>' : '');
  }

  if (inUl) html += '</ul>';
  if (inOl) html += '</ol>';

  return html;
}

// === CHAT ===
async function sendChat() {
  const input = document.getElementById('chatInput');
  const msg = input.value.trim();
  if (!msg) return;

  input.value = '';
  addChatMessage('user', msg);
  chatHistory.push({role: 'user', content: msg});

  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  addChatMessage('ai', '<div class="thinking-dots"><span></span><span></span><span></span></div>', 'thinking-msg');

  try {
    const resp = await fetch('api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: msg, history: chatHistory })
    });
    const data = await resp.json();

    // Remove thinking message
    document.getElementById('thinking-msg')?.remove();

    addChatMessage('ai', data.response);
    chatHistory.push({role: 'assistant', content: data.response});
  } catch(e) {
    document.getElementById('thinking-msg')?.remove();
    addChatMessage('ai', 'Sorry, something went wrong. Try again?');
  }

  btn.disabled = false;
}

function sendQuick(el) {
  document.getElementById('chatInput').value = el.textContent.replace(/^[^\s]+\s/, '');
  sendChat();
}

function addChatMessage(role, content, id) {
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  if (id) div.id = id;
  // For AI messages (not thinking indicators), render markdown
  if (role === 'ai' && !id) {
    div.innerHTML = renderMarkdown(content);
  } else {
    div.innerHTML = content;
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

// === TABS ===
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.panel === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === `panel-${name}`));
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.panel)));

// === PROFILE ===
async function loadProfile() {
  try {
    const resp = await fetch('api/profile');
    if (!resp.ok) return;
    const p = await resp.json();

    document.getElementById('profileContent').innerHTML = `
      <div class="stat-grid">
        <div class="stat-box"><div class="value">${p.total_trips}</div><div class="label">Trips</div></div>
        <div class="stat-box"><div class="value">${p.total_days}</div><div class="label">Days Away</div></div>
        <div class="stat-box"><div class="value">${p.countries.length}</div><div class="label">Countries</div></div>
        <div class="stat-box"><div class="value">${p.avg_trip_days}</div><div class="label">Avg Trip (days)</div></div>
      </div>
      <h4 style="margin-bottom:8px">🌍 Countries</h4>
      <p style="color:var(--dim);font-size:13px;margin-bottom:16px">${p.countries.join(', ') || 'None detected yet'}</p>
      <h4 style="margin-bottom:8px">📊 Travel DNA</h4>
      <div style="font-size:13px;color:var(--dim);line-height:1.8">
        <div>Season: <b style="color:var(--accent)">${p.preferred_seasons.join(', ')}</b></div>
        <div>Style: <b style="color:var(--accent)">${p.preferences.style || '?'}</b></div>
        <div>Range: <b style="color:var(--accent)">${p.avg_distance_km}km avg</b> (max: ${p.max_distance_km}km — ${p.farthest || '?'})</div>
        <div>Family trips: <b style="color:var(--accent)">${p.family_pct}%</b></div>
        <div>Most with: <b style="color:var(--accent)">${p.most_travelled_with.slice(0,3).join(', ') || '?'}</b></div>
        <div>Adventurousness: <b style="color:var(--accent)">${p.preferences.adventurousness || '?'}</b></div>
      </div>
    `;
  } catch(e) {
    document.getElementById('profileContent').innerHTML = '<p style="color:var(--dim)">Run <code>wanderlust scan</code> first to load trips</p>';
  }
}

// === MAP LAYERS ===
function toggleLayer(name, btn) {
  layers[name] = !layers[name];
  btn.classList.toggle('active', layers[name]);

  if (name === 'markers') { layers[name] ? markerLayer.addTo(map) : map.removeLayer(markerLayer); }
  if (name === 'heat') { layers[name] ? heatLayer.addTo(map) : map.removeLayer(heatLayer); }
  if (name === 'freq') { layers[name] ? freqLayer.addTo(map) : map.removeLayer(freqLayer); }
}

// === FREQUENCY ===
function renderFrequency(data) {
  const container = document.getElementById('frequencyContent');
  if (!data.length) { container.innerHTML = '<p style="color:var(--dim);padding:20px">No trip data yet</p>'; return; }

  const badgeClass = {'Annual favourite':'annual','Regular':'regular','Occasional':'occasional','One-off':'oneoff'};

  let html = '<div style="padding:12px 14px;border-bottom:1px solid var(--border);font-size:13px;color:var(--dim)">' +
    'How often you visit each region — based on ' + trips.length + ' trips</div>';

  data.sort((a,b) => b.visit_count - a.visit_count);

  data.forEach(f => {
    html += `<div class="freq-item" onclick="map.setView([${f.lat},${f.lon}],6)">
      <div>
        <span class="freq-region">${f.region}</span>
        <span class="freq-badge ${badgeClass[f.frequency_label] || 'oneoff'}">${f.frequency_label}</span>
      </div>
      <div class="freq-meta">${f.visit_count} visit${f.visit_count>1?'s':''} · ${f.total_days} days · ${f.total_photos} photos</div>
      <div class="freq-years">${f.years.map(y => `<span class="freq-year">${y}</span>`).join('')}</div>
    </div>`;
  });

  // Timeline at bottom
  html += '<div style="padding:12px 14px;border-top:2px solid var(--border);margin-top:8px">';
  html += '<h4 style="color:var(--accent);margin-bottom:8px">📅 Timeline</h4>';

  const byYear = {};
  trips.forEach(t => {
    const y = new Date(t.start).getFullYear();
    if (!byYear[y]) byYear[y] = [];
    byYear[y].push(t);
  });

  Object.keys(byYear).sort((a,b) => b-a).forEach(year => {
    html += `<div style="margin-bottom:10px"><div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:4px">${year}</div>`;
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px">';
    byYear[year].sort((a,b) => new Date(a.start) - new Date(b.start)).forEach(t => {
      const month = new Date(t.start).toLocaleDateString('en-GB', {month:'short'});
      const cls = t.family ? 'family' : '';
      html += `<span class="timeline-trip ${cls}">${month}: ${t.name || '?'}</span>`;
    });
    html += '</div></div>';
  });
  html += '</div>';

  container.innerHTML = html;
}

// === UTILS ===
async function openInPhotos(uuid) {
  try {
    const resp = await fetch(`api/photo/${uuid}/open`);
    if (!resp.ok) console.warn('Could not open in Photos');
  } catch(e) { console.warn('Photos open failed:', e); }
}

function haversineJS(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = (lat2-lat1) * Math.PI/180;
  const dLon = (lon2-lon1) * Math.PI/180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

// === FILTERS ===
let activeTypeFilter = 'all';
let selectedPeople = [];
let peopleFilterMode = 'any'; // 'any' (OR) or 'all' (AND)
let hideSoloTrips = false;
let showOnlyUnreviewed = false;
let selectedYears = [];
let routeLayer;

function toggleUnreviewedFilter(btn) {
  showOnlyUnreviewed = !showOnlyUnreviewed;
  btn.classList.toggle('active', showOnlyUnreviewed);
  btn.textContent = showOnlyUnreviewed ? '⚠ Unreviewed only' : '⚠ Unreviewed';
  applyFilters();
}

function quickReview() {
  const unreviewed = trips.filter(t => !t.reviewed);
  if (!unreviewed.length) {
    alert('All trips have been reviewed!');
    return;
  }
  // Pick the first unreviewed trip
  selectTrip(unreviewed[0].id);
}

function populatePeopleFilter() {
  const allPeople = {};
  trips.forEach(t => t.people.forEach(p => { allPeople[p] = (allPeople[p] || 0) + 1; }));
  const dd = document.getElementById('peopleDropdown');
  let html = `<div style="display:flex;gap:4px;padding:4px 8px;margin-bottom:4px;border-bottom:1px solid var(--border)">
    <button onclick="setPeopleMode('any')" id="modeAny" style="flex:1;padding:3px;border-radius:4px;border:1px solid var(--border);font-size:11px;cursor:pointer;background:var(--accent);color:var(--bg)">Any of</button>
    <button onclick="setPeopleMode('all')" id="modeAll" style="flex:1;padding:3px;border-radius:4px;border:1px solid var(--border);font-size:11px;cursor:pointer;background:var(--surface);color:var(--text)">All of</button>
    <span style="padding:3px;font-size:11px;color:var(--dim);cursor:pointer" onclick="clearPeopleFilter()">✕</span>
  </div>`;
  Object.entries(allPeople)
    .sort((a,b) => b[1] - a[1])
    .forEach(([name, count]) => {
      html += `<label style="display:flex;align-items:center;gap:6px;padding:4px 8px;font-size:12px;cursor:pointer;border-radius:4px;white-space:nowrap" onmouseover="this.style.background='var(--border)'" onmouseout="this.style.background=''">
        <input type="checkbox" value="${name}" onchange="updatePeopleFilter()" style="accent-color:var(--accent)">
        ${name} <span style="color:var(--dim)">(${count})</span>
      </label>`;
    });
  dd.innerHTML = html;
  // Init route layer
  routeLayer = L.layerGroup().addTo(map);

  // Populate year filter
  const years = [...new Set(trips.map(t => new Date(t.start).getFullYear()))].sort((a,b) => b-a);
  const yearDD = document.getElementById('yearDropdown');
  let yearHtml = '<label style="display:block;padding:4px 8px;font-size:12px;color:var(--dim);cursor:pointer" onclick="clearYearFilter()">Clear all</label>';
  years.forEach(y => {
    const count = trips.filter(t => new Date(t.start).getFullYear() === y).length;
    yearHtml += `<label style="display:flex;align-items:center;gap:6px;padding:3px 8px;font-size:12px;cursor:pointer;border-radius:4px" onmouseover="this.style.background='var(--border)'" onmouseout="this.style.background=''">
      <input type="checkbox" value="${y}" onchange="updateYearFilter()" style="accent-color:var(--accent)">
      ${y} <span style="color:var(--dim)">(${count})</span>
    </label>`;
  });
  yearDD.innerHTML = yearHtml;
}

function toggleYearDropdown() {
  const dd = document.getElementById('yearDropdown');
  dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
}
document.addEventListener('click', e => {
  const wrap = document.getElementById('yearFilterWrap');
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById('yearDropdown').style.display = 'none';
  }
});

function updateYearFilter() {
  const checks = document.querySelectorAll('#yearDropdown input[type=checkbox]');
  selectedYears = [...checks].filter(c => c.checked).map(c => parseInt(c.value));
  const btn = document.getElementById('yearFilterBtn');
  btn.textContent = selectedYears.length ? `📅 ${selectedYears.join(', ')}` : '📅 Years';
  btn.classList.toggle('active', selectedYears.length > 0);
  applyFilters();
}

function clearYearFilter() {
  document.querySelectorAll('#yearDropdown input[type=checkbox]').forEach(c => c.checked = false);
  updateYearFilter();
}

function togglePeopleDropdown() {
  const dd = document.getElementById('peopleDropdown');
  dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
}
// Close dropdown when clicking elsewhere
document.addEventListener('click', e => {
  const wrap = document.getElementById('peopleFilterWrap');
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById('peopleDropdown').style.display = 'none';
  }
});

function updatePeopleFilter() {
  const checks = document.querySelectorAll('#peopleDropdown input[type=checkbox]');
  selectedPeople = [...checks].filter(c => c.checked).map(c => c.value);
  const btn = document.getElementById('peopleFilterBtn');
  btn.textContent = selectedPeople.length ? `👥 ${selectedPeople.length} selected` : '👥 People';
  btn.classList.toggle('active', selectedPeople.length > 0);
  applyFilters();
}

function setPeopleMode(mode) {
  peopleFilterMode = mode;
  document.getElementById('modeAny').style.background = mode === 'any' ? 'var(--accent)' : 'var(--surface)';
  document.getElementById('modeAny').style.color = mode === 'any' ? 'var(--bg)' : 'var(--text)';
  document.getElementById('modeAll').style.background = mode === 'all' ? 'var(--accent)' : 'var(--surface)';
  document.getElementById('modeAll').style.color = mode === 'all' ? 'var(--bg)' : 'var(--text)';
  applyFilters();
}

function clearPeopleFilter() {
  document.querySelectorAll('#peopleDropdown input[type=checkbox]').forEach(c => c.checked = false);
  updatePeopleFilter();
}

function filterType(type, btn) {
  activeTypeFilter = type;
  document.querySelectorAll('.map-controls .map-btn').forEach(b => {
    if (['All','🏨 Stays','🚗 Road trips','📍 Day trips'].includes(b.textContent)) b.classList.remove('active');
  });
  if (btn) btn.classList.add('active');
  applyFilters();
}

function toggleSoloFilter(btn) {
  hideSoloTrips = !hideSoloTrips;
  btn.classList.toggle('active', hideSoloTrips);
  btn.textContent = hideSoloTrips ? '👤 Solo hidden' : '👤 Hide solo';
  applyFilters();
}

function applyFilters() {
  const filtered = trips.filter(t => {
    if (activeTypeFilter !== 'all' && t.trip_type !== activeTypeFilter) return false;
    if (selectedPeople.length > 0) {
      if (peopleFilterMode === 'all') {
        if (!selectedPeople.every(p => t.people.includes(p))) return false;
      } else {
        if (!selectedPeople.some(p => t.people.includes(p))) return false;
      }
    }
    if (hideSoloTrips && t.people.length === 0) return false;
    if (selectedYears.length > 0 && !selectedYears.includes(new Date(t.start).getFullYear())) return false;
    if (showOnlyUnreviewed && t.reviewed) return false;
    return true;
  });

  // Update markers — don't reset zoom
  markerLayer.clearLayers();
  if (routeLayer) routeLayer.clearLayers();

  filtered.forEach(t => {
    const color = t.family ? '#00cc88' : '#0088ff';
    const radius = Math.max(8, Math.min(16, t.photos / 10));
    const marker = L.circleMarker([t.lat, t.lon], {
      radius, color, fillColor: color, fillOpacity: 0.6, weight: 2
    }).addTo(markerLayer);

    const peopleHtml = t.people.length
      ? '<br>👥 ' + t.people.map(p => {
          const count = t.people_counts?.[p];
          return count ? `${p} <span style="opacity:0.6">(${count})</span>` : p;
        }).join(', ')
      : '';
    const familyBadge = t.family ? ' <span style="color:#00cc88">👨‍👩‍👧 Family</span>' : '';
    const typeBadge = t.trip_type === 'road trip' ? ' 🚗' : t.trip_type === 'day trip' ? ' 📍' : '';
    let stopsHtml = '';
    if (t.stops?.length > 1) {
      const stopNames = t.stops.filter(s => s.name).map(s => `<span style="opacity:0.8">${s.name}</span> (${s.photo_count})`);
      stopsHtml = `<br>📌 <b>${t.stops.length} stops:</b><br>${stopNames.join('<br>')}`;
    }
    marker.bindPopup(`<b>${t.name || 'Unknown'}${typeBadge}</b>${familyBadge}<br>${new Date(t.start).toLocaleDateString('en-GB', {month:'short',year:'numeric'})} — ${new Date(t.end).toLocaleDateString('en-GB', {month:'short',year:'numeric'})}<br>${t.days} days, ${t.photos} photos${stopsHtml}${peopleHtml}`, {maxWidth: 350});
    marker.on('click', () => { selectTrip(t.id); });
    marker.on('mouseover', function() { this.bringToFront(); });
    markers[t.id] = marker;

    // Show stop dots for trips with multiple stops
    // Only draw route lines between stops that are within driving distance
    if (t.stops && t.stops.length > 1 && routeLayer) {
      // Draw route segments only between nearby stops (< 300km apart)
      for (let i = 0; i < t.stops.length - 1; i++) {
        const s1 = t.stops[i], s2 = t.stops[i+1];
        const dist = haversineJS(s1.lat, s1.lon, s2.lat, s2.lon);
        if (dist < 300) {
          L.polyline([[s1.lat, s1.lon], [s2.lat, s2.lon]], {
            color, weight: 2, opacity: 0.4, dashArray: '6,4'
          }).addTo(routeLayer);
        }
      }
      // Small dots for each stop
      t.stops.forEach(s => {
        L.circleMarker([s.lat, s.lon], {
          radius: 3, color, fillColor: color, fillOpacity: 0.5, weight: 1
        }).addTo(routeLayer);
      });
    }
  });

  // Update trip cards
  const bar = document.getElementById('tripsBar');
  const filteredIds = new Set(filtered.map(t => t.id));
  bar.querySelectorAll('.trip-card').forEach(card => {
    card.style.display = filteredIds.has(parseInt(card.dataset.id)) ? '' : 'none';
  });

  // Update stats
  document.getElementById('tripCount').textContent = filtered.length;
  document.getElementById('countryCount').textContent = [...new Set(filtered.map(t => t.country).filter(Boolean))].length;

  // Update timeline
  renderTimeline(filtered);
}

function showStops(trip) {
  // Highlight stops when clicking a trip
  if (!trip.stops || trip.stops.length <= 1) return;
}

function renderTimeline(filteredTrips) {
  const container = document.getElementById('timelineContent');
  if (!filteredTrips) filteredTrips = trips;

  const byYear = {};
  filteredTrips.forEach(t => {
    const y = new Date(t.start).getFullYear();
    if (!byYear[y]) byYear[y] = [];
    byYear[y].push(t);
  });

  let html = '';
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  Object.keys(byYear).sort((a,b) => b-a).forEach(year => {
    const yearTrips = byYear[year].sort((a,b) => new Date(a.start) - new Date(b.start));
    html += `<div style="margin-bottom:20px">
      <div style="font-size:15px;font-weight:700;color:var(--accent);margin-bottom:8px;position:sticky;top:0;background:var(--bg);padding:4px 0">${year} <span style="font-size:12px;font-weight:400;color:var(--dim)">${yearTrips.length} trips</span></div>`;

    yearTrips.forEach(t => {
      const month = months[new Date(t.start).getMonth()];
      const color = t.family ? 'var(--accent)' : 'var(--accent2)';
      const typIcon = t.trip_type === 'road trip' ? '🚗' : t.trip_type === 'day trip' ? '📍' : '🏨';
      const peopleChips = t.people.slice(0, 4).map(p =>
        `<span style="display:inline-block;padding:1px 6px;border-radius:10px;font-size:10px;background:${t.family ? 'rgba(0,204,136,0.15)' : 'rgba(0,136,255,0.15)'};color:${color};margin-right:3px">${p}</span>`
      ).join('');

      html += `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);cursor:pointer" onclick="selectTrip(${t.id})">
        <div style="min-width:36px;font-size:12px;color:var(--dim);padding-top:2px">${month}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
            ${typIcon} ${t.name || 'Unknown'}
          </div>
          <div style="font-size:11px;color:var(--dim);margin-top:2px">
            ${t.days}d · ${t.photos} photos${t.stops?.length > 1 ? ` · ${t.stops.length} stops` : ''}
          </div>
          ${peopleChips ? `<div style="margin-top:3px">${peopleChips}</div>` : ''}
        </div>
      </div>`;
    });

    html += '</div>';
  });

  container.innerHTML = html || '<p style="color:var(--dim)">No trips match filters</p>';
}

// === PLANNER ===
let currentPlanView = 'planner';

function showPlanView(view) {
  currentPlanView = view;
  document.getElementById('planViewPlanner').classList.toggle('active', view === 'planner');
  document.getElementById('planViewCalendar').classList.toggle('active', view === 'calendar');
  document.getElementById('planViewSaved').classList.toggle('active', view === 'saved');
  if (view === 'planner') {
    if (plannerState === 'setup') renderPlanSetup();
    else if (plannerState === 'result' && currentPlan) renderPlanResult(currentPlan);
  } else if (view === 'calendar') {
    renderCalendar();
  } else if (view === 'saved') {
    renderSavedPlans();
  }
}

function renderPlanSetup() {
  plannerState = 'setup';
  const countries = [...new Set(trips.map(t => t.country).filter(Boolean))].sort();
  const countryOptions = countries.map(c => `<option value="${c}">${c}</option>`).join('') +
    '<option value="__new__">Somewhere new...</option>';

  const interests = ['Beach', 'Nature', 'Food', 'Culture', 'Adventure', 'Relaxation', 'City', 'History'];
  const interestChips = interests.map(i =>
    `<span class="plan-interest" onclick="this.classList.toggle('selected')">${i}</span>`
  ).join('');

  const people = ['Jon', 'Anne', 'Clara', 'Zoë', 'Ethan'];
  const whoChecks = people.map(p =>
    `<label><input type="checkbox" name="plan-who" value="${p}" checked> <span>${p}</span></label>`
  ).join('');

  const budgetRadios = ['Low', 'Medium', 'High', 'Luxury'].map((b, i) =>
    `<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;cursor:pointer;margin-right:12px">
      <input type="radio" name="plan-budget" value="${b.toLowerCase()}" ${i === 1 ? 'checked' : ''} style="accent-color:var(--accent)"> ${b}
    </label>`
  ).join('');

  document.getElementById('planContent').innerHTML = `
    <div class="plan-form">
      <label>Country / Region</label>
      <select id="planCountry">${countryOptions}</select>

      <label>Who's going?</label>
      <div class="plan-who">${whoChecks}</div>

      <label>Interests</label>
      <div class="plan-interests">${interestChips}</div>

      <label>Budget</label>
      <div>${budgetRadios}</div>

      <label>Start date</label>
      <input type="date" id="planStart">

      <label>End date</label>
      <input type="date" id="planEnd">

      <label>Max driving per day (hours)</label>
      <input type="number" id="planMaxDrive" value="4" min="1" max="10">

      <label>Notes</label>
      <textarea id="planNotes" class="inline-notes" placeholder="e.g. avoid motorways, must see the coast..."></textarea>

      <button class="generate-btn" onclick="generateRoadTrip()">Generate Route</button>
    </div>
  `;
}

async function generateRoadTrip() {
  const country = document.getElementById('planCountry').value;
  const countryLabel = country === '__new__' ? 'somewhere new' : country;
  const who = [...document.querySelectorAll('input[name="plan-who"]:checked')].map(c => c.value);
  const interests = [...document.querySelectorAll('.plan-interest.selected')].map(c => c.textContent);
  const budget = document.querySelector('input[name="plan-budget"]:checked')?.value || 'medium';
  const startDate = document.getElementById('planStart').value;
  const endDate = document.getElementById('planEnd').value;
  const maxDrive = document.getElementById('planMaxDrive').value;
  const notes = document.getElementById('planNotes').value;

  renderPlanLoading(countryLabel);

  try {
    const resp = await fetch('api/roadtrip/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ country, who, interests, budget, start_date: startDate, end_date: endDate, max_drive_hours: maxDrive, notes })
    });
    if (!resp.ok) throw new Error('Failed to generate route');
    const plan = await resp.json();
    currentPlan = plan;
    plannerState = 'result';
    renderPlanResult(plan);
  } catch(e) {
    document.getElementById('planContent').innerHTML = `
      <div style="padding:20px;text-align:center">
        <div style="font-size:24px;margin-bottom:8px">😕</div>
        <div style="color:var(--warn);margin-bottom:12px">Failed to generate route</div>
        <div style="font-size:12px;color:var(--dim);margin-bottom:16px">${e.message}</div>
        <button class="generate-btn" style="width:auto;padding:8px 20px" onclick="renderPlanSetup()">← Try again</button>
      </div>
    `;
  }
}

function renderPlanLoading(country) {
  const countryTrips = trips.filter(t => t.country && t.country.toLowerCase() === country.toLowerCase());
  const context = countryTrips.length
    ? `Checking ${countryTrips.length} place${countryTrips.length > 1 ? 's' : ''} you've been in ${country}...`
    : `Exploring fresh territory in ${country}...`;

  document.getElementById('planContent').innerHTML = `
    <div class="plan-loading">
      <div class="pulse" style="font-size:48px;margin-bottom:16px">🗺️</div>
      <div style="font-size:16px;font-weight:600;margin-bottom:8px">Planning your ${country} road trip...</div>
      <div style="font-size:12px;color:var(--dim)">${context}</div>
    </div>
  `;
}

function renderPlanResult(plan) {
  plannerState = 'result';
  const stops = plan.stops || [];

  let stopsHtml = '<div style="padding:16px 16px 0">';
  stopsHtml += `<h3 style="margin-bottom:4px">🗺️ ${plan.title || plan.country + ' Road Trip'}</h3>`;
  stopsHtml += `<div style="font-size:12px;color:var(--dim);margin-bottom:16px">${plan.summary || ''}</div>`;

  stops.forEach((stop, i) => {
    const driveInfo = i > 0 && stop.drive_time ? `<div class="drive-info">🚗 ${stop.drive_time} from ${stops[i-1].name}</div>` : '';
    const activities = stop.activities ? `<div class="activities">${stop.activities.map(a => `• ${a}`).join('<br>')}</div>` : '';
    const why = stop.why ? `<div class="why">"${stop.why}"</div>` : '';
    const similar = stop.similar_to ? `<div class="similar">Similar to your trip to ${stop.similar_to}</div>` : '';
    const actions = `<div class="stop-actions">
      <button onclick="map.setView([${stop.lat},${stop.lon}],12)">📍 Show</button>
      <button onclick="window.open('https://www.google.com/maps/search/?api=1&query=${stop.lat},${stop.lon}','_blank')">🗺️ Google Maps</button>
    </div>`;

    stopsHtml += `<div class="route-stop" data-day="${i + 1}">
      <h4>${stop.name}</h4>
      ${driveInfo}
      ${activities}
      ${why}
      ${similar}
      ${actions}
    </div>`;
  });

  stopsHtml += `<div style="display:flex;gap:8px;margin-top:20px;padding-bottom:16px">
    <button class="generate-btn" style="flex:1" onclick="savePlan()">💾 Save plan</button>
    <button class="generate-btn" style="flex:1;background:var(--accent2)" onclick="generateRoadTrip()">🔄 Regenerate</button>
  </div>`;
  stopsHtml += `<div style="padding-bottom:16px">
    <button class="generate-btn" style="background:var(--surface);color:var(--accent);border:1px solid var(--accent)" onclick="refineInChat()">💬 Refine in chat</button>
  </div>`;
  stopsHtml += `<div style="padding-bottom:16px">
    <button class="generate-btn" style="background:var(--surface);color:var(--dim);border:1px solid var(--border)" onclick="renderPlanSetup()">← New plan</button>
  </div>`;
  stopsHtml += '</div>';

  document.getElementById('planContent').innerHTML = stopsHtml;

  if (stops.length) {
    drawPlannedRoute(stops);
    if (plan.country) drawExclusionZones(plan.country);
  }
}

function refineInChat() {
  if (!currentPlan) return;
  switchTab('chat');
  const stopNames = (currentPlan.stops || []).map(s => s.name).join(', ');
  const msg = `I have a road trip plan for ${currentPlan.country || 'a trip'} with stops: ${stopNames}. Can you help me refine it?`;
  document.getElementById('chatInput').value = msg;
  sendChat();
}

function drawPlannedRoute(stops) {
  clearPlannedRoute();
  plannedRouteLayer = L.layerGroup().addTo(map);

  // Draw polyline
  const coords = stops.map(s => [s.lat, s.lon]);
  L.polyline(coords, { color: '#00cc88', weight: 4, opacity: 0.8 }).addTo(plannedRouteLayer);

  // Numbered markers
  stops.forEach((s, i) => {
    L.circleMarker([s.lat, s.lon], {
      radius: 12, color: '#00cc88', fillColor: '#00cc88', fillOpacity: 1, weight: 0
    }).bindTooltip(`${i + 1}`, { permanent: true, direction: 'center', className: 'stop-number' })
      .bindPopup(`<b>${s.name}</b>${s.why ? '<br><em>' + s.why + '</em>' : ''}`)
      .addTo(plannedRouteLayer);
  });

  // Dim existing trip markers
  Object.values(markers).forEach(m => { m.setStyle({ fillOpacity: 0.3, opacity: 0.3 }); });

  // Fit bounds
  if (coords.length) map.fitBounds(coords, { padding: [40, 40] });

  // Add clear button
  if (!document.getElementById('clearPlanBtn')) {
    const btn = document.createElement('button');
    btn.id = 'clearPlanBtn';
    btn.className = 'map-btn';
    btn.textContent = '✕ Clear plan';
    btn.style.background = 'var(--warn)';
    btn.style.color = 'white';
    btn.style.borderColor = 'var(--warn)';
    btn.onclick = clearPlannedRoute;
    document.querySelector('.map-controls').appendChild(btn);
  }
}

function drawExclusionZones(country) {
  if (exclusionZoneLayer) map.removeLayer(exclusionZoneLayer);
  exclusionZoneLayer = L.layerGroup().addTo(map);

  trips.forEach(t => {
    if (t.country && t.country.toLowerCase() === country.toLowerCase()) {
      const visitDate = new Date(t.end).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
      L.circle([t.lat, t.lon], {
        radius: 50000, color: '#888', fillColor: '#888', fillOpacity: 0.15, weight: 1, dashArray: '4,4'
      }).bindTooltip(`Visited ${visitDate}`, { direction: 'top' })
        .addTo(exclusionZoneLayer);
    }
  });
}

function clearPlannedRoute() {
  if (plannedRouteLayer) { map.removeLayer(plannedRouteLayer); plannedRouteLayer = L.layerGroup(); }
  if (exclusionZoneLayer) { map.removeLayer(exclusionZoneLayer); exclusionZoneLayer = L.layerGroup(); }

  // Restore marker opacity
  Object.values(markers).forEach(m => { m.setStyle({ fillOpacity: 0.6, opacity: 1 }); });

  // Remove clear button
  const btn = document.getElementById('clearPlanBtn');
  if (btn) btn.remove();
}

async function savePlan() {
  if (!currentPlan) return;
  try {
    const resp = await fetch('api/roadtrip/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(currentPlan)
    });
    if (resp.ok) {
      const saveBtn = document.querySelector('.generate-btn');
      if (saveBtn && saveBtn.textContent.includes('Save')) {
        saveBtn.textContent = '✓ Saved!';
        saveBtn.disabled = true;
        setTimeout(() => { saveBtn.textContent = '💾 Save plan'; saveBtn.disabled = false; }, 2000);
      }
    }
  } catch(e) {
    alert('Failed to save plan');
  }
}

async function renderCalendar() {
  const container = document.getElementById('planContent');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // Build trip months lookup
  const tripMonths = {};
  trips.forEach(t => {
    const start = new Date(t.start);
    const end = new Date(t.end);
    let d = new Date(start.getFullYear(), start.getMonth(), 1);
    while (d <= end) {
      const key = `${d.getFullYear()}-${d.getMonth()}`;
      if (!tripMonths[key]) tripMonths[key] = [];
      tripMonths[key].push(t.name || t.country);
      d.setMonth(d.getMonth() + 1);
    }
  });

  // School holidays (approximate UK)
  const schoolHolidays = {
    0: 'Winter half-term', 3: 'Easter', 4: 'May half-term',
    6: 'Summer', 7: 'Summer', 9: 'October half-term', 11: 'Christmas'
  };

  const years = [...new Set(trips.map(t => new Date(t.start).getFullYear()))].sort();
  const minYear = Math.min(...years, new Date().getFullYear());
  const maxYear = Math.max(...years, new Date().getFullYear() + 1);

  let html = '<div style="padding:12px">';
  html += '<div style="font-size:13px;color:var(--dim);margin-bottom:8px">🟢 Trip &nbsp; 🟡 School holiday &nbsp; 🔵 Available gap</div>';

  for (let y = maxYear; y >= minYear; y--) {
    html += `<div class="cal-year-label">${y}</div>`;
    html += '<div class="cal-grid">';
    for (let m = 0; m < 12; m++) {
      const key = `${y}-${m}`;
      const hasTrip = tripMonths[key];
      const isHoliday = schoolHolidays[m];
      const isGap = !hasTrip && isHoliday;
      const isPast = new Date(y, m + 1, 0) < new Date();

      let cls = 'cal-month';
      let title = months[m];
      if (hasTrip) { cls += ' has-trip'; title += ': ' + hasTrip.join(', '); }
      else if (isGap && !isPast) { cls += ' gap'; title += ' — ' + isHoliday + ' (available)'; }
      else if (isHoliday && !isPast) { cls += ' holiday'; title += ' — ' + isHoliday; }

      const clickHandler = isGap && !isPast
        ? ` onclick="prefillPlanDates(${y},${m})"`
        : '';

      html += `<div class="${cls}" title="${title}"${clickHandler}>${months[m]}</div>`;
    }
    html += '</div>';
  }
  html += '</div>';

  container.innerHTML = html;
}

function prefillPlanDates(year, month) {
  showPlanView('planner');
  setTimeout(() => {
    const startEl = document.getElementById('planStart');
    const endEl = document.getElementById('planEnd');
    if (startEl) {
      const start = new Date(year, month, 1);
      const end = new Date(year, month + 1, 0);
      startEl.value = start.toISOString().split('T')[0];
      endEl.value = end.toISOString().split('T')[0];
    }
  }, 50);
}

async function renderSavedPlans() {
  const container = document.getElementById('planContent');
  container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--dim)">Loading saved plans...</div>';

  try {
    const resp = await fetch('api/roadtrip/plans');
    if (!resp.ok) throw new Error('No saved plans');
    const plans = await resp.json();

    if (!plans.length) {
      container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--dim)">No saved plans yet. Generate a road trip to get started!</div>';
      return;
    }

    let html = '<div style="padding:12px">';
    plans.forEach((plan, i) => {
      const stopCount = plan.stops?.length || 0;
      const dateStr = plan.start_date ? new Date(plan.start_date).toLocaleDateString('en-GB', {month:'short', year:'numeric'}) : '';
      html += `<div class="saved-plan" onclick="loadSavedPlan(${i})">
        <h4>${plan.title || plan.country || 'Road Trip'}</h4>
        <div class="meta">${dateStr}${dateStr ? ' · ' : ''}${stopCount} stops${plan.who ? ' · ' + plan.who.join(', ') : ''}</div>
      </div>`;
    });
    html += '</div>';
    container.innerHTML = html;

    // Store for loading
    window._savedPlans = plans;
  } catch(e) {
    container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--dim)">No saved plans yet. Generate a road trip to get started!</div>';
  }
}

function loadSavedPlan(index) {
  const plans = window._savedPlans;
  if (!plans || !plans[index]) return;
  currentPlan = plans[index];
  plannerState = 'result';
  showPlanView('planner');
  renderPlanResult(currentPlan);
}

// === BOOT ===
init();
</script>
</body>
</html>
"""


def run_web(host="127.0.0.1", port=5555, trips_file=None, debug=False):
    """Start the web server."""
    if trips_file:
        global TRIPS_FILE
        TRIPS_FILE = Path(trips_file)

    load_state()
    print(f"\n🌍 Wanderlust running at http://{host}:{port}\n")
    app.run(host=host, port=port, debug=debug)
