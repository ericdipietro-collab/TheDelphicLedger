"""Interactive Rich review of column mapping proposals."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from committee.ingest.header_map import MappingProposal
from committee.ingest.importer import ImportResult
from committee.ingest.template import MappingTemplate, save_template

console = Console()

_METHOD_STYLE = {
    "exact": "green",
    "fuzzy_auto": "yellow",
    "fuzzy_pending": "bold yellow",
    "unmatched": "red",
}


def _sample_values(
    proposals: list[MappingProposal],
    data_rows: list[list[str]],
    headers: list[str],
    n: int = 2,
) -> dict[str, list[str]]:
    """Return up to n sample values per source column."""
    header_index = {h: i for i, h in enumerate(headers)}
    samples: dict[str, list[str]] = {}
    for p in proposals:
        idx = header_index.get(p.source_col)
        if idx is None:
            samples[p.source_col] = []
            continue
        vals: list[str] = []
        for row in data_rows:
            if idx < len(row) and row[idx].strip():
                vals.append(row[idx].strip())
            if len(vals) >= n:
                break
        samples[p.source_col] = vals
    return samples


def show_mapping_table(
    result: ImportResult,
    data_rows: list[list[str]],
    headers: list[str],
) -> None:
    table = Table(title=f"Column Mapping — {result.filename}", show_lines=True)
    table.add_column("Source Column", style="cyan")
    table.add_column("Canonical Field", style="white")
    table.add_column("Method", style="white")
    table.add_column("Score", justify="right")
    table.add_column("Samples", style="dim")

    samples = _sample_values(result.proposals, data_rows, headers)

    for p in result.proposals:
        method_style = _METHOD_STYLE.get(p.method, "white")
        score_str = f"{p.score:.0f}" if p.score is not None else "—"
        sample_str = " | ".join(samples.get(p.source_col, []))
        mapped = result.column_map.get(p.source_col)
        table.add_row(
            p.source_col,
            mapped or "[dim]—[/dim]",
            f"[{method_style}]{p.method}[/{method_style}]",
            score_str,
            sample_str,
        )

    console.print(table)


def review_and_save(
    result: ImportResult,
    data_rows: list[list[str]],
    headers: list[str],
    profiles_dir: Path = Path("profiles"),
    non_interactive: bool = False,
) -> MappingTemplate:
    """Show the mapping table, allow corrections, save as template.

    In non_interactive mode, accept proposals as-is (for testing).
    """
    show_mapping_table(result, data_rows, headers)

    column_map = dict(result.column_map)

    has_pending = any(p.method == "fuzzy_pending" for p in result.proposals)

    if not non_interactive and has_pending:
        console.print("\n[bold yellow]Some mappings need confirmation.[/bold yellow]")
        console.print("Enter corrections as:  source_col = canonical_field  (or blank to skip)\n")
        for p in result.proposals:
            if p.method == "fuzzy_pending":
                correction = typer.prompt(
                    f"  '{p.source_col}' → '{column_map.get(p.source_col)}' ? [enter to accept]",
                    default="",
                    show_default=False,
                )
                correction = correction.strip()
                if correction:
                    if "=" in correction:
                        _, val = correction.split("=", 1)
                        column_map[p.source_col] = val.strip() or None
                    else:
                        column_map[p.source_col] = correction or None

    # Ask for template name
    if non_interactive:
        template_name = result.template_name or f"template_{result.broker_fingerprint}"
    else:
        template_name = typer.prompt(
            "Save template as",
            default=result.template_name or f"template_{result.broker_fingerprint}",
        )

    template = MappingTemplate(
        name=template_name,
        fingerprint=result.broker_fingerprint,
        file_type=result.file_type,
        column_map=column_map,
        type_aliases={},
    )
    path = save_template(template, profiles_dir)
    console.print(f"[green]Template saved → {path}[/green]")
    return template


def confirm_existing_template(
    result: ImportResult,
    data_rows: list[list[str]],
    headers: list[str],
    non_interactive: bool = False,
) -> bool:
    """Show one-screen confirmation for an auto-loaded template. Returns True to proceed."""
    show_mapping_table(result, data_rows, headers)
    console.print(f"\n[green]Template '{result.template_name}' auto-applied.[/green]")
    if non_interactive:
        return True
    return typer.confirm("Proceed with this mapping?", default=True)
