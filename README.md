# 🌍 Wanderlust

**Your photos already know where you should go next.**

Wanderlust scans your Apple Photos library to discover your travel history, then uses AI to recommend your next perfect holiday — based on where you've actually been, who you travel with, and what patterns emerge from your real life.

## How It Works

1. **Scan** — Reads your macOS Photos library metadata (GPS, dates, faces)
2. **Discover** — Clusters photos into trips (multi-day, away from home, family present)
3. **Profile** — Builds your travel DNA: seasons, durations, destinations, travel style
4. **Recommend** — AI-powered suggestions for new destinations you'll love

## Quick Start

```bash
# Install
pip install -e .

# Scan your photo library (read-only, nothing leaves your machine)
wanderlust scan

# See your discovered trips
wanderlust trips

# Get recommendations
wanderlust recommend

# Interactive exploration
wanderlust explore
```

## Privacy First

- **100% local** — your photo data never leaves your machine
- **Read-only** — we never modify your Photos library
- **No cloud** — AI runs locally (Ollama) or uses your own API keys
- Metadata only — we read GPS coords and timestamps, never pixel data

## Requirements

- macOS (reads Apple Photos SQLite database)
- Python 3.10+
- Optional: Ollama (local AI) or OpenAI/Gemini API key for recommendations

## Architecture

```
Photos.sqlite ──→ Scanner ──→ Trip Clusterer ──→ Travel Profile ──→ AI Recommender
                    │              │                    │                  │
              GPS coords    Group by location     Preferences &      Personalised
              Timestamps    + time windows       travel patterns     suggestions
              Face groups   Filter: >30km home                     with reasoning
                           2+ days, family
```

## License

MIT
