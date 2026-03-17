"""
Wanderlust CLI — Discover your travel history from Apple Photos.
"""

import click
import json
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress

from . import __version__

console = Console()


@click.group()
@click.version_option(version=__version__)
@click.option("--library", default=None, help="Path to Photos library or .photoslibrary")
@click.pass_context
def main(ctx, library):
    """🌍 Wanderlust — Your photos know where you should go next."""
    ctx.ensure_object(dict)
    ctx.obj["library"] = library


@main.command()
@click.option("--family", "-f", multiple=True, help="Family member names (for trip detection)")
@click.option("--name-map", "-n", multiple=True, help="Map photo name to real name (e.g. 'wifey=Anne')")
@click.option("--born", "-b", multiple=True, help="Birth year for family member (e.g. 'Clara=2016')")
@click.option("--home-lat", default=51.5615, help="Home latitude")
@click.option("--home-lon", default=-0.0750, help="Home longitude")
@click.option("--output", "-o", default=None, help="Save results to JSON file")
@click.option("--min-days", default=1, help="Minimum trip duration in days (1 = include day trips)")
@click.pass_context
def scan(ctx, family, name_map, born, home_lat, home_lon, output, min_days):
    """Scan your Photos library and discover trips."""
    from .scanner import scan_photos, get_library_stats
    from .clusterer import cluster_trips
    from .geocoder import enrich_trips

    library = ctx.obj["library"]

    with console.status("[bold green]Connecting to Photos library..."):
        try:
            stats = get_library_stats(library)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise SystemExit(1)

    console.print(Panel(
        f"📸 [bold]{stats['total_photos']:,}[/bold] photos\n"
        f"📍 [bold]{stats['geotagged']:,}[/bold] geotagged ({stats['geotagged_pct']}%)\n"
        f"👤 [bold]{stats['named_people']}[/bold] named people\n"
        f"📅 {stats['oldest'].strftime('%b %Y') if stats['oldest'] else '?'} — "
        f"{stats['newest'].strftime('%b %Y') if stats['newest'] else '?'}",
        title="📷 Photos Library",
    ))

    with Progress() as progress:
        task = progress.add_task("Scanning photos...", total=3)

        # Parse name mappings (e.g. "wifey=Anne")
        name_mapping = {}
        for nm in name_map:
            if "=" in nm:
                photo_name, real_name = nm.split("=", 1)
                name_mapping[photo_name.strip()] = real_name.strip()

        # Parse birth years (e.g. "Clara=2016")
        birth_years = {}
        for b in born:
            if "=" in b:
                name, year = b.split("=", 1)
                birth_years[name.strip()] = int(year.strip())

        if name_mapping:
            console.print(f"  → Name mappings: {name_mapping}")
        if birth_years:
            console.print(f"  → Birth years: {birth_years}")

        photos = scan_photos(
            library,
            progress_callback=lambda msg: console.print(f"  → {msg}"),
            name_map=name_mapping or None,
        )
        progress.advance(task)

        console.print(f"\n[bold]Clustering into trips...[/bold]")
        trips = cluster_trips(
            photos,
            home=(home_lat, home_lon),
            min_trip_days=min_days,
            family_names=list(family) if family else None,
            birth_years=birth_years or None,
            progress_callback=lambda msg: console.print(f"  → {msg}"),
        )
        progress.advance(task)

        console.print(f"\n[bold]Geocoding locations...[/bold]")
        trips = enrich_trips(trips, progress_callback=lambda msg: console.print(f"  → {msg}"))
        progress.advance(task)

    # Display results
    if not trips:
        console.print("\n[yellow]No trips found![/yellow] Try lowering --min-days or checking your Photos library has GPS data.")
        return

    table = Table(title=f"\n🌍 Discovered {len(trips)} Trips")
    table.add_column("#", style="dim")
    table.add_column("Destination", style="bold")
    table.add_column("When", style="cyan")
    table.add_column("Days", justify="right")
    table.add_column("Photos", justify="right")
    table.add_column("People", style="green")
    table.add_column("Family?", justify="center")

    for i, trip in enumerate(trips, 1):
        table.add_row(
            str(i),
            trip.place_name or f"({trip.center_lat:.2f}, {trip.center_lon:.2f})",
            trip.start_date.strftime("%b %Y"),
            str(trip.duration_days),
            str(trip.photo_count),
            ", ".join(trip.people[:2]) if trip.people else "—",
            "👨‍👩‍👧" if trip.is_family_trip else "—",
        )

    console.print(table)

    if output:
        _save_results(trips, output)
        console.print(f"\n💾 Saved to {output}")


@main.command()
@click.pass_context
def stats(ctx):
    """Show Photos library statistics."""
    from .scanner import get_library_stats

    try:
        s = get_library_stats(ctx.obj["library"])
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    console.print(Panel(
        f"📸 Total photos: [bold]{s['total_photos']:,}[/bold]\n"
        f"📍 Geotagged: [bold]{s['geotagged']:,}[/bold] ({s['geotagged_pct']}%)\n"
        f"👤 Named people: [bold]{s['named_people']}[/bold]\n"
        f"📅 Range: {s['oldest'].strftime('%d %b %Y') if s['oldest'] else '?'} — "
        f"{s['newest'].strftime('%d %b %Y') if s['newest'] else '?'}\n"
        f"💾 Database: {s['db_path']}",
        title="📷 Photos Library Stats",
    ))


@main.command()
@click.option("--provider", type=click.Choice(["openai", "ollama", "manual"]), default="manual")
@click.option("--model", default=None, help="Model name")
@click.option("--budget", type=click.Choice(["low", "medium", "high", "luxury"]), default=None)
@click.option("--kids", multiple=True, type=int, help="Kids' ages")
@click.option("--when", default=None, help="Time of year (e.g. 'February half term')")
@click.option("--days", type=int, default=None, help="Trip duration")
@click.option("--interests", multiple=True, help="Interests (beach, culture, food, adventure)")
@click.option("--trips-file", default=None, help="Load trips from JSON file")
@click.pass_context
def recommend(ctx, provider, model, budget, kids, when, days, interests, trips_file):
    """Get AI-powered holiday recommendations based on your travel history."""
    from .profiler import build_profile
    from .recommender import (
        build_recommendation_prompt,
        recommend_with_openai,
        recommend_with_ollama,
        recommend_manual,
    )

    if trips_file:
        trips = _load_trips(trips_file)
    else:
        console.print("[yellow]No trips file specified. Run 'wanderlust scan -o trips.json' first.[/yellow]")
        raise SystemExit(1)

    profile = build_profile(trips)

    if provider == "manual":
        console.print(recommend_manual(profile, trips))
        console.print("\n[dim]Use --provider openai or ollama for AI recommendations[/dim]")
        return

    constraints = {}
    if budget:
        constraints["budget"] = budget
    if kids:
        constraints["kids_ages"] = list(kids)
    if when:
        constraints["time_of_year"] = when
    if days:
        constraints["duration_days"] = days
    if interests:
        constraints["interests"] = list(interests)

    prompt = build_recommendation_prompt(profile, trips, constraints)

    with console.status("[bold green]Thinking about destinations..."):
        if provider == "openai":
            import os
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                console.print("[red]Set OPENAI_API_KEY environment variable[/red]")
                raise SystemExit(1)
            result = recommend_with_openai(prompt, api_key, model=model or "gpt-4o")
        elif provider == "ollama":
            result = recommend_with_ollama(prompt, model=model or "llama3.2:3b")

    console.print(Panel(result, title="🌍 Your Personalised Recommendations", border_style="green"))


@main.command()
@click.option("--trips-file", required=True, help="Load trips from JSON file")
@click.option("--output", "-o", default="travel-map.html", help="Output HTML file")
def map(trips_file, output):
    """Generate an interactive map of your trips."""
    import folium

    trips = _load_trips(trips_file)

    # Auto-center on trip data
    if trips:
        avg_lat = sum(t.center_lat for t in trips) / len(trips)
        avg_lon = sum(t.center_lon for t in trips) / len(trips)
        m = folium.Map(location=[avg_lat, avg_lon], zoom_start=5, tiles="CartoDB dark_matter")
    else:
        m = folium.Map(location=[30, 0], zoom_start=3, tiles="CartoDB dark_matter")

    for trip in trips:
        popup = (
            f"<b>{trip.place_name or 'Unknown'}</b><br>"
            f"{trip.start_date.strftime('%b %Y')}<br>"
            f"{trip.duration_days} days, {trip.photo_count} photos"
        )
        folium.CircleMarker(
            [trip.center_lat, trip.center_lon],
            radius=max(4, min(12, trip.photo_count / 15)),
            popup=popup,
            color="#00ff88" if trip.is_family_trip else "#0088ff",
            fill=True,
            fill_opacity=0.7,
        ).add_to(m)

    m.save(output)
    console.print(f"🗺️  Map saved to [bold]{output}[/bold]")


def _save_results(trips, path):
    """Serialize trips to JSON."""
    data = []
    for t in trips:
        data.append({
            "id": t.id,
            "place_name": t.place_name,
            "city": t.city,
            "country": t.country,
            "center": [t.center_lat, t.center_lon],
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "duration_days": t.duration_days,
            "photo_count": t.photo_count,
            "favorite_count": t.favorite_count,
            "people": t.people,
            "people_counts": t.people_counts,
            "is_family_trip": t.is_family_trip,
            "trip_type": t.trip_type,
            "spread_km": t.spread_km,
            "stops": t.stops,
            "season": t.season,
        })
    Path(path).write_text(json.dumps(data, indent=2))


def _load_trips(path):
    """Deserialize trips from JSON."""
    from .clusterer import Trip
    from datetime import datetime

    data = json.loads(Path(path).read_text())
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
            photo_count=d.get("photo_count", 0),
            favorite_count=d.get("favorite_count", 0),
            trip_type=d.get("trip_type", "stay"),
            spread_km=d.get("spread_km", 0.0),
            stops=d.get("stops", []),
        )
        trips.append(trip)
    return trips


@main.command()
@click.option("--trips-file", required=True, help="Load trips from JSON file")
@click.option("--trip-id", type=int, default=None, help="Enrich a specific trip (by ID)")
@click.option("--provider", type=click.Choice(["openai", "ollama", "manual"]), default="ollama")
@click.option("--model", default=None, help="Model name")
def enrich(trips_file, trip_id, provider, model):
    """Enrich trips with AI-generated narratives from photo metadata."""
    from .enricher import enrich_trip

    trips = _load_trips(trips_file)

    if trip_id is not None:
        target_trips = [t for t in trips if t.id == trip_id]
        if not target_trips:
            console.print(f"[red]Trip {trip_id} not found[/red]")
            raise SystemExit(1)
    else:
        target_trips = trips

    for trip in target_trips:
        console.print(f"\n[bold]Enriching: {trip.place_name or 'Unknown'} ({trip.start_date.strftime('%b %Y')})[/bold]")
        narrative = enrich_trip(
            trip,
            provider=provider,
            model=model,
            progress_callback=lambda msg: console.print(f"  → {msg}"),
        )
        console.print(Panel(narrative, title=f"🌍 {trip.place_name or 'Trip'}", border_style="green"))


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=5555, help="Port to run on")
@click.option("--trips-file", default=None, help="Load trips from JSON file")
@click.option("--debug", is_flag=True, help="Enable debug mode")
def web(host, port, trips_file, debug):
    """Launch the interactive web UI."""
    from .web import run_web
    run_web(host=host, port=port, trips_file=trips_file, debug=debug)


if __name__ == "__main__":
    main()
