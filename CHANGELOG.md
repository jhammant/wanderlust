# Changelog

All notable changes to Wanderlust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial open source launch

### Changed
- None

### Fixed
- None

## [0.1.0] - 2025-03-28

### Added
- 🎉 Initial release: Wanderlust is now open source!
- Scanner module to read Apple Photos library metadata
- Clusterer module for trip detection using DBSCAN + time windows
- Profiler module to build Travel DNA
- Recommender module for AI-powered holiday recommendations
- Geocoder module with Nominatim integration and caching
- Enricher module for AI trip narratives
- CLI with commands: scan, stats, recommend, map, enrich, web
- Interactive web dashboard with Leaflet.js maps

### Features
- Scan Apple Photos library for geotagged photos
- Auto-discover trips from photo metadata (GPS + timestamps)
- Build Travel DNA profile with seasonal preferences, distance analysis
- AI recommendations using OpenAI, OpenRouter, or local Ollama
- Interactive web UI with trip reviews and ratings
- Road trip planner with driving routes and cost estimates
- Weather information via Open-Meteo API

### Technical Details
- Python 3.10+ compatible
- SQLite read-only access to Photos library (macOS only)
- 100% local processing — privacy-first architecture
- 46 tests passing with pytest-cov coverage

### Security
- No hardcoded API keys or secrets
- Environment variable configuration (.env.example)
- Secure handling of user data
