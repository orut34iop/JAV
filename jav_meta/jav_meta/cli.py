import asyncio
import datetime
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from jav_meta.config import settings
from jav_meta.crawler.orchestrator import CrawlOrchestrator
from jav_meta.database.engine import init_db, SessionLocal
from jav_meta.database.models import Movie
from jav_meta.scraper.local_scraper import LocalScraper
from jav_meta.utils.http_client import AdaptiveClient
from jav_meta.scrapers.javbus import JavBusScraper
from jav_meta.utils.selftest import SelfTestRunner

app = typer.Typer(help="JAV Local Metadata Database & Scraper")
console = Console()


def _setup_logging(verbose: bool = False):
    from loguru import logger as _logger
    _logger.remove()
    level = "DEBUG" if verbose else "INFO"
    _logger.add(lambda msg: print(msg, end=""), level=level, colorize=True,
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")):
    _setup_logging(verbose)


@app.command()
def init():
    """Initialize the local database."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    settings.dumps_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    console.print("[green]Database initialized successfully.[/green]")
    console.print(f"DB path: {settings.db_path}")


@app.command()
def crawl(
    source: str = typer.Argument("javbus", help="Scraper source name"),
    full: bool = typer.Option(False, "--full", help="Run full crawl"),
    incremental: bool = typer.Option(False, "--incremental", help="Run incremental update"),
    pages: Optional[str] = typer.Option(None, "--pages", help="Page range, e.g. 1-100"),
    uncensored: bool = typer.Option(False, "--uncensored", "-u", help="Crawl uncensored section"),
):
    """Crawl metadata from online sources."""
    if source != "javbus":
        console.print(f"[red]Source '{source}' not yet implemented. Use 'javbus'.[/red]")
        raise typer.Exit(1)

    start_page = 1
    end_page = None

    if pages:
        try:
            parts = pages.split("-")
            start_page = int(parts[0])
            end_page = int(parts[1]) if len(parts) > 1 else start_page
        except ValueError:
            console.print("[red]Invalid page format. Use e.g. 1-100[/red]")
            raise typer.Exit(1)
    elif full:
        start_page = 1
        end_page = None
    elif incremental:
        start_page = 1
        end_page = None
    else:
        console.print("[yellow]Please specify --full, --incremental, or --pages[/yellow]")
        raise typer.Exit(1)

    async def _run():
        orchestrator = CrawlOrchestrator()
        if incremental:
            await orchestrator.run_incremental(uncensored=uncensored)
        else:
            await orchestrator.run_full(
                start_page=start_page,
                end_page=end_page,
                uncensored=uncensored,
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Crawl interrupted.[/yellow]")


@app.command()
def scrape(
    path: Path = typer.Argument(..., help="Path to movie folder or file"),
    nfo: bool = typer.Option(True, "--nfo/--no-nfo", help="Generate NFO files"),
    cover: bool = typer.Option(True, "--cover/--no-cover", help="Copy cover images"),
    screenshots: bool = typer.Option(True, "--screenshots/--no-screenshots", help="Copy screenshots"),
    actors: bool = typer.Option(True, "--actors/--no-actors", help="Copy actor photos"),
    rename: bool = typer.Option(False, "--rename", help="Rename video files"),
    mkdir: bool = typer.Option(False, "--mkdir", help="Create per-movie folders"),
    move: bool = typer.Option(False, "--move", help="Move files into per-movie folders"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
):
    """Scrape local video files using the local database."""
    path = Path(path).resolve()
    if not path.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    scraper = LocalScraper()
    try:
        if path.is_file():
            # Single file: treat parent folder
            scraper.scrape_folder(
                path.parent,
                write_nfo=nfo,
                download_cover=cover,
                download_screenshots=screenshots,
                copy_actor_photos=actors,
                rename_file=rename,
                create_folder=mkdir,
                move_to_folder=move,
                dry_run=dry_run,
            )
        else:
            scraper.scrape_folder(
                path,
                write_nfo=nfo,
                download_cover=cover,
                download_screenshots=screenshots,
                copy_actor_photos=actors,
                rename_file=rename,
                create_folder=mkdir,
                move_to_folder=move,
                dry_run=dry_run,
            )
    finally:
        scraper.close()

    action = "[dry-run preview]" if dry_run else "Done"
    console.print(f"[green]{action} scraping {path}[/green]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Movie code or title keyword"),
):
    """Search the local database."""
    scraper = LocalScraper()
    try:
        results = scraper.search(query)
        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        table = Table(title=f"Search Results for '{query}'")
        table.add_column("Code", style="cyan")
        table.add_column("Title", style="green")
        table.add_column("Date", style="yellow")
        table.add_column("Studio", style="magenta")

        for m in results:
            table.add_row(
                m.code,
                m.title or "",
                str(m.release_date) if m.release_date else "",
                m.studio or "",
            )
        console.print(table)
    finally:
        scraper.close()


@app.command()
def selftest():
    """Run self-test before crawling."""
    runner = SelfTestRunner()
    passed = runner.run_and_print()
    if not passed:
        raise typer.Exit(1)
    console.print("[green]System is ready for crawling.[/green]")


@app.command()
def daemon(
    interval_hours: int = typer.Option(24, "--interval", help="Incremental crawl interval in hours"),
    uncensored: bool = typer.Option(False, "--uncensored", "-u", help="Also crawl uncensored section"),
):
    """Run as a background daemon with periodic incremental updates."""
    async def _loop():
        while True:
            console.print(f"[cyan]{datetime.datetime.now()}: Starting incremental crawl...[/cyan]")
            try:
                orchestrator = CrawlOrchestrator()
                await orchestrator.run_incremental(uncensored=uncensored)
            except Exception as e:
                logger.error(f"Daemon crawl failed: {e}")
                console.print(f"[red]Crawl failed: {e}. Retrying in 1 hour...[/red]")
                await asyncio.sleep(3600)
                continue

            next_run = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)
            console.print(f"[green]Next incremental crawl at {next_run}[/green]")
            await asyncio.sleep(interval_hours * 3600)

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped.[/yellow]")


@app.command()
def stats():
    """Show database statistics."""
    scraper = LocalScraper()
    try:
        s = scraper.stats()
        table = Table(title="Database Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="green")
        table.add_row("Movies", str(s.get("movies", 0)))
        table.add_row("Actresses", str(s.get("actresses", 0)))
        console.print(table)
    finally:
        scraper.close()


if __name__ == "__main__":
    app()
