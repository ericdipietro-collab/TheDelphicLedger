"""Typer CLI entry point for committee."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(name="committee", add_completion=False)
console = Console()

_DB_PATH_OPT = typer.Option(Path("data/ledger.db"), "--db", help="SQLite database path")
_PROFILES_OPT = typer.Option(Path("profiles"), "--profiles", help="Profiles directory")


@app.command()
def import_file(
    file: Path = typer.Argument(..., help="CSV file to import"),  # noqa: B008
    db: Path = _DB_PATH_OPT,
    profiles: Path = _PROFILES_OPT,
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="Skip prompts"),
    file_type: str | None = typer.Option(None, "--type", help="Force 'positions' or 'transactions'"),
) -> None:
    """Import a positions or transactions CSV file."""
    from committee.db import get_session, init_db
    from committee.ingest.importer import process_file, read_csv_rows
    from committee.ingest.persist import persist_import
    from committee.ingest.preclean import preclean
    from committee.ingest.review import confirm_existing_template, review_and_save

    if not file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    content = file.read_bytes()

    try:
        result = process_file(
            content=content,
            filename=file.name,
            profiles_dir=profiles,
        )
    except ValueError as e:
        console.print(f"[red]Import error: {e}[/red]")
        raise typer.Exit(1) from None

    # Force file type if user specified
    if file_type:
        if file_type not in ("positions", "transactions"):
            console.print("[red]--type must be 'positions' or 'transactions'[/red]")
            raise typer.Exit(1)
        result = result.model_copy(update={"file_type": file_type})

    # Get data rows for review display
    raw_rows = read_csv_rows(content)
    headers, data_rows = preclean(raw_rows)

    # Template interaction
    if result.template_name is not None:
        # Template auto-loaded — show confirmation screen
        proceed = confirm_existing_template(result, data_rows, headers, non_interactive)
        if not proceed:
            console.print("[yellow]Import cancelled.[/yellow]")
            raise typer.Exit(0)
    else:
        # New broker — interactive review and save
        template = review_and_save(result, data_rows, headers, profiles, non_interactive)
        result = result.model_copy(update={
            "template_name": template.name,
            "column_map": template.column_map,
        })

    # Show queued types
    if result.queued_types:
        console.print(f"\n[yellow]Unknown transaction types queued for review: {result.queued_types}[/yellow]")

    # Persist
    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        batch = persist_import(result, session)
        session.commit()
        console.print(
            f"\n[green]Imported {result.row_count} {result.file_type} rows "
            f"(batch #{batch.id})[/green]"
        )
    except ValueError as e:
        console.print(f"[yellow]{e}[/yellow]")
    except Exception:
        session.rollback()
        raise
    finally:
        import contextlib
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command()
def template_export(
    fingerprint: str = typer.Argument(..., help="Broker fingerprint or template name"),
    profiles: Path = _PROFILES_OPT,
) -> None:
    """Export a mapping template as YAML to stdout."""
    from committee.ingest.template import export_template_yaml, load_named_template, load_template

    tmpl = load_template(fingerprint, profiles) or load_named_template(fingerprint, profiles)
    if tmpl is None:
        console.print(f"[red]Template not found: {fingerprint}[/red]")
        raise typer.Exit(1)
    console.print(export_template_yaml(tmpl))


@app.command()
def template_import(
    file: Path = typer.Argument(..., help="YAML template file to import"),  # noqa: B008
    profiles: Path = _PROFILES_OPT,
) -> None:
    """Import a mapping template from a YAML file."""
    from committee.ingest.template import import_template_yaml, save_template

    yaml_str = file.read_text()
    try:
        tmpl = import_template_yaml(yaml_str)
    except Exception as e:
        console.print(f"[red]Invalid template: {e}[/red]")
        raise typer.Exit(1) from None
    path = save_template(tmpl, profiles)
    console.print(f"[green]Template '{tmpl.name}' saved → {path}[/green]")


if __name__ == "__main__":
    app()
