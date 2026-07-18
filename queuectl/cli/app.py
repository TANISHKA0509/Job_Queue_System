from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from queuectl import __version__
from queuectl.core.config import ConfigError
from queuectl.core.models import JobCreate, JobRead, JobState, MetricsSnapshot, StatusSnapshot
from queuectl.core.time import isoformat_z
from queuectl.services.job_service import (
    InvalidStateTransitionError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobService,
)
from queuectl.services.worker_service import WorkerService
from queuectl.storage.database import (
    create_engine_for_url,
    create_session_factory,
    get_database_url,
    init_db,
)

console = Console()

app = typer.Typer(
    name="queuectl",
    help="Manage durable background jobs from the terminal.",
    no_args_is_help=True,
    invoke_without_command=True,
)
worker_app = typer.Typer(help="Start and stop worker processes.", no_args_is_help=True)
dlq_app = typer.Typer(help="Inspect and retry dead-letter jobs.", no_args_is_help=True)
config_app = typer.Typer(help="Read and update queuectl configuration.", no_args_is_help=True)

app.add_typer(worker_app, name="worker")
app.add_typer(dlq_app, name="dlq")
app.add_typer(config_app, name="config")


@dataclass(frozen=True)
class AppContext:
    database_url: str
    jobs: JobService
    workers: WorkerService


def build_context(database_url: str | None = None) -> AppContext:
    resolved_url = database_url or get_database_url()
    engine = create_engine_for_url(resolved_url)
    init_db(engine)
    session_factory = create_session_factory(engine)
    jobs = JobService(session_factory)
    workers = WorkerService(session_factory, database_url=resolved_url)
    return AppContext(database_url=resolved_url, jobs=jobs, workers=workers)


def get_context(ctx: typer.Context) -> AppContext:
    if not isinstance(ctx.obj, AppContext):
        ctx.obj = build_context()
    return ctx.obj


def version_callback(value: bool) -> None:
    if value:
        console.print(f"queuectl {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the installed queuectl version.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = False,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="QUEUECTL_DATABASE_URL", help="SQLite database URL."),
    ] = None,
) -> None:
    ctx.obj = build_context(database_url)


def render_banner(text: str) -> None:
    console.print(Panel.fit(text, title="queuectl", border_style="cyan"))


def render_jobs(jobs: list[JobRead], title: str = "Jobs") -> Table:
    table = Table(title=title, show_lines=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("State")
    table.add_column("Priority")
    table.add_column("Attempts", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("Run At")
    table.add_column("Updated")
    table.add_column("Command", overflow="fold")
    for job in jobs:
        state_style = {
            JobState.PENDING: "yellow",
            JobState.PROCESSING: "blue",
            JobState.COMPLETED: "green",
            JobState.FAILED: "red",
            JobState.DEAD: "bold red",
        }[job.state]
        table.add_row(
            job.id,
            f"[{state_style}]{job.state.value}[/{state_style}]",
            job.priority.value,
            f"{job.attempts}/{job.max_retries}",
            "" if job.exit_code is None else str(job.exit_code),
            isoformat_z(job.run_at) or "now",
            isoformat_z(job.updated_at) or "",
            job.command,
        )
    return table


def render_status(snapshot: StatusSnapshot) -> Table:
    table = Table(title="Queue Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="bold")
    table.add_row("Active workers", str(snapshot.active_workers))
    table.add_row("Pending jobs", str(snapshot.pending_jobs))
    table.add_row("Processing jobs", str(snapshot.processing_jobs))
    table.add_row("Completed jobs", str(snapshot.completed_jobs))
    table.add_row("Failed jobs", str(snapshot.failed_jobs))
    table.add_row("Dead jobs", str(snapshot.dead_jobs))
    return table


def render_metrics(snapshot: MetricsSnapshot) -> Table:
    table = Table(title="Queue Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="bold")
    table.add_row("Total jobs", str(snapshot.total_jobs))
    table.add_row("Completed jobs", str(snapshot.completed_jobs))
    table.add_row("Failed jobs", str(snapshot.failed_jobs))
    table.add_row("Dead jobs", str(snapshot.dead_jobs))
    table.add_row("Success rate", f"{snapshot.success_rate:.2f}%")
    avg = snapshot.average_execution_time
    table.add_row("Average execution time", "n/a" if avg is None else f"{avg:.3f}s")
    table.add_row("Active workers", str(snapshot.active_workers))
    return table


def render_config(values: dict[str, str]) -> Table:
    table = Table(title="Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="bold")
    for key, value in values.items():
        table.add_row(key, value or "[dim]<unset>[/dim]")
    return table


@app.command()
def enqueue(
    ctx: typer.Context,
    payload: Annotated[str, typer.Argument(help="JSON job payload.")],
) -> None:
    context = get_context(ctx)
    try:
        job = JobCreate.model_validate_json(payload)
        created = context.jobs.enqueue(job)
    except ValidationError as exc:
        console.print("[bold red]Invalid job payload[/bold red]")
        console.print(exc)
        raise typer.Exit(2) from exc
    except JobAlreadyExistsError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(1) from exc

    render_banner(f"Enqueued job [bold cyan]{created.id}[/bold cyan]")
    console.print(render_jobs([created], title="Created Job"))


@worker_app.command("start")
def worker_start(
    ctx: typer.Context,
    count: Annotated[int, typer.Option("--count", "-c", min=1, help="Number of workers to start.")] = 1,
) -> None:
    context = get_context(ctx)
    render_banner(f"Starting [bold]{count}[/bold] worker process(es). Press Ctrl+C to request shutdown.")
    context.workers.start(count=count)
    console.print("[green]Workers stopped cleanly.[/green]")


@worker_app.command("stop")
def worker_stop(ctx: typer.Context) -> None:
    context = get_context(ctx)
    updated = context.workers.stop_all()
    console.print(Panel.fit(f"Stop requested for {updated} worker(s).", border_style="yellow"))


@app.command()
def status(ctx: typer.Context) -> None:
    context = get_context(ctx)
    console.print(render_status(context.jobs.status()))


@app.command("list")
def list_jobs(
    ctx: typer.Context,
    state: Annotated[
        JobState | None,
        typer.Option("--state", "-s", help="Filter by job state."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 100,
) -> None:
    context = get_context(ctx)
    jobs = context.jobs.list_jobs(state=state, limit=limit)
    console.print(render_jobs(jobs, title="Jobs"))


@dlq_app.command("list")
def dlq_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 100,
) -> None:
    context = get_context(ctx)
    console.print(render_jobs(context.jobs.list_dlq(limit=limit), title="Dead Letter Queue"))


@dlq_app.command("retry")
def dlq_retry(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Dead job ID to requeue.")],
) -> None:
    context = get_context(ctx)
    try:
        job = context.jobs.retry_dead(job_id)
    except (JobNotFoundError, InvalidStateTransitionError) as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(1) from exc
    console.print(Panel.fit(f"Requeued dead job [bold cyan]{job.id}[/bold cyan].", border_style="green"))


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Configuration key.")],
    value: Annotated[str, typer.Argument(help="Configuration value.")],
) -> None:
    context = get_context(ctx)
    try:
        saved_key, saved_value = context.jobs.set_config(key, value)
    except ConfigError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(2) from exc
    console.print(Panel.fit(f"{saved_key} = {saved_value or '<unset>'}", border_style="green"))


@config_app.command("list")
def config_list(ctx: typer.Context) -> None:
    context = get_context(ctx)
    console.print(render_config(context.jobs.get_config()))


@app.command()
def metrics(ctx: typer.Context) -> None:
    context = get_context(ctx)
    console.print(render_metrics(context.jobs.metrics()))


@app.command()
def dashboard(
    ctx: typer.Context,
    refresh: Annotated[float, typer.Option("--refresh", "-r", min=0.2)] = 1.0,
) -> None:
    context = get_context(ctx)
    with Live(render_status(context.jobs.status()), refresh_per_second=4, console=console) as live:
        try:
            while True:
                live.update(render_status(context.jobs.status()))
                time.sleep(refresh)
        except KeyboardInterrupt:
            console.print("[yellow]Dashboard closed.[/yellow]")


def main() -> None:
    app()
