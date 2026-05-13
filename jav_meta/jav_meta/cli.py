import asyncio
from pathlib import Path
from typing import Optional

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from jav_meta.config import settings
from jav_meta.crawler.orchestrator import CrawlOrchestrator
from jav_meta.database.engine import init_db, SessionLocal
from jav_meta.database.models import Movie, DiscoveryQueue, QueueStatus
from jav_meta.scraper.local_scraper import LocalScraper
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
def discover(
    uncensored: bool = typer.Option(False, "--uncensored", "-u", help="Also scan uncensored section"),
    skip_selftest: bool = typer.Option(False, "--skip-selftest", help="Skip pre-flight checks"),
):
    """Phase 1: Scan all list pages and populate discovery queue."""
    async def _run():
        orchestrator = CrawlOrchestrator()
        await orchestrator.run_discovery(uncensored=uncensored, skip_selftest=skip_selftest)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Discovery interrupted. Resume within 6h to continue.[/yellow]")


@app.command()
def crawl(
    full: bool = typer.Option(False, "--full", help="Full crawl: discover + download all"),
    incremental: bool = typer.Option(False, "--incremental", help="Incremental crawl: discover + download new only"),
    download: bool = typer.Option(False, "--download", help="Download only: process pending queue items"),
    uncensored: bool = typer.Option(False, "--uncensored", "-u", help="Include uncensored section"),
):
    """Crawl metadata from online sources."""
    if sum([full, incremental, download]) != 1:
        console.print("[yellow]Specify exactly one of: --full, --incremental, --download[/yellow]")
        raise typer.Exit(1)

    async def _run():
        orchestrator = CrawlOrchestrator()
        if full:
            await orchestrator.run_full(uncensored=uncensored, skip_selftest=True)
        elif incremental:
            await orchestrator.run_incremental(uncensored=uncensored, skip_selftest=True)
        elif download:
            await orchestrator.run_full_download(uncensored=uncensored, skip_selftest=True)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Crawl interrupted.[/yellow]")


@app.command()
def repair(
    uncensored: bool = typer.Option(False, "--uncensored", "-u", help="Include uncensored section"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be repaired without downloading"),
):
    """Repair movies with missing data (covers, screenshots, actress photos)."""
    with SessionLocal() as db:
        # Movies without cover
        no_cover = db.query(Movie).filter(Movie.cover_local == None).count()
        # Movies without screenshots
        from sqlalchemy import func
        total_movies = db.query(Movie).count()

    console.print(f"[cyan]Repair scan:[/cyan]")
    console.print(f"  Movies without cover: {no_cover}")
    console.print(f"  Total movies: {total_movies}")

    if dry_run:
        console.print("[yellow]Dry run — no changes made.[/yellow]")
        return

    if no_cover == 0:
        console.print("[green]Nothing to repair.[/green]")
        return

    async def _run():
        orch = CrawlOrchestrator()
        await orch.run_full_download(uncensored=uncensored, skip_selftest=True)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Repair interrupted.[/yellow]")


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
            scraper.scrape_folder(
                path.parent, write_nfo=nfo, download_cover=cover,
                download_screenshots=screenshots, copy_actor_photos=actors,
                rename_file=rename, create_folder=mkdir, move_to_folder=move,
                dry_run=dry_run,
            )
        else:
            scraper.scrape_folder(
                path, write_nfo=nfo, download_cover=cover,
                download_screenshots=screenshots, copy_actor_photos=actors,
                rename_file=rename, create_folder=mkdir, move_to_folder=move,
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
def stats():
    """Show database statistics."""
    with SessionLocal() as db:
        total_movies = db.query(Movie).count()
        pending = db.query(DiscoveryQueue).filter(DiscoveryQueue.status == QueueStatus.PENDING).count()
        done = db.query(DiscoveryQueue).filter(DiscoveryQueue.status == QueueStatus.DONE).count()
        failed = db.query(DiscoveryQueue).filter(DiscoveryQueue.status == QueueStatus.FAILED).count()

    table = Table(title="Database Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green")
    table.add_row("Movies", str(total_movies))
    table.add_row("Discovery: pending", str(pending))
    table.add_row("Discovery: done", str(done))
    table.add_row("Discovery: failed", str(failed))
    console.print(table)


@app.command()
def queue():
    """Show discovery queue status."""
    with SessionLocal() as db:
        pending = db.query(DiscoveryQueue).filter(DiscoveryQueue.status == QueueStatus.PENDING).all()
        failed = db.query(DiscoveryQueue).filter(DiscoveryQueue.status == QueueStatus.FAILED).all()

    if pending:
        table = Table(title=f"Pending ({len(pending)})")
        table.add_column("Code", style="cyan")
        table.add_column("URL", style="dim")
        for item in pending[:20]:
            table.add_row(item.code, item.detail_url[:60])
        console.print(table)
        if len(pending) > 20:
            console.print(f"  ... and {len(pending) - 20} more")

    if failed:
        table = Table(title=f"Failed ({len(failed)})")
        table.add_column("Code", style="red")
        for item in failed:
            table.add_row(item.code)
        console.print(table)

    if not pending and not failed:
        console.print("[green]Discovery queue is empty. Nothing to download.[/green]")


if __name__ == "__main__":
    app()
