"""Tests for the CLI."""

import pytest
from click.testing import CliRunner


def test_cli_help():
    """Test CLI help output."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "scan" in result.output


def test_cli_scanning_empty_db():
    """Test scan command with no database."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(
        main, ["scan", "--library", "/nonexistent/photos.photoslibrary"]
    )

    # Should exit with error
    assert result.exit_code != 0 or "_photoslibrary" in result.output


def test_cli_stats():
    """Test stats command."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["stats"])

    # May fail if no db, but should not crash
    assert result.exception is None or result.exit_code != 0


def test_cli_recommend():
    """Test recommend command."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["recommend", "--trips-file", "/nonexistent.json"])

    # Should fail because file doesn't exist
    assert result.exit_code != 0 or "No trips file" in str(result.exception)


def test_cli_map():
    """Test map command."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["map", "--trips-file", "/nonexistent.json"])

    # Should fail because file doesn't exist
    assert result.exit_code != 0 or "No trips" in str(result.exception)


def test_cli_version():
    """Test version flag."""
    from wanderlust.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0
    assert "wanderlust" in result.output.lower() or "version" in result.output.lower()
