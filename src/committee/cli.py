"""Typer CLI entry point for committee."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="committee", add_completion=False)
universe_app = typer.Typer(name="universe", help="Manage the instrument universe (bundles).")
app.add_typer(universe_app, name="universe")
console = Console()

_DB_PATH_OPT = typer.Option(Path("data/ledger.db"), "--db", help="SQLite database path")
_PROFILES_OPT = typer.Option(Path("profiles"), "--profiles", help="Profiles directory")
_BUNDLES_OPT = typer.Option(Path("bundles"), "--bundles", help="Bundles directory")


@app.command()
def import_file(
    file: Path = typer.Argument(..., help="CSV file to import"),  # noqa: B008
    db: Path = _DB_PATH_OPT,
    profiles: Path = _PROFILES_OPT,
    non_interactive: bool = typer.Option(False, "--non-interactive", "-n", help="Skip prompts"),
    file_type: str | None = typer.Option(None, "--type", help="Force 'positions' or 'transactions'"),
    no_resolve: bool = typer.Option(False, "--no-resolve", help="Skip auto-resolution after import"),
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

    if file_type:
        if file_type not in ("positions", "transactions"):
            console.print("[red]--type must be 'positions' or 'transactions'[/red]")
            raise typer.Exit(1)
        result = result.model_copy(update={"file_type": file_type})

    raw_rows = read_csv_rows(content)
    headers, data_rows = preclean(raw_rows)

    if result.template_name is not None:
        proceed = confirm_existing_template(result, data_rows, headers, non_interactive)
        if not proceed:
            console.print("[yellow]Import cancelled.[/yellow]")
            raise typer.Exit(0)
    else:
        template = review_and_save(result, data_rows, headers, profiles, non_interactive)
        result = result.model_copy(update={
            "template_name": template.name,
            "column_map": template.column_map,
        })

    if result.queued_types:
        console.print(f"\n[yellow]Unknown transaction types queued for review: {result.queued_types}[/yellow]")

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

        if not no_resolve and result.file_type == "positions":
            _run_auto_resolve(session, batch.id)
            session.commit()

    except ValueError as e:
        console.print(f"[yellow]{e}[/yellow]")
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


def _run_auto_resolve(session: object, batch_id: int) -> None:
    """Run the resolver cascade on all unresolved raw_instrument values in a batch."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from committee.models import PositionSnapshot
    from committee.resolver.cascade import resolve_instrument
    from committee.resolver.openfigi import live_figi_lookup

    if not isinstance(session, Session):
        return

    snapshots = session.execute(
        select(PositionSnapshot).where(PositionSnapshot.batch_id == batch_id)
    ).scalars().all()

    seen: set[str] = set()
    resolved = queued = 0
    for snap in snapshots:
        raw = snap.raw_instrument
        if not raw or raw in seen:
            continue
        seen.add(raw)
        res = resolve_instrument(
            raw_ticker=raw,
            raw_name=None,
            session=session,
            batch_id=batch_id,
            figi_lookup=live_figi_lookup,
        )
        if res.queued:
            queued += 1
        else:
            resolved += 1

    if resolved or queued:
        console.print(
            f"  Resolver: [green]{resolved} resolved[/green]"
            + (f", [yellow]{queued} queued[/yellow]" if queued else "")
        )


@app.command()
def resolve(
    db: Path = _DB_PATH_OPT,
) -> None:
    """Interactively resolve queued instruments."""
    from datetime import datetime

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from committee.db import get_session, init_db
    from committee.models import Instrument, UnresolvedQueue
    from committee.resolver.aliases import get_or_create_cash
    from committee.resolver.cascade import _add_alias, _write_decision
    from committee.resolver.classify import (
        infer_asset_class,
        needs_unwind_flag,
    )

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, Session):
        return

    try:
        pending = session.execute(
            select(UnresolvedQueue).where(
                UnresolvedQueue.queue_type == "instrument",
                UnresolvedQueue.resolved_at.is_(None),
            ).order_by(UnresolvedQueue.created_at)
        ).scalars().all()

        if not pending:
            console.print("[green]No unresolved instruments.[/green]")
            raise typer.Exit(0)

        console.print(f"\n[bold]Unresolved instruments ({len(pending)} pending)[/bold]\n")

        for item in pending:
            ctx = item.context_json or {}
            candidates_raw: list[tuple[int, int]] = ctx.get("candidates", [])

            console.rule(f"[bold]{item.raw_value}[/bold]")

            # Show candidates if any
            candidate_insts: list[Instrument] = []
            if candidates_raw:
                console.print("  Candidates (fuzzy matches):")
                for inst_id, score in candidates_raw[:3]:
                    inst = session.get(Instrument, inst_id)
                    if inst:
                        candidate_insts.append(inst)
                        console.print(f"    [{len(candidate_insts)}] {inst.ticker or '?'} — {inst.name} (score {score})")

            console.print(
                "\n  Options: "
                + ("[a]ccept candidate  " if candidate_insts else "")
                + "[s]earch  [c]reate  [m]ark-cash  [k]eep  [q]uit"
            )
            choice = typer.prompt("  Enter choice", default="k").strip().lower()

            if choice == "q":
                break

            if choice == "k":
                continue

            resolved_inst: Instrument | None = None

            if choice == "a" and candidate_insts:
                if len(candidate_insts) == 1:
                    resolved_inst = candidate_insts[0]
                else:
                    idx = typer.prompt(
                        f"  Choose candidate (1–{len(candidate_insts)})", default="1"
                    )
                    try:
                        resolved_inst = candidate_insts[int(idx) - 1]
                    except (ValueError, IndexError):
                        console.print("[red]Invalid choice, keeping in queue.[/red]")
                        continue

            elif choice == "s":
                ticker_in = typer.prompt("  Ticker or FIGI").strip().upper()
                found = session.execute(
                    select(Instrument).where(Instrument.ticker == ticker_in)
                ).scalar_one_or_none()
                if found is None:
                    console.print(f"[yellow]No instrument found for '{ticker_in}'.[/yellow]")
                    continue
                resolved_inst = found

            elif choice == "c":
                ticker_in = typer.prompt("  Ticker (leave blank for name-only)").strip() or None
                name_in = typer.prompt("  Name").strip() or None
                itype = typer.prompt("  Type (stock/etf/mutual_fund/cash/unclassified)", default="unclassified").strip()
                itype = itype if itype in ("stock", "etf", "mutual_fund", "cash") else "unclassified"
                asset_class = infer_asset_class(itype, name_in)
                resolved_inst = Instrument(
                    ticker=ticker_in,
                    name=name_in,
                    instrument_type=itype,
                    asset_class=asset_class,
                    sleeve="unclassified",
                    is_cash_equivalent=(itype == "cash"),
                    needs_unwind=needs_unwind_flag(itype),
                    aliases=[],
                    bundle_tags=[],
                )
                session.add(resolved_inst)
                session.flush()

            elif choice == "m":
                resolved_inst = get_or_create_cash(session)

            else:
                console.print("[yellow]Unknown choice, keeping in queue.[/yellow]")
                continue

            if resolved_inst is not None:
                _add_alias(resolved_inst, item.raw_value)
                _write_decision(
                    session,
                    batch_id=ctx.get("batch_id"),
                    raw_value=item.raw_value,
                    resolved_to=resolved_inst.ticker,
                    method="human",
                    confidence=__import__("decimal").Decimal("1"),
                    accepted_by="human",
                )
                item.resolved_at = datetime.now()
                item.resolved_to = resolved_inst.ticker
                session.commit()
                console.print(f"  [green]Resolved → {resolved_inst.ticker or resolved_inst.name}[/green]")

    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command()
def holdings(
    db: Path = _DB_PATH_OPT,
    account: str | None = typer.Option(None, "--account", "-a", help="Filter by account ID"),
    household: bool = typer.Option(False, "--household", "-H", help="Aggregate across all accounts"),
) -> None:
    """Derive and display current holdings from latest position snapshots."""
    from committee.db import get_session, init_db
    from committee.holdings import household_view as agg_household
    from committee.holdings import rebuild_holdings

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        rows = rebuild_holdings(session)
        session.commit()

        if account:
            rows = [r for r in rows if r.account_id == account]
        if household:
            rows = agg_household(rows)

        if not rows:
            console.print("[yellow]No holdings found. Run 'committee import' first.[/yellow]")
            raise typer.Exit(0)

        tbl = Table(title="Holdings" + (" — Household" if household else ""), show_lines=False)
        tbl.add_column("Ticker", style="bold")
        tbl.add_column("Name")
        tbl.add_column("Type")
        tbl.add_column("Account")
        tbl.add_column("Qty", justify="right")
        tbl.add_column("Mkt Value", justify="right")
        tbl.add_column("As Of")

        for r in sorted(rows, key=lambda x: (x.ticker or x.raw_instrument)):
            ticker_str = r.ticker or f"[dim]{r.raw_instrument}[/dim]"
            if not r.is_resolved:
                ticker_str = f"[red]{r.raw_instrument}[/red]"
            tbl.add_row(
                ticker_str,
                r.name or "—",
                r.instrument_type or "—",
                r.account_id or "household",
                f"{r.qty:,.4f}" if r.qty is not None else "—",
                f"${r.market_value:,.2f}" if r.market_value is not None else "—",
                r.as_of.isoformat() if r.as_of else "—",
            )

        console.print(tbl)
        unresolved = sum(1 for r in rows if not r.is_resolved)
        if unresolved:
            console.print(
                f"[yellow]{unresolved} unresolved instrument(s). "
                "Run 'committee resolve' to link them.[/yellow]"
            )
    except Exception:
        session.rollback()
        raise
    finally:
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


# ── Universe commands ──────────────────────────────────────────────────────────

@universe_app.command("enable")
def universe_enable(
    bundle_id: str = typer.Argument(..., help="Bundle ID to enable"),
    db: Path = _DB_PATH_OPT,
    bundles: Path = _BUNDLES_OPT,
    cap: int = typer.Option(3000, "--cap", help="Instrument cap override"),
) -> None:
    """Enable a bundle (adds it to the active universe)."""
    from committee.db import get_session, init_db
    from committee.universe.loader import load_bundle_config
    from committee.universe.manager import enable_bundle

    cfg = load_bundle_config(bundle_id, bundles)
    if cfg is None:
        console.print(f"[red]Bundle '{bundle_id}' not found in {bundles}[/red]")
        raise typer.Exit(1)

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        status, msg = enable_bundle(session, cfg, cap=cap)
        if status == "refuse":
            console.print(f"[red]{msg}[/red]")
            raise typer.Exit(1)
        style = "yellow" if status == "warn" else "green"
        console.print(f"[{style}]{msg}[/{style}]")
        session.commit()
    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@universe_app.command("disable")
def universe_disable(
    bundle_id: str = typer.Argument(..., help="Bundle ID to disable"),
    db: Path = _DB_PATH_OPT,
) -> None:
    """Disable a bundle (removes it from the active universe)."""
    from committee.db import get_session, init_db
    from committee.universe.manager import disable_bundle

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        msg = disable_bundle(session, bundle_id)
        console.print(f"[green]{msg}[/green]")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@universe_app.command("refresh")
def universe_refresh(
    bundle_id: str | None = typer.Argument(default=None, help="Bundle ID (omit for all enabled)"),
    db: Path = _DB_PATH_OPT,
    bundles: Path = _BUNDLES_OPT,
    profiles: Path = _PROFILES_OPT,
) -> None:
    """Refresh bundle instrument membership (resolve + tag instruments)."""
    from committee.db import get_session, init_db
    from committee.models import BundleState
    from committee.universe.loader import load_bundle_config, load_bundle_configs
    from committee.universe.manager import refresh_bundle

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        if bundle_id:
            cfg = load_bundle_config(bundle_id, bundles)
            if cfg is None:
                console.print(f"[red]Bundle '{bundle_id}' not found in {bundles}[/red]")
                raise typer.Exit(1)
            targets = [cfg]
        else:
            all_cfgs = load_bundle_configs(bundles)
            enabled_ids = {
                s.id
                for s in session.query(BundleState).filter(BundleState.enabled.is_(True)).all()
            }
            targets = [c for c in all_cfgs if c.id in enabled_ids]
            if not targets:
                console.print("[yellow]No enabled bundles to refresh.[/yellow]")
                raise typer.Exit(0)

        for cfg in targets:
            console.print(f"  Refreshing [bold]{cfg.id}[/bold]…", end=" ")
            count, err = refresh_bundle(session, cfg, profiles_dir=profiles)
            if err:
                console.print(f"[red]error: {err}[/red]")
            else:
                console.print(f"[green]{count} instruments tagged[/green]")

        session.commit()
    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@universe_app.command("status")
def universe_status(
    db: Path = _DB_PATH_OPT,
    bundles: Path = _BUNDLES_OPT,
) -> None:
    """Show the status of all bundles."""
    from committee.db import get_session, init_db
    from committee.universe.guard import active_universe_size
    from committee.universe.loader import load_bundle_configs
    from committee.universe.manager import get_all_states

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        configs = load_bundle_configs(bundles)
        states = get_all_states(session)
        current_size = active_universe_size(session)

        tbl = Table(title=f"Universe bundles  (active ~{current_size}/3000)", show_lines=False)
        tbl.add_column("Bundle", style="bold")
        tbl.add_column("Source")
        tbl.add_column("Refresh")
        tbl.add_column("~Size", justify="right")
        tbl.add_column("Enabled")
        tbl.add_column("Instruments", justify="right")
        tbl.add_column("Last Refresh")
        tbl.add_column("Error")

        for cfg in configs:
            state = states.get(cfg.id)
            enabled = state.enabled if state else cfg.enabled_default
            count = state.instrument_count if state else 0
            refreshed = (
                state.last_refreshed_at.strftime("%Y-%m-%d %H:%M") if (state and state.last_refreshed_at) else "—"
            )
            error = (state.last_error or "")[:40] if state else ""
            tbl.add_row(
                cfg.id,
                cfg.source,
                cfg.refresh_policy,
                str(cfg.size_estimate or "?"),
                "[green]yes[/green]" if enabled else "[dim]no[/dim]",
                str(count),
                refreshed,
                f"[red]{error}[/red]" if error else "—",
            )

        console.print(tbl)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


if __name__ == "__main__":
    app()
