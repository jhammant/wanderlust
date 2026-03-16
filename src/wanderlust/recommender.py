"""
Recommender — AI-powered holiday recommendations based on your travel profile.

Supports:
- OpenAI (GPT-4o)
- Google Gemini
- Ollama (local, private)
- Manual mode (just shows your profile, you think of ideas)

The prompt includes your full travel DNA and asks for personalised,
reasoned recommendations — not generic "visit Paris" stuff.
"""

import json
from typing import Optional

from .profiler import TravelProfile
from .clusterer import Trip


def build_recommendation_prompt(
    profile: TravelProfile,
    trips: list[Trip],
    constraints: Optional[dict] = None,
) -> str:
    """
    Build the recommendation prompt from travel profile and trip history.

    constraints can include:
        budget: "low" | "medium" | "high" | "luxury"
        max_flight_hours: int
        kids_ages: list[int]
        interests: list[str]  e.g. ["beach", "culture", "food", "adventure"]
        avoid: list[str]  e.g. ["long flights", "extreme heat"]
        time_of_year: str  e.g. "February half term"
        duration_days: int
    """
    # Build trip history summary
    trip_summaries = []
    for t in trips:
        people_str = f" (with: {', '.join(t.people[:3])})" if t.people else ""
        trip_summaries.append(
            f"- {t.place_name or 'Unknown'}: {t.start_date.strftime('%b %Y')}, "
            f"{t.duration_days} days, {t.photo_count} photos{people_str}"
        )

    trip_history = "\n".join(trip_summaries) if trip_summaries else "No trips discovered yet."

    prompt = f"""You are a travel advisor with deep knowledge of destinations worldwide.
Your client has the following travel profile, built from {profile.total_trips} trips
spanning {profile.date_range}:

**Travel DNA:**
- Average trip: {profile.avg_trip_days:.0f} days
- Preferred seasons: {', '.join(profile.preferred_seasons) if profile.preferred_seasons else 'No strong preference'}
- Countries visited: {', '.join(profile.countries_visited) if profile.countries_visited else 'Unknown'}
- Domestic vs international: {profile.domestic_pct:.0f}% domestic
- Typical distance: {profile.avg_distance_km:.0f}km from home (max: {profile.max_distance_km:.0f}km — {profile.farthest_trip})
- Travel style: {profile.preferences.get('style', 'unknown')}
- Adventurousness: {profile.preferences.get('adventurousness', 'unknown')}
- Trip length preference: {profile.preferences.get('range', 'unknown')} range
- Repeat destinations: {', '.join(profile.repeat_destinations) if profile.repeat_destinations else 'None — always somewhere new'}
- Most travelled with: {', '.join(profile.most_travelled_with[:3]) if profile.most_travelled_with else 'Unknown'}
- Family trip rate: {profile.family_trip_pct:.0f}%

**Trip History:**
{trip_history}

**Home base:** London, UK
"""

    if constraints:
        prompt += "\n**Current constraints:**\n"
        if constraints.get("budget"):
            prompt += f"- Budget: {constraints['budget']}\n"
        if constraints.get("max_flight_hours"):
            prompt += f"- Max flight time: {constraints['max_flight_hours']} hours\n"
        if constraints.get("kids_ages"):
            prompt += f"- Travelling with kids ages: {', '.join(str(a) for a in constraints['kids_ages'])}\n"
        if constraints.get("interests"):
            prompt += f"- Interests: {', '.join(constraints['interests'])}\n"
        if constraints.get("avoid"):
            prompt += f"- Avoid: {', '.join(constraints['avoid'])}\n"
        if constraints.get("time_of_year"):
            prompt += f"- When: {constraints['time_of_year']}\n"
        if constraints.get("duration_days"):
            prompt += f"- Duration: {constraints['duration_days']} days\n"

    prompt += """
Based on this real travel history, recommend **5 destinations** they haven't been to.

For each recommendation:
1. **Destination** — specific place, not just a country
2. **Why it fits** — connect to their actual travel patterns (reference specific past trips)
3. **Best time to go** — based on their seasonal preferences
4. **Trip style** — how they'd enjoy it (family activities, food, culture, etc.)
5. **Similar to** — which of their past trips it's most like (and why)
6. **Wildcard factor** — one thing about this place they wouldn't expect

Be specific. Reference their actual history. No generic tourism brochure copy.
Surprise them with at least one unexpected pick.
"""

    return prompt


def recommend_with_openai(prompt: str, api_key: str, model: str = "gpt-4o") -> str:
    """Get recommendations via OpenAI API."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=2000,
    )
    return response.choices[0].message.content


def recommend_with_ollama(prompt: str, model: str = "llama3.2:3b", base_url: str = "http://localhost:11434") -> str:
    """Get recommendations via local Ollama."""
    import requests
    response = requests.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]


def recommend_manual(
    profile: TravelProfile,
    trips: list[Trip],
) -> str:
    """Return a formatted profile summary for manual recommendation."""
    lines = [
        "═" * 60,
        "  YOUR TRAVEL DNA",
        "═" * 60,
        "",
        f"  🗓  {profile.total_trips} trips over {profile.date_range}",
        f"  📸  {profile.total_photos} photos across {profile.total_days_away} days away",
        f"  🌍  {len(profile.countries_visited)} countries: {', '.join(profile.countries_visited)}",
        f"  ✈️  Average distance: {profile.avg_distance_km:.0f}km (farthest: {profile.farthest_trip})",
        f"  👨‍👩‍👧‍👦  {profile.family_trip_pct:.0f}% family trips",
        f"  🔄  Repeat destinations: {', '.join(profile.repeat_destinations) or 'None — always exploring'}",
        "",
        "  Preferences:",
        f"    Season: {', '.join(profile.preferred_seasons)}",
        f"    Style: {profile.preferences.get('style', '?')}",
        f"    Range: {profile.preferences.get('range', '?')}",
        f"    Adventurousness: {profile.preferences.get('adventurousness', '?')}",
        "",
        "═" * 60,
    ]
    return "\n".join(lines)
