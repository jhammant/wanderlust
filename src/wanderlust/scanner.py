"""
Scanner — Reads Apple Photos SQLite database for location metadata.

The macOS Photos app stores all metadata in:
  ~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite

Key tables:
  ZASSET — main photo/video table (dates, GPS, UUIDs)
  ZADDITIONALASSETATTRIBUTES — extended metadata (camera, lens, etc.)
  ZDETECTEDFACE — face detection results
  ZPERSON — named people (face groups)
  ZGENERICALBUM — albums
  ZMOMENT — auto-grouped moments (Apple's own clustering)

GPS fields in ZASSET:
  ZLATITUDE, ZLONGITUDE — photo location
  ZDATECREATED — Core Data timestamp (seconds since 2001-01-01)

We NEVER write to this database. Read-only always.
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional


# Core Data epoch: 2001-01-01 00:00:00 UTC
CORE_DATA_EPOCH = datetime(2001, 1, 1)

# Default Photos library location
DEFAULT_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary" / "database" / "Photos.sqlite"


@dataclass
class PhotoRecord:
    """A single photo's metadata extracted from the library."""
    uuid: str
    timestamp: datetime
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    faces: list[str] = field(default_factory=list)
    filename: Optional[str] = None
    duration: float = 0.0  # video duration in seconds
    is_favorite: bool = False


def find_photos_db(library_path: Optional[str] = None) -> Path:
    """Locate the Photos SQLite database."""
    if library_path:
        p = Path(library_path)
        if p.suffix == ".photoslibrary":
            p = p / "database" / "Photos.sqlite"
        if not p.exists():
            raise FileNotFoundError(f"Photos database not found: {p}")
        return p

    # Try default location
    if DEFAULT_LIBRARY.exists():
        return DEFAULT_LIBRARY

    # Search for any .photoslibrary in ~/Pictures
    pics = Path.home() / "Pictures"
    for lib in pics.glob("*.photoslibrary"):
        db = lib / "database" / "Photos.sqlite"
        if db.exists():
            return db

    raise FileNotFoundError(
        "No Photos library found. Specify path with --library or ensure "
        "~/Pictures/Photos Library.photoslibrary exists."
    )


def core_data_to_datetime(timestamp: float) -> datetime:
    """Convert Core Data timestamp to Python datetime."""
    if timestamp is None:
        return None
    return CORE_DATA_EPOCH + timedelta(seconds=timestamp)


def scan_photos(
    library_path: Optional[str] = None,
    min_year: int = 2000,
    progress_callback=None,
) -> list[PhotoRecord]:
    """
    Scan the Photos database and extract geotagged photos.

    Returns a list of PhotoRecord with location data.
    Only includes photos that have GPS coordinates.
    """
    db_path = find_photos_db(library_path)

    # Open read-only (we NEVER write)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # Step 1: Get all geotagged photos
        photos_query = """
        SELECT
            Z_PK,
            ZUUID,
            ZDATECREATED,
            ZLATITUDE,
            ZLONGITUDE,
            ZFILENAME,
            ZDURATION,
            ZFAVORITE
        FROM ZASSET
        WHERE ZLATITUDE IS NOT NULL
          AND ZLONGITUDE IS NOT NULL
          AND ZLATITUDE != 0
          AND ZLONGITUDE != 0
          AND ZTRASHEDSTATE = 0
        ORDER BY ZDATECREATED
        """
        cursor = conn.execute(photos_query)
        rows = cursor.fetchall()

        if progress_callback:
            progress_callback(f"Found {len(rows)} geotagged photos")

        # Step 2: Build face lookup (person name -> asset PKs)
        face_map = {}  # asset_pk -> [person_names]
        try:
            face_query = """
            SELECT
                f.ZASSET,
                p.ZFULLNAME
            FROM ZDETECTEDFACE f
            JOIN ZPERSON p ON f.ZPERSON = p.Z_PK
            WHERE p.ZFULLNAME IS NOT NULL
              AND p.ZFULLNAME != ''
            """
            for row in conn.execute(face_query):
                asset_pk = row["ZASSET"]
                name = row["ZFULLNAME"]
                face_map.setdefault(asset_pk, []).append(name)
        except sqlite3.OperationalError:
            # Face tables might not exist or have different schema
            if progress_callback:
                progress_callback("Warning: Could not read face data")

        # Step 3: Build PhotoRecords
        photos = []
        min_ts = datetime(min_year, 1, 1)

        for row in rows:
            ts = core_data_to_datetime(row["ZDATECREATED"])
            if ts is None or ts < min_ts:
                continue

            record = PhotoRecord(
                uuid=row["ZUUID"],
                timestamp=ts,
                latitude=row["ZLATITUDE"],
                longitude=row["ZLONGITUDE"],
                filename=row["ZFILENAME"],
                duration=row["ZDURATION"] or 0.0,
                is_favorite=bool(row["ZFAVORITE"]),
                faces=face_map.get(row["Z_PK"], []),
            )
            photos.append(record)

        if progress_callback:
            progress_callback(f"Extracted {len(photos)} photos with location data")

        return photos

    finally:
        conn.close()


def get_library_stats(library_path: Optional[str] = None) -> dict:
    """Get quick stats about the Photos library without full scan."""
    db_path = find_photos_db(library_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    try:
        total = conn.execute("SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0").fetchone()[0]
        geotagged = conn.execute(
            "SELECT COUNT(*) FROM ZASSET WHERE ZLATITUDE IS NOT NULL AND ZLATITUDE != 0 AND ZTRASHEDSTATE = 0"
        ).fetchone()[0]

        try:
            named_faces = conn.execute(
                "SELECT COUNT(DISTINCT ZFULLNAME) FROM ZPERSON WHERE ZFULLNAME IS NOT NULL AND ZFULLNAME != ''"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            named_faces = 0

        oldest = conn.execute(
            "SELECT MIN(ZDATECREATED) FROM ZASSET WHERE ZTRASHEDSTATE = 0 AND ZDATECREATED > 0"
        ).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(ZDATECREATED) FROM ZASSET WHERE ZTRASHEDSTATE = 0"
        ).fetchone()[0]

        return {
            "total_photos": total,
            "geotagged": geotagged,
            "geotagged_pct": round(geotagged / total * 100, 1) if total else 0,
            "named_people": named_faces,
            "oldest": core_data_to_datetime(oldest) if oldest else None,
            "newest": core_data_to_datetime(newest) if newest else None,
            "db_path": str(db_path),
        }
    finally:
        conn.close()
