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
from pathlib import Path
from datetime import datetime
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
            is_family_trip=d.get("is_family_trip", False),
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
            "family": t.is_family_trip,
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


@app.route("/api/trip/<int:trip_id>/review", methods=["POST"])
def api_save_review(trip_id):
    data = request.json
    STATE["reviews"][str(trip_id)] = data
    save_reviews()
    return jsonify({"ok": True})


@app.route("/api/exclusions")
def api_exclusions():
    return jsonify(get_exclusion_zones())


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
6. Consider their kids (ages 4, 7, 8) for family trips
7. Factor in their travel patterns (seasons, duration, style)

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
            "Set OPENAI_API_KEY or start Ollama to get personalised recommendations. "
            "In the meantime, review your trips and rate them — that helps me learn your preferences!")


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
    --accent2: #0088ff; --warn: #ff6644; --radius: 12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }

  .app { display: grid; grid-template-columns: 1fr 400px; grid-template-rows: auto 1fr; height: 100vh; }

  .header { grid-column: 1 / -1; padding: 16px 24px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 20px; }
  .header h1 span { color: var(--accent); }
  .header .stats { margin-left: auto; display: flex; gap: 24px; font-size: 13px; color: var(--dim); }
  .header .stats b { color: var(--accent); font-size: 16px; }

  .main { display: flex; flex-direction: column; overflow: hidden; }

  #map { flex: 1; min-height: 300px; }

  .trips-bar { padding: 12px; border-top: 1px solid var(--border); overflow-x: auto;
               display: flex; gap: 8px; flex-shrink: 0; }
  .trip-card { flex-shrink: 0; background: var(--surface); border: 1px solid var(--border);
               border-radius: var(--radius); padding: 10px 14px; cursor: pointer; min-width: 150px;
               transition: border-color 0.2s; }
  .trip-card:hover, .trip-card.active { border-color: var(--accent); }
  .trip-card .name { font-weight: 600; font-size: 14px; white-space: nowrap; }
  .trip-card .meta { font-size: 12px; color: var(--dim); margin-top: 2px; }
  .trip-card .rating { color: #ffd700; font-size: 12px; }
  .trip-card .unreviewed { color: var(--warn); font-size: 11px; }

  .sidebar { border-left: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }

  .tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .tab { flex: 1; padding: 12px; text-align: center; font-size: 13px; font-weight: 500;
         cursor: pointer; border-bottom: 2px solid transparent; color: var(--dim); transition: all 0.2s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .panel { flex: 1; overflow-y: auto; display: none; }
  .panel.active { display: flex; flex-direction: column; }

  /* Chat panel */
  .chat-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 90%; padding: 10px 14px; border-radius: var(--radius); font-size: 14px; line-height: 1.5; }
  .msg.user { align-self: flex-end; background: var(--accent2); color: white; }
  .msg.ai { align-self: flex-start; background: var(--surface); border: 1px solid var(--border); }
  .msg.ai .thinking { color: var(--dim); font-style: italic; }

  .chat-input { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); flex-shrink: 0; }
  .chat-input input { flex: 1; background: var(--surface); border: 1px solid var(--border);
                       border-radius: 8px; padding: 10px 14px; color: var(--text); font-size: 14px; outline: none; }
  .chat-input input:focus { border-color: var(--accent); }
  .chat-input button { background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
                        padding: 10px 16px; font-weight: 600; cursor: pointer; }
  .chat-input button:disabled { opacity: 0.5; cursor: default; }

  /* Review panel */
  .review-content { padding: 16px; }
  .review-content h3 { margin-bottom: 12px; }
  .question { margin-bottom: 20px; }
  .question label { display: block; font-size: 14px; font-weight: 500; margin-bottom: 8px; }
  .stars { display: flex; gap: 4px; }
  .star { font-size: 24px; cursor: pointer; color: var(--border); transition: color 0.2s; }
  .star.filled { color: #ffd700; }
  .star:hover { color: #ffd700; }
  .options { display: flex; flex-wrap: wrap; gap: 6px; }
  .opt { padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border);
         font-size: 13px; cursor: pointer; transition: all 0.2s; user-select: none; }
  .opt.selected { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  .opt:hover { border-color: var(--accent); }
  textarea.review-text { width: 100%; background: var(--surface); border: 1px solid var(--border);
                          border-radius: 8px; padding: 10px; color: var(--text); font-size: 14px;
                          resize: vertical; min-height: 60px; outline: none; }
  .save-btn { background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
              padding: 10px 20px; font-weight: 600; cursor: pointer; margin-top: 12px; }

  /* Profile panel */
  .profile-content { padding: 16px; }
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
  .stat-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
              padding: 12px; text-align: center; }
  .stat-box .value { font-size: 24px; font-weight: 700; color: var(--accent); }
  .stat-box .label { font-size: 12px; color: var(--dim); margin-top: 4px; }

  .quick-prompts { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-top: 1px solid var(--border); flex-shrink: 0; }
  .quick-prompt { padding: 4px 10px; border-radius: 16px; border: 1px solid var(--border);
                  font-size: 12px; cursor: pointer; color: var(--dim); transition: all 0.2s; }
  .quick-prompt:hover { border-color: var(--accent); color: var(--text); }
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
    </div>
  </div>

  <div class="main">
    <div id="map"></div>
    <div class="trips-bar" id="tripsBar"></div>
  </div>

  <div class="sidebar">
    <div class="tabs">
      <div class="tab active" data-panel="chat">💬 Ask</div>
      <div class="tab" data-panel="review">⭐ Review</div>
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

    <div class="panel" id="panel-review">
      <div class="review-content" id="reviewContent">
        <p style="color:var(--dim);padding:20px;text-align:center">
          Click a trip on the map or in the bar below to review it
        </p>
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
<script>
// === STATE ===
let trips = [];
let chatHistory = [];
let selectedTrip = null;
let map, markers = {};

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

  // Add markers
  const bounds = [];
  trips.forEach(t => {
    const color = t.family ? '#00cc88' : '#0088ff';
    const radius = Math.max(6, Math.min(18, t.photos / 8));
    const marker = L.circleMarker([t.lat, t.lon], {
      radius, color, fillColor: color, fillOpacity: 0.6, weight: 2
    }).addTo(map);

    const rating = t.rating ? '⭐'.repeat(t.rating) : '<span style="color:#ff6644">Not reviewed</span>';
    marker.bindPopup(`<b>${t.name || 'Unknown'}</b><br>${new Date(t.start).toLocaleDateString('en-GB', {month:'short',year:'numeric'})}<br>${t.days} days, ${t.photos} photos<br>${rating}`);
    marker.on('click', () => selectTrip(t.id));
    markers[t.id] = marker;
    bounds.push([t.lat, t.lon]);
  });

  if (bounds.length) map.fitBounds(bounds, { padding: [30, 30] });

  // Trip cards
  renderTripCards();

  // Load profile
  loadProfile();
}

function renderTripCards() {
  const bar = document.getElementById('tripsBar');
  bar.innerHTML = trips.map(t => {
    const rating = t.rating ? '<span class="rating">' + '★'.repeat(t.rating) + '☆'.repeat(5-t.rating) + '</span>'
                            : '<span class="unreviewed">⚠ Not reviewed</span>';
    return `<div class="trip-card" data-id="${t.id}" onclick="selectTrip(${t.id})">
      <div class="name">${t.name || 'Unknown'}</div>
      <div class="meta">${new Date(t.start).toLocaleDateString('en-GB', {month:'short',year:'numeric'})} · ${t.days}d</div>
      ${rating}
    </div>`;
  }).join('');
}

function selectTrip(id) {
  selectedTrip = trips.find(t => t.id === id);
  if (!selectedTrip) return;

  // Highlight card
  document.querySelectorAll('.trip-card').forEach(c => c.classList.toggle('active', parseInt(c.dataset.id) === id));

  // Pan map
  map.setView([selectedTrip.lat, selectedTrip.lon], 6);
  markers[id]?.openPopup();

  // Switch to review tab
  switchTab('review');
  loadReview(id);
}

async function loadReview(tripId) {
  const resp = await fetch(`api/trip/${tripId}/questions`);
  const data = await resp.json();
  const container = document.getElementById('reviewContent');
  const existing = data.existing_review || {};

  let html = `<h3>Review: ${data.trip.name}</h3>`;

  data.questions.forEach(q => {
    html += `<div class="question">`;
    html += `<label>${q.text}</label>`;

    if (q.type === 'rating') {
      const val = existing[q.id] || 0;
      html += `<div class="stars" data-qid="${q.id}">`;
      for (let i = 1; i <= 5; i++) {
        html += `<span class="star ${i <= val ? 'filled' : ''}" data-val="${i}" onclick="setRating('${q.id}',${i})">★</span>`;
      }
      html += `</div>`;
    } else if (q.type === 'multi_select') {
      const selected = existing[q.id] || [];
      html += `<div class="options" data-qid="${q.id}">`;
      q.options.forEach(opt => {
        html += `<span class="opt ${selected.includes(opt) ? 'selected' : ''}" onclick="toggleOpt(this,'${q.id}')">${opt}</span>`;
      });
      html += `</div>`;
    } else if (q.type === 'select') {
      const selected = existing[q.id] || '';
      html += `<div class="options" data-qid="${q.id}">`;
      q.options.forEach(opt => {
        html += `<span class="opt ${selected === opt ? 'selected' : ''}" onclick="selectOne(this,'${q.id}')">${opt}</span>`;
      });
      html += `</div>`;
    } else if (q.type === 'text') {
      html += `<textarea class="review-text" data-qid="${q.id}" placeholder="Optional notes...">${existing[q.id] || ''}</textarea>`;
    }

    html += `</div>`;
  });

  html += `<button class="save-btn" onclick="saveReview(${tripId})">Save Review</button>`;
  container.innerHTML = html;
}

function setRating(qid, val) {
  const stars = document.querySelector(`.stars[data-qid="${qid}"]`);
  stars.querySelectorAll('.star').forEach(s => {
    s.classList.toggle('filled', parseInt(s.dataset.val) <= val);
  });
  stars.dataset.value = val;
}

function toggleOpt(el, qid) {
  el.classList.toggle('selected');
}

function selectOne(el, qid) {
  el.parentElement.querySelectorAll('.opt').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}

async function saveReview(tripId) {
  const review = {};
  document.querySelectorAll('.stars[data-qid]').forEach(el => {
    review[el.dataset.qid] = parseInt(el.dataset.value) || 0;
  });
  document.querySelectorAll('.options[data-qid]').forEach(el => {
    const selected = [...el.querySelectorAll('.opt.selected')].map(o => o.textContent);
    const qid = el.dataset.qid;
    // Check if multi or single select
    const isMulti = el.querySelectorAll('.opt.selected').length > 1 || el.closest('.question').querySelector('label').textContent.includes('highlights') || el.closest('.question').querySelector('label').textContent.includes('best for');
    review[qid] = isMulti ? selected : (selected[0] || '');
  });
  document.querySelectorAll('textarea[data-qid]').forEach(el => {
    if (el.value.trim()) review[el.dataset.qid] = el.value.trim();
  });

  await fetch(`api/trip/${tripId}/review`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(review)
  });

  // Update trip state
  const trip = trips.find(t => t.id === tripId);
  if (trip) { trip.reviewed = true; trip.rating = review.overall; }
  document.getElementById('reviewCount').textContent = trips.filter(t => t.reviewed).length;
  renderTripCards();

  // Flash save confirmation
  const btn = document.querySelector('.save-btn');
  btn.textContent = '✓ Saved!';
  btn.style.background = '#00cc88';
  setTimeout(() => { btn.textContent = 'Save Review'; }, 2000);
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
  addChatMessage('ai', '<span class="thinking">Thinking about destinations...</span>', 'thinking-msg');

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
  div.innerHTML = content;
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
