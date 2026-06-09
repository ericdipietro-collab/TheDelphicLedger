"""Typer CLI entry point for committee."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="committee", add_completion=False)
universe_app = typer.Typer(name="universe", help="Manage the instrument universe (bundles).")
recon_app = typer.Typer(name="recon", help="Quantity reconciliation (run, list, show, resolve).")
app.add_typer(universe_app, name="universe")
app.add_typer(recon_app, name="recon")
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

        # Rebuild lots after every import (derived data, like holdings)
        from committee.lots.derive import derive_lots
        lot_count = derive_lots(session)
        session.commit()
        if lot_count:
            console.print(f"  Lots: [dim]{lot_count} lot(s) derived[/dim]")

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

            # Rich interprets [x] as markup — use \[ to emit a literal bracket
            opts = (
                "\n  Options: "
                + ("(a) accept candidate  " if candidate_insts else "")
                + "(s) map to ticker  (c) create new  (m) mark cash  (k) keep / skip  (q) quit"
            )
            console.print(opts)
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
                ticker_in = typer.prompt("  Ticker").strip().upper()
                found = session.execute(
                    select(Instrument).where(Instrument.ticker == ticker_in)
                ).scalar_one_or_none()
                if found is None:
                    # Ticker not in DB yet — offer to create it on the spot
                    console.print(f"[yellow]'{ticker_in}' not in instruments table.[/yellow]")
                    create_now = typer.confirm(f"  Create new instrument for {ticker_in}?", default=True)
                    if not create_now:
                        continue
                    name_in = typer.prompt("  Name (Enter to use ticker as name)", default=ticker_in).strip() or ticker_in
                    itype = typer.prompt("  Type (stock/etf/mutual_fund/cash/unclassified)", default="etf").strip()
                    itype = itype if itype in ("stock", "etf", "mutual_fund", "cash") else "unclassified"
                    asset_class = infer_asset_class(itype, name_in)
                    found = Instrument(
                        ticker=ticker_in,
                        name=name_in,
                        instrument_type=itype,
                        asset_class=asset_class,
                        sleeve="unclassified",
                        is_cash_equivalent=False,
                        needs_unwind=needs_unwind_flag(itype),
                        aliases=[],
                        bundle_tags=[],
                    )
                    session.add(found)
                    session.flush()
                resolved_inst = found

            elif choice == "c":
                ticker_in = typer.prompt("  Ticker (Enter to skip)").strip().upper() or None
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


# ── Recon commands ─────────────────────────────────────────────────────────────

@recon_app.command("run")
def recon_run(
    db: Path = _DB_PATH_OPT,
    materiality_shares: float = typer.Option(0.5, "--materiality-shares", help="Shares tolerance"),
    materiality_pct: float = typer.Option(0.001, "--materiality-pct", help="Pct tolerance (0.001 = 0.1%)"),
) -> None:
    """Run quantity reconciliation across all instruments."""
    from decimal import Decimal

    from committee.db import get_session, init_db
    from committee.recon.engine import ReconConfig, run_recon

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        config = ReconConfig(
            materiality_shares=Decimal(str(materiality_shares)),
            materiality_pct=Decimal(str(materiality_pct)),
        )
        summary = run_recon(session, config)
        session.commit()
        console.print(
            f"[bold]Recon complete.[/bold] "
            f"[red]{summary.open_breaks} open[/red]  "
            f"[yellow]{summary.coverage_gaps} gaps[/yellow]  "
            f"[dim]{summary.auto_closed} auto-closed[/dim]  "
            f"[green]{summary.checkpoints_clean} clean[/green]"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@recon_app.command("list")
def recon_list(
    db: Path = _DB_PATH_OPT,
    status: str = typer.Option("open", "--status", "-s", help="Filter by status (open/resolved/auto_closed/all)"),
) -> None:
    """List reconciliation breaks."""
    from sqlalchemy import select

    from committee.db import get_session, init_db
    from committee.models import Instrument, ReconBreak

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        q = select(ReconBreak).order_by(ReconBreak.as_of.desc(), ReconBreak.id)
        if status != "all":
            q = q.where(ReconBreak.status == status)
        breaks = session.execute(q).scalars().all()

        if not breaks:
            console.print(f"[green]No breaks with status='{status}'.[/green]")
            raise typer.Exit(0)

        tbl = Table(title=f"Recon breaks (status={status})", show_lines=False)
        tbl.add_column("ID", justify="right")
        tbl.add_column("Ticker")
        tbl.add_column("Account")
        tbl.add_column("As Of")
        tbl.add_column("Expected", justify="right")
        tbl.add_column("Actual", justify="right")
        tbl.add_column("Delta", justify="right")
        tbl.add_column("Cause")
        tbl.add_column("Gap?")
        tbl.add_column("Status")

        for brk in breaks:
            inst = session.get(Instrument, brk.instrument_id)
            ticker = inst.ticker if inst else f"inst#{brk.instrument_id}"
            delta_str = f"{brk.delta:+.4f}" if brk.delta is not None else "—"
            delta_style = "red" if brk.delta and brk.delta != 0 else "green"
            tbl.add_row(
                str(brk.id),
                ticker or "—",
                brk.account_id,
                brk.as_of.isoformat(),
                f"{brk.expected_qty:.4f}" if brk.expected_qty is not None else "—",
                f"{brk.actual_qty:.4f}" if brk.actual_qty is not None else "—",
                f"[{delta_style}]{delta_str}[/{delta_style}]",
                brk.suggested_cause or "—",
                "[yellow]yes[/yellow]" if brk.coverage_gap else "no",
                brk.status,
            )

        console.print(tbl)
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@recon_app.command("show")
def recon_show(
    break_id: int = typer.Argument(..., help="Break ID"),
    db: Path = _DB_PATH_OPT,
) -> None:
    """Show details for a single reconciliation break."""
    from committee.db import get_session, init_db
    from committee.models import Instrument, ReconBreak
    from committee.recon.projector import project_qty

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        brk = session.get(ReconBreak, break_id)
        if brk is None:
            console.print(f"[red]Break {break_id} not found.[/red]")
            raise typer.Exit(1)

        inst = session.get(Instrument, brk.instrument_id)
        console.print(f"\n[bold]Break #{brk.id}[/bold]")
        console.print(f"  Instrument : {inst.ticker if inst else '?'} (id={brk.instrument_id})")
        console.print(f"  Account    : {brk.account_id}")
        console.print(f"  As of      : {brk.as_of}")
        console.print(f"  Expected   : {brk.expected_qty}")
        console.print(f"  Actual     : {brk.actual_qty}")
        console.print(f"  Delta      : [red]{brk.delta:+.4f}[/red]" if brk.delta else "  Delta      : 0")
        console.print(f"  Cause      : {brk.suggested_cause or '—'}")
        console.print(f"  Coverage gap: {'[yellow]yes[/yellow]' if brk.coverage_gap else 'no'}")
        console.print(f"  Status     : {brk.status}")
        if brk.resolution_note:
            console.print(f"  Note       : {brk.resolution_note}")
        if brk.resolved_at:
            console.print(f"  Resolved at: {brk.resolved_at}")

        # Re-run projector to show effects
        if inst and brk.expected_qty is not None:
            from sqlalchemy import select as sa_select

            from committee.models import PositionSnapshot
            prev_snap = session.execute(
                sa_select(PositionSnapshot)
                .where(
                    PositionSnapshot.instrument_id == brk.instrument_id,
                    PositionSnapshot.account_id == brk.account_id,
                    PositionSnapshot.as_of < brk.as_of,
                    PositionSnapshot.qty.isnot(None),
                )
                .order_by(PositionSnapshot.as_of.desc())
                .limit(1)
            ).scalar_one_or_none()
            if prev_snap and prev_snap.as_of:
                result = project_qty(
                    session,
                    brk.instrument_id,
                    brk.account_id,
                    prev_snap.as_of,
                    prev_snap.qty,
                    brk.as_of,
                )
                if result.effects:
                    console.print("\n  Effects applied:")
                    for eff in result.effects:
                        console.print(f"    {eff.effect_date}  {eff.description}  (Δ {eff.delta:+.4f})")
                else:
                    console.print("\n  No qty-affecting transactions in window.")
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@recon_app.command("resolve")
def recon_resolve(
    break_id: int = typer.Argument(..., help="Break ID to resolve"),
    note: str = typer.Option(..., "--note", "-n", help="Resolution note"),
    db: Path = _DB_PATH_OPT,
) -> None:
    """Resolve a reconciliation break with a typed note (audit write)."""
    from committee.db import get_session, init_db
    from committee.recon.engine import resolve_break

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        resolve_break(session, break_id, note)
        session.commit()
        console.print(f"[green]Break #{break_id} resolved.[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


# ── Committee convene / dissent / minutes ──────────────────────────────────────


@app.command("convene")
def convene(
    db: Path = _DB_PATH_OPT,
    constraint: str = typer.Option("unconstrained", "--constraint", "-c", help="Constraint profile ID"),
    scenario: str | None = typer.Option(None, "--scenario", help="Scenario ID (optional)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show proposals without writing audit rows"),
) -> None:
    """Run all six oracles + rebalancer. Write audit rows. Show results."""
    import uuid

    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.models import Decision, Instrument, RegimeState
    from committee.oracles.runner import run_all_oracles
    from committee.rebalancer.engine import RebalanceParams, propose
    from committee.rebalancer.profiles import load_constraint_profile

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        try:
            profile = load_constraint_profile(constraint)
        except FileNotFoundError:
            console.print(f"[red]Constraint profile '{constraint}' not found.[/red]")
            raise typer.Exit(1) from None

        console.print(f"[bold]Committee convene[/bold]  constraint=[cyan]{constraint}[/cyan]")
        if dry_run:
            console.print("[yellow]dry-run mode — audit rows will be written with dry_run=True[/yellow]")

        run_id = str(uuid.uuid4())
        regime_row = session.get(RegimeState, 1)
        regime_snapshot = regime_row.tilt if regime_row else "neutral"

        oracle_outputs = run_all_oracles(session)

        # Build instruments map for display
        inst_map: dict[int, Instrument] = {}
        for inst_row in session.query(Instrument).all():
            inst_map[inst_row.id] = inst_row

        # Main results table
        tbl = Table(title=f"Committee run  {run_id[:8]}…", show_lines=True)
        tbl.add_column("Oracle", style="bold", min_width=22)
        tbl.add_column("Holdings scored", justify="right")
        tbl.add_column("Abstained", justify="right")
        tbl.add_column("Trade proposals", justify="right")
        tbl.add_column("Regime / status")

        for output in oracle_outputs:
            proposals = propose(output, session, constraint_profile=profile,
                                params=RebalanceParams(dry_run=dry_run))

            scored = sum(1 for h in output.per_holding_scores.values() if h.score is not None)
            abstained_count = sum(1 for h in output.per_holding_scores.values() if h.score is None)

            regime_info = ""
            if output.oracle_id == "macro_tactician":
                r = session.get(RegimeState, 1)
                regime_info = f"tilt={r.tilt}" if r else "no data"
            elif output.abstained:
                regime_info = f"[yellow]{output.abstain_reason}[/yellow]"

            tbl.add_row(
                output.display_name,
                str(scored),
                str(abstained_count) if abstained_count else "—",
                str(len(proposals)),
                regime_info or "—",
            )

            # Write audit row (always, even in dry_run — with flag set per Invariant I)
            if not dry_run:
                inputs_snapshot = {
                    "holdings_count": scored + abstained_count,
                    "constraint": constraint,
                    "scenario": scenario,
                }
                outputs_snapshot: dict[str, object] = {
                    "scores": {
                        str(iid): {
                            "score": hs.score,
                            "reasons": hs.reasons,
                        }
                        for iid, hs in output.per_holding_scores.items()
                    },
                    "sleeve_targets": {k: str(v) for k, v in output.sleeve_targets.items()},
                    "abstained": output.abstained,
                }
                proposals_snapshot = [
                    {
                        "instrument_id": p.instrument_id,
                        "account_id": p.account_id,
                        "direction": p.direction,
                        "qty": str(p.qty),
                        "estimated_value": str(p.estimated_value),
                        "tags": p.rationale_tags,
                        "oracle_score": p.oracle_score,
                        "tax_note": p.tax_note,
                    }
                    for p in proposals
                ]
                decision = Decision(
                    run_id=run_id,
                    oracle_name=output.display_name,
                    persona_key=output.oracle_id,
                    inputs_json=inputs_snapshot,
                    outputs_json=outputs_snapshot,
                    proposals_json=proposals_snapshot,
                    regime_state=regime_snapshot,
                    scenario_id=scenario,
                    dry_run=False,
                )
                session.add(decision)

        console.print(tbl)
        console.print(f"\n[dim]run_id {run_id}[/dim]")

        if not dry_run:
            session.commit()
            console.print(f"[green]Audit rows written ({len(oracle_outputs)} oracles).[/green]")
        else:
            session.rollback()

    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command("dissent")
def dissent(
    db: Path = _DB_PATH_OPT,
    run_id: str | None = typer.Option(None, "--run-id", help="Run ID to inspect (default: latest)"),
) -> None:
    """Six-way side-by-side: where oracles disagree and rivals object."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.models import Decision, Instrument

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        # Find the run_id to inspect
        if run_id is None:
            latest = session.execute(
                select(Decision.run_id, Decision.run_at)
                .order_by(Decision.run_at.desc())
                .limit(1)
            ).first()
            if latest is None:
                console.print("[yellow]No committee runs found. Run `committee convene` first.[/yellow]")
                raise typer.Exit(0)
            run_id = latest[0]

        rows = session.execute(
            select(Decision)
            .where(Decision.run_id == run_id)
            .order_by(Decision.run_at)
        ).scalars().all()

        if not rows:
            console.print(f"[red]No decisions found for run_id={run_id}[/red]")
            raise typer.Exit(1)

        console.print(f"\n[bold]Committee dissent[/bold]  run {run_id[:8]}…  ({rows[0].run_at.strftime('%Y-%m-%d %H:%M')})\n")

        # Collect all instrument IDs across all proposal lists
        all_inst_ids: set[int] = set()
        proposals_by_oracle: dict[str, list[dict]] = {}
        for row in rows:
            prps = row.proposals_json or []
            proposals_by_oracle[row.persona_key] = prps
            for p in prps:
                all_inst_ids.add(p["instrument_id"])

        inst_map: dict[int, str] = {}
        for iid in all_inst_ids:
            inst = session.get(Instrument, iid)
            if inst:
                inst_map[iid] = inst.ticker or str(iid)

        # Build the disagreement table: rows = instruments, cols = oracles
        oracle_order = [row.persona_key for row in rows]
        oracle_display = {row.persona_key: row.oracle_name for row in rows}

        tbl = Table(title="Trade proposals by oracle", show_lines=True)
        tbl.add_column("Instrument", style="bold")
        for oid in oracle_order:
            tbl.add_column(oracle_display.get(oid, oid)[:16], justify="center")

        # Build per-instrument, per-oracle direction
        inst_oracle: dict[int, dict[str, str]] = {}
        for oid, prps in proposals_by_oracle.items():
            for p in prps:
                iid = p["instrument_id"]
                inst_oracle.setdefault(iid, {})[oid] = p["direction"]

        for iid, directions in sorted(inst_oracle.items(), key=lambda x: inst_map.get(x[0], "")):
            ticker = inst_map.get(iid, str(iid))
            cols = []
            for oid in oracle_order:
                d = directions.get(oid)
                if d == "buy":
                    cols.append("[green]buy[/green]")
                elif d == "sell":
                    cols.append("[red]sell[/red]")
                else:
                    cols.append("[dim]—[/dim]")
            tbl.add_row(ticker, *cols)

        console.print(tbl)

        # Rivals' objections
        rival_map: dict[str, list[str]] = {}
        for row in rows:
            # Load oracle config to get rivals list
            try:
                from committee.oracles.base import load_oracle_config
                cfg = load_oracle_config(row.persona_key)
                rival_map[row.persona_key] = cfg.rivals
            except Exception:
                rival_map[row.persona_key] = []

        console.print("\n[bold]Rivals' objections[/bold]")
        for row in rows:
            rivals = rival_map.get(row.persona_key, [])
            if not rivals:
                continue
            prps = proposals_by_oracle.get(row.persona_key, [])
            if not prps:
                continue
            for rival_id in rivals:
                rival_display = oracle_display.get(rival_id, rival_id)
                rival_prps = proposals_by_oracle.get(rival_id, [])
                conflicts = []
                for p in prps:
                    rival_direction = next(
                        (rp["direction"] for rp in rival_prps if rp["instrument_id"] == p["instrument_id"]),
                        None,
                    )
                    if rival_direction and rival_direction != p["direction"]:
                        ticker = inst_map.get(p["instrument_id"], str(p["instrument_id"]))
                        conflicts.append(f"{ticker}: {oracle_display.get(row.persona_key)} {p['direction']} ↔ {rival_display} {rival_direction}")
                if conflicts:
                    console.print(f"  [yellow]{oracle_display.get(row.persona_key, row.persona_key)}[/yellow] vs [yellow]{rival_display}[/yellow]:")
                    for c in conflicts[:5]:
                        console.print(f"    {c}")

    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command("minutes")
def minutes(
    db: Path = _DB_PATH_OPT,
    n: int = typer.Option(5, "--n", help="Number of recent runs to show"),
) -> None:
    """Show the last N committee runs from the audit log."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.models import Decision

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        # Get distinct run_ids ordered by run_at desc
        run_rows = session.execute(
            select(Decision.run_id, Decision.run_at, Decision.regime_state, Decision.scenario_id)
            .order_by(Decision.run_at.desc())
        ).all()

        seen: list[tuple[str, object, object, object]] = []
        seen_ids: set[str] = set()
        for row in run_rows:
            if row[0] not in seen_ids:
                seen.append(row)
                seen_ids.add(row[0])
            if len(seen) >= n:
                break

        if not seen:
            console.print("[yellow]No committee runs recorded yet.[/yellow]")
            raise typer.Exit(0)

        tbl = Table(title="Committee minutes", show_lines=False)
        tbl.add_column("Run ID", style="dim")
        tbl.add_column("Date")
        tbl.add_column("Regime")
        tbl.add_column("Scenario")
        tbl.add_column("Oracles")
        tbl.add_column("Total proposals", justify="right")

        for run_id_val, run_at, regime, scenario in seen:
            oracle_rows = session.execute(
                select(Decision).where(Decision.run_id == run_id_val)
            ).scalars().all()
            total_proposals = sum(
                len(r.proposals_json or []) for r in oracle_rows
            )
            tbl.add_row(
                str(run_id_val)[:8] + "…",
                run_at.strftime("%Y-%m-%d %H:%M") if run_at else "—",
                str(regime or "neutral"),
                str(scenario or "—"),
                str(len(oracle_rows)),
                str(total_proposals),
            )

        console.print(tbl)

    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command("scenario")
def run_scenario(
    pack: str = typer.Argument(..., help="Scenario pack ID (e.g. gfc-2008, covid-2020)"),
    db: Path = _DB_PATH_OPT,
    constraint: str = typer.Option("unconstrained", "--constraint", "-c", help="Constraint profile ID"),
    scenarios_dir: Path = typer.Option(Path("scenarios"), "--scenarios", help="Scenarios directory"),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run", help="Show results without writing audit rows"),
) -> None:
    """Replay a scenario pack: shock the portfolio and re-run all oracles deterministically."""
    import uuid

    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.models import Decision, Instrument, RegimeState
    from committee.oracles.runner import run_all_oracles
    from committee.rebalancer.engine import RebalanceParams, propose
    from committee.rebalancer.profiles import load_constraint_profile
    from committee.scenarios.loader import list_packs, load_pack

    init_db(db)

    try:
        pack_obj = load_pack(pack, scenarios_dir)
    except FileNotFoundError as e:
        available = list_packs(scenarios_dir)
        console.print(f"[red]{e}[/red]")
        if available:
            console.print(f"[yellow]Available packs: {', '.join(available)}[/yellow]")
        raise typer.Exit(1) from None

    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        try:
            profile = load_constraint_profile(constraint)
        except FileNotFoundError:
            console.print(f"[red]Constraint profile '{constraint}' not found.[/red]")
            raise typer.Exit(1) from None

        label = "[yellow](hypothetical)[/yellow]" if pack_obj.pack_type == "hypothetical" else "[cyan](historical)[/cyan]"
        console.print(f"\n[bold]Committee scenario[/bold]  {pack_obj.display_name}  {label}")
        console.print(f"[dim]pack_id={pack}  constraint={constraint}[/dim]\n")

        # Honesty note
        console.print(f"[yellow]Honesty note:[/yellow] {pack_obj.honesty_note}\n")

        # Sources
        if pack_obj.sources:
            console.print("[dim]Sources:[/dim]")
            for src in pack_obj.sources:
                console.print(f"  [dim]• {src}[/dim]")
            console.print()

        # Show sleeve shocks
        shock_tbl = Table(title="Sleeve shocks", show_header=True)
        shock_tbl.add_column("Sleeve")
        shock_tbl.add_column("Shock", justify="right")
        for sleeve, shock in sorted(pack_obj.context.sleeve_shocks.items()):
            pct = float(shock) * 100
            color = "red" if pct < 0 else "green"
            shock_tbl.add_row(sleeve, f"[{color}]{pct:+.1f}%[/{color}]")
        console.print(shock_tbl)

        # Show indicator overrides
        if pack_obj.context.indicator_overrides:
            ind_tbl = Table(title="Indicator overrides", show_header=True)
            ind_tbl.add_column("Series")
            ind_tbl.add_column("Override value", justify="right")
            for series_id, val in sorted(pack_obj.context.indicator_overrides.items()):
                ind_tbl.add_row(series_id, f"{val:.2f}")
            console.print(ind_tbl)

        console.print()

        run_id = str(uuid.uuid4())
        regime_row = session.get(RegimeState, 1)
        regime_snapshot = regime_row.tilt if regime_row else "neutral"

        # Run all oracles with shocked inputs; regime state is NOT persisted (what-if run)
        oracle_outputs = run_all_oracles(session, scenario=pack_obj.context)

        inst_map: dict[int, Instrument] = {}
        for inst_row in session.query(Instrument).all():
            inst_map[inst_row.id] = inst_row

        results_tbl = Table(title=f"Scenario results  {run_id[:8]}…", show_lines=True)
        results_tbl.add_column("Oracle", style="bold", min_width=22)
        results_tbl.add_column("Holdings scored", justify="right")
        results_tbl.add_column("Abstained", justify="right")
        results_tbl.add_column("Trade proposals", justify="right")
        results_tbl.add_column("Regime / status")

        all_proposals_by_oracle: list[tuple[str, list]] = []

        for output in oracle_outputs:
            proposals = propose(
                output, session,
                constraint_profile=profile,
                params=RebalanceParams(dry_run=dry_run),
                scenario=pack_obj.context,
            )
            all_proposals_by_oracle.append((output.oracle_id, proposals))

            scored = sum(1 for h in output.per_holding_scores.values() if h.score is not None)
            abstained_count = sum(1 for h in output.per_holding_scores.values() if h.score is None)

            regime_info = ""
            if output.oracle_id == "macro_tactician":
                # Regime FSM ran but was NOT persisted — show what it would have done
                regime_info = "scenario tilt (not saved)"
            elif output.abstained:
                regime_info = f"[yellow]{output.abstain_reason}[/yellow]"

            results_tbl.add_row(
                output.display_name,
                str(scored),
                str(abstained_count) if abstained_count else "—",
                str(len(proposals)),
                regime_info or "—",
            )

        console.print(results_tbl)
        console.print(f"\n[dim]run_id {run_id}[/dim]")

        # Write audit rows (always, per Invariant I — scenario runs recorded with scenario_id)
        if not dry_run:
            for output, (_, proposals) in zip(oracle_outputs, all_proposals_by_oracle, strict=False):
                scored = sum(1 for h in output.per_holding_scores.values() if h.score is not None)
                abstained_count = sum(1 for h in output.per_holding_scores.values() if h.score is None)
                inputs_snapshot = {
                    "holdings_count": scored + abstained_count,
                    "constraint": constraint,
                    "scenario": pack,
                    "sleeve_shocks": {k: str(v) for k, v in sorted(pack_obj.context.sleeve_shocks.items())},
                    "indicator_overrides": {k: v for k, v in sorted(pack_obj.context.indicator_overrides.items())},
                }
                outputs_snapshot: dict[str, object] = {
                    "scores": {
                        str(iid): {"score": hs.score, "reasons": hs.reasons}
                        for iid, hs in sorted(output.per_holding_scores.items())
                    },
                    "sleeve_targets": {k: str(v) for k, v in sorted(output.sleeve_targets.items())},
                    "abstained": output.abstained,
                }
                proposals_snapshot = [
                    {
                        "instrument_id": p.instrument_id,
                        "account_id": p.account_id,
                        "direction": p.direction,
                        "qty": str(p.qty),
                        "estimated_value": str(p.estimated_value),
                        "tags": p.rationale_tags,
                        "oracle_score": p.oracle_score,
                        "tax_note": p.tax_note,
                    }
                    for p in proposals
                ]
                decision = Decision(
                    run_id=run_id,
                    oracle_name=output.display_name,
                    persona_key=output.oracle_id,
                    inputs_json=inputs_snapshot,
                    outputs_json=outputs_snapshot,
                    proposals_json=proposals_snapshot,
                    regime_state=regime_snapshot,
                    scenario_id=pack,
                    dry_run=False,
                )
                session.add(decision)
            session.commit()
            console.print(f"[green]Audit rows written (scenario_id={pack}).[/green]")
        else:
            console.print("[yellow]dry-run — no audit rows written.[/yellow]")

    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command("unwind")
def unwind(
    db: Path = _DB_PATH_OPT,
    st_rate: float = typer.Option(0.37, "--st-rate", help="Short-term marginal tax rate (e.g. 0.37)"),
    lt_rate: float = typer.Option(0.20, "--lt-rate", help="Long-term marginal tax rate (e.g. 0.20)"),
    account: str | None = typer.Option(None, "--account", "-a", help="Filter by account ID"),
) -> None:
    """Lot-level unwind analysis for individual (non-fund) positions.

    Shows unrealized gain/loss, ST/LT status, wash-sale risk, and estimated tax impact.
    This is information only — not a directive. Consult a qualified tax advisor.
    """
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.lots.analysis import LotAnalysis, compute_unwind_analysis, estimated_tax
    from committee.lots.derive import derive_lots
    from committee.models import Instrument

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        # Rebuild lots (derived data) before analysis
        derive_lots(session)

        today = _date.today()
        st = _Decimal(str(st_rate))
        lt = _Decimal(str(lt_rate))

        analyses = compute_unwind_analysis(session, today, st, lt, account_filter=account)

        if not analyses:
            unwind_insts = session.query(Instrument).filter(Instrument.needs_unwind.is_(True)).count()
            if unwind_insts == 0:
                console.print("[yellow]No individual (needs_unwind) instruments in the portfolio.[/yellow]")
            else:
                console.print("[yellow]No lot data available. Import transactions or positions with cost basis.[/yellow]")
            raise typer.Exit(0)

        console.print(
            f"\n[bold]Individual position unwind analysis[/bold]"
            f"  [dim](ST rate {st_rate:.0%}  LT rate {lt_rate:.0%})[/dim]"
        )
        console.print(
            "[yellow]This is information only — not a directive. "
            "Consult a qualified tax advisor.[/yellow]\n"
        )

        # Group by instrument + account
        groups: dict[tuple[int, str], list[LotAnalysis]] = {}
        for a in analyses:
            key = (a.lot.instrument_id, a.lot.account_id)
            groups.setdefault(key, []).append(a)

        for (iid, acct_id), group in sorted(
            groups.items(), key=lambda kv: (kv[1][0].instrument.ticker or "", kv[0][1])
        ):
            inst = group[0].instrument
            ticker = inst.ticker or f"inst#{iid}"
            price_str = f"${group[0].current_price:,.2f}" if group[0].current_price else "n/a"
            has_fallback = any(a.basis_quality == "average_fallback" for a in group)

            console.rule(f"[bold]{ticker}[/bold]  {inst.name or ''}  [{acct_id}]  price={price_str}")

            if has_fallback:
                console.print("  [yellow]⚠ average-cost basis — lot detail unavailable; all shares treated as one lot[/yellow]")

            tbl = Table(show_header=True, show_lines=False, box=None, padding=(0, 1))
            tbl.add_column("Acquired", style="dim")
            tbl.add_column("Qty", justify="right")
            tbl.add_column("Cost/sh", justify="right")
            tbl.add_column("Gain/Loss", justify="right")
            tbl.add_column("Status")
            tbl.add_column("Est. Tax", justify="right")
            tbl.add_column("Note")

            for a in group:
                gain_str = "n/a"
                gain_style = ""
                if a.unrealized_gain is not None:
                    gain_style = "red" if a.unrealized_gain < 0 else "green"
                    gain_str = f"[{gain_style}]{a.unrealized_gain:+,.2f}[/{gain_style}]"

                status_str = "[cyan]LT[/cyan]" if a.long_term else "[yellow]ST[/yellow]"
                if not a.long_term and a.days_to_lt is not None:
                    status_str += f" [dim]({a.days_to_lt}d to LT)[/dim]"

                tax_str = "n/a"
                if a.unrealized_gain is not None:
                    tax_val = estimated_tax(a.unrealized_gain, a.long_term, st, lt)
                    tax_style = "red" if tax_val > 0 else "green"
                    tax_str = f"[{tax_style}]{tax_val:+,.2f}[/{tax_style}]"

                note = ""
                if a.wash_sale_flagged:
                    note = "[red]⚠ wash-sale risk (recent buy within 30d)[/red]"
                elif not a.long_term and a.days_to_lt and a.days_to_lt <= 30:
                    note = "[dim]near LT boundary[/dim]"

                tbl.add_row(
                    a.lot.acquired_date.isoformat(),
                    f"{a.lot.qty:,.4f}",
                    f"${a.lot.cost_per_share:,.2f}",
                    gain_str,
                    status_str,
                    tax_str,
                    note,
                )

            console.print(tbl)

            # Ordering suggestion
            losses = [a for a in group if a.unrealized_gain is not None and a.unrealized_gain < 0]
            lt_gains = [a for a in group if a.long_term and a.unrealized_gain is not None and a.unrealized_gain >= 0]
            st_gains = [a for a in group if not a.long_term and a.unrealized_gain is not None and a.unrealized_gain >= 0]
            parts = []
            if losses:
                parts.append(f"[green]{len(losses)} loss lot(s) first[/green]")
            if lt_gains:
                parts.append(f"[cyan]{len(lt_gains)} LT gain lot(s) next[/cyan]")
            if st_gains:
                min_wait = min(a.days_to_lt or 0 for a in st_gains)
                parts.append(f"[yellow]{len(st_gains)} ST gain lot(s) last[/yellow] [dim](wait up to {min_wait}d for LT)[/dim]")
            if parts:
                console.print("  Suggested order if selling: " + " → ".join(parts))
            console.print()

    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


_REASON_TYPES = ("conviction", "tax_cost", "liquidity", "risk_budget", "timing", "other")


@app.command("deviate")
def deviate(
    db: Path = _DB_PATH_OPT,
    run_id: str | None = typer.Option(None, "--run-id", help="Committee run ID (default: latest)"),
    instrument: str | None = typer.Option(None, "--instrument", "-i", help="Ticker of affected instrument"),
    user_action: str | None = typer.Option(None, "--user-action", "-u", help="What you chose to do"),
    reason_type: str | None = typer.Option(None, "--reason", "-r", help=f"Reason type: {'/'.join(_REASON_TYPES)}"),
    note: str | None = typer.Option(None, "--note", "-n", help="Optional free-text note"),
    history: bool = typer.Option(False, "--history", "-H", help="Show deviation history and exit"),
) -> None:
    """Record a deviation from the committee's recommendation (personal mode).

    Writes to the deviations audit table and surfaces the running override history.
    All output framed as a record, not as advice.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session as _Session

    from committee.db import get_session, init_db
    from committee.models import Decision, Deviation, Instrument

    init_db(db)
    gen = get_session()
    session = next(gen)
    if not isinstance(session, _Session):
        return
    try:
        # Always show history
        _show_deviation_history(session, console)

        if history:
            raise typer.Exit(0)

        # Resolve run_id
        if run_id is None:
            latest = session.execute(
                select(Decision.run_id, Decision.run_at)
                .order_by(Decision.run_at.desc())
                .limit(1)
            ).first()
            if latest is None:
                console.print(
                    "[red]No committee runs found. Run `committee convene` first.[/red]"
                )
                raise typer.Exit(1)
            run_id = latest[0]

        decision_rows = session.execute(
            select(Decision).where(Decision.run_id == run_id).order_by(Decision.id)
        ).scalars().all()

        if not decision_rows:
            console.print(f"[red]No decisions found for run_id={run_id}[/red]")
            raise typer.Exit(1)

        console.print(f"\n[bold]Recording deviation[/bold]  run {run_id[:8]}…")

        # Resolve instrument
        inst: Instrument | None = None
        if instrument is None:
            instrument = typer.prompt("Ticker (or 'none' for portfolio-level)").strip()
        if instrument.lower() != "none":
            inst = session.execute(
                select(Instrument).where(Instrument.ticker == instrument.upper())
            ).scalar_one_or_none()
            if inst is None:
                console.print(f"[yellow]Ticker '{instrument}' not found — recording without instrument link.[/yellow]")

        # Derive committee_action from proposals across all oracles for this run
        committee_action = _derive_committee_action(decision_rows, inst.id if inst else None)

        # Collect user_action
        if user_action is None:
            console.print(f"  Committee proposed: [cyan]{committee_action}[/cyan]")
            user_action = typer.prompt("  What did you do instead?").strip()

        # Validate reason_type
        if reason_type is None:
            rt_display = " / ".join(_REASON_TYPES)
            reason_type = typer.prompt(f"  Reason type ({rt_display})").strip().lower()
        if reason_type not in _REASON_TYPES:
            console.print(f"[yellow]Unknown reason type '{reason_type}' — recording as 'other'.[/yellow]")
            reason_type = "other"

        if note is None:
            raw_note = typer.prompt("  Note (optional, Enter to skip)", default="").strip()
            note = raw_note or None

        # Write to deviations table (Invariant I — always write, no dry-run skip)
        deviation = Deviation(
            decision_id=decision_rows[0].id,
            instrument_id=inst.id if inst else None,
            committee_action=committee_action,
            user_action=user_action,
            reason_type=reason_type,
            reason_note=note,
        )
        session.add(deviation)
        session.commit()

        ticker_label = instrument.upper() if instrument and instrument.lower() != "none" else "portfolio-level"
        console.print(
            f"\n[green]Deviation recorded[/green]  "
            f"instrument={ticker_label}  "
            f"reason={reason_type}  "
            f"id={deviation.id}"
        )

    except SystemExit:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        with contextlib.suppress(StopIteration):
            next(gen)


def _derive_committee_action(decision_rows: list, instrument_id: int | None) -> str:
    """Summarize the committee's proposal for a given instrument across all oracle rows."""
    if instrument_id is None:
        return "portfolio-level (no specific instrument)"

    directions: dict[str, int] = {}
    for row in decision_rows:
        for p in row.proposals_json or []:
            if p.get("instrument_id") == instrument_id:
                d = p.get("direction", "hold")
                directions[d] = directions.get(d, 0) + 1

    if not directions:
        return "no proposal (hold)"

    summary_parts = [f"{d} ({n}/{len(decision_rows)} oracles)" for d, n in sorted(directions.items(), key=lambda x: -x[1])]
    return "; ".join(summary_parts)


def _show_deviation_history(session: object, con: Console) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as _Session

    from committee.models import Deviation, Instrument

    if not isinstance(session, _Session):
        return

    rows = session.execute(
        select(Deviation).order_by(Deviation.recorded_at.desc()).limit(20)
    ).scalars().all()

    if not rows:
        con.print("[dim]No deviations recorded yet.[/dim]\n")
        return

    tbl = Table(title="Deviation history (last 20)", show_lines=False)
    tbl.add_column("ID", justify="right", style="dim")
    tbl.add_column("Date")
    tbl.add_column("Ticker")
    tbl.add_column("Committee proposed")
    tbl.add_column("User did")
    tbl.add_column("Reason")
    tbl.add_column("Note")

    for dev in rows:
        inst = session.get(Instrument, dev.instrument_id) if dev.instrument_id else None
        ticker = inst.ticker if inst else "—"
        date_str = dev.recorded_at.strftime("%Y-%m-%d") if dev.recorded_at else "—"
        tbl.add_row(
            str(dev.id),
            date_str,
            ticker,
            dev.committee_action[:40],
            dev.user_action[:40],
            dev.reason_type,
            (dev.reason_note or "")[:30],
        )

    con.print(tbl)


@app.command("backtest")
def backtest(
    db: Path = _DB_PATH_OPT,
    date_from: str = typer.Option("", "--from", help="Start date YYYY-MM-DD (default: 1 year ago)"),
    date_to: str = typer.Option("", "--to", help="End date YYYY-MM-DD (default: today)"),
    perturb: bool = typer.Option(False, "--perturb", help="Include ±50% drift-parameter perturbation report"),
) -> None:
    """Point-in-time regime-tilt backtest vs. Passive Pragmatist benchmark.

    Replays Macro Tactician's sleeve targets using only data stamped ≤ each
    date, measuring whether the tilt passes the pre-committed bar from DESIGN §11:
    cuts max drawdown ≥20% relative while costing ≤1% CAGR with <10 switches/decade.
    Fails bar → Macro Tactician labeled entertainment-only.

    Invariant H: all numbers come from sourced inputs; 'n/a' when insufficient history.
    """
    from datetime import date as _date

    from committee.backtest.engine import run_backtest
    from committee.db import get_session, init_db

    today = _date.today()
    try:
        d_from = _date.fromisoformat(date_from) if date_from else today.replace(year=today.year - 1)
        d_to = _date.fromisoformat(date_to) if date_to else today
    except ValueError as e:
        console.print(f"[red]Invalid date format: {e}[/red]")
        raise typer.Exit(1) from None

    init_db(db)
    gen = get_session()
    session = next(gen)
    try:
        console.print(f"\n[bold]Committee backtest[/bold]  {d_from} → {d_to}")
        console.print("[dim]Benchmark: Passive Pragmatist (neutral allocation)[/dim]")
        console.print("[dim]Tilt: Macro Tactician (regime-driven)[/dim]\n")

        report = run_backtest(session, d_from, d_to, perturb=perturb)

        if report.note:
            console.print(f"[yellow]Note: {report.note.replace('_', ' ')}[/yellow]")
            console.print("[dim]Import positions and price history, then retry.[/dim]")
            raise typer.Exit(0)

        def _fmt(v: float | None, pct: bool = True) -> str:
            if v is None:
                return "n/a"
            return f"{v:+.2%}" if pct else f"{v:.2%}"

        # Main metrics table
        tbl = Table(title=f"Backtest metrics  ({report.date_from} → {report.date_to})", show_lines=True)
        tbl.add_column("Oracle", style="bold")
        tbl.add_column("CAGR", justify="right")
        tbl.add_column("Max DD", justify="right")
        tbl.add_column("Ulcer", justify="right")
        tbl.add_column("Switches", justify="right")

        for m in (report.benchmark_metrics, report.tilt_metrics):
            if m.oracle_id == "passive_pragmatist":
                label = "Passive Pragmatist (benchmark)"
            else:
                bar = report.pass_bar
                label_suffix = ""
                if bar is not None:
                    label_suffix = (
                        "  [green][ACTIVE][/green]" if bar.passes
                        else "  [yellow][entertainment-only][/yellow]"
                    )
                label = f"Macro Tactician{label_suffix}"
            tbl.add_row(
                label,
                _fmt(m.cagr),
                _fmt(m.max_drawdown, pct=False) if m.max_drawdown is not None else "n/a",
                f"{m.ulcer_index:.3f}" if m.ulcer_index is not None else "n/a",
                str(m.switch_count) if m.oracle_id == "macro_tactician" else "—",
            )
        console.print(tbl)

        # Pass-bar verdict
        if report.pass_bar:
            bar = report.pass_bar
            color = "green" if bar.passes else "yellow"
            console.print(f"\n[bold]Pass-bar verdict[/bold] [{color}]{bar.verdict}[/{color}]")
            console.print(f"[dim]Fallback mode: {bar.fallback_mode}[/dim]")
            if not bar.passes:
                console.print(
                    "[dim]Macro Tactician tilt does not earn live influence. "
                    "Available in `committee dissent` labeled entertainment-only.[/dim]"
                )

        # Perturbation report
        if report.perturbation:
            console.print()
            ptbl = Table(title="±50% drift-abs perturbation", show_lines=False)
            ptbl.add_column("drift_abs", justify="right")
            ptbl.add_column("Tilt CAGR", justify="right")
            ptbl.add_column("Tilt Max DD", justify="right")
            ptbl.add_column("Ann. Turnover", justify="right")
            for row in report.perturbation:
                ptbl.add_row(
                    f"{row.drift_abs:.3f}",
                    _fmt(row.cagr),
                    _fmt(row.max_drawdown, pct=False) if row.max_drawdown is not None else "n/a",
                    _fmt(row.annualized_turnover) if row.annualized_turnover is not None else "n/a",
                )
            console.print(ptbl)

        # Shadow stance reminder
        console.print(
            "\n[dim]Shadow stance: run without acting for the first few months; "
            "grade tilt against what you'd have done before trusting it.[/dim]"
        )

    finally:
        import contextlib
        with contextlib.suppress(StopIteration):
            next(gen)


@app.command("demo")
def demo(
    db: Path = typer.Option(Path("data/demo.db"), "--db", help="Demo database path"),  # noqa: B008
    open_browser: bool = typer.Option(False, "--open/--no-open", help="Start server and open browser"),  # noqa: B008
) -> None:
    """Seed a synthetic demo database and optionally launch the dashboard.

    Creates a self-contained demo — no real portfolio data required.
    Run committee demo --open to see the full dashboard in one command.
    """
    import os
    import subprocess
    import sys
    import threading
    import time
    import webbrowser
    from datetime import date, datetime
    from decimal import Decimal

    import requests as _requests
    from sqlalchemy import select

    from committee.api.deps import init_engine
    from committee.db import get_session, init_db
    from committee.models import (
        Account,
        Holding,
        ImportBatch,
        Instrument,
        MarketObservation,
        PositionSnapshot,
    )

    db.parent.mkdir(parents=True, exist_ok=True)
    init_db(db)

    INSTRUMENTS = [
        dict(ticker="VTSAX", name="Vanguard Total Stock Market Index Fund Admiral Shares",
             instrument_type="mutual_fund", sleeve="equity_us", is_cash_equivalent=False),
        dict(ticker="VTIAX", name="Vanguard Total International Stock Index Fund Admiral Shares",
             instrument_type="mutual_fund", sleeve="equity_intl", is_cash_equivalent=False),
        dict(ticker="VBTLX", name="Vanguard Total Bond Market Index Fund Admiral Shares",
             instrument_type="mutual_fund", sleeve="fixed_income", is_cash_equivalent=False),
        dict(ticker="VMFXX", name="Vanguard Federal Money Market Fund",
             instrument_type="mutual_fund", sleeve="cash", is_cash_equivalent=True),
        dict(ticker="VFIFX", name="Vanguard Target Retirement 2050 Fund",
             instrument_type="mutual_fund", sleeve="alternatives", is_cash_equivalent=False),
    ]

    HOLDINGS = [
        dict(ticker="VTSAX", qty=Decimal("500"), market_value=Decimal("73800")),
        dict(ticker="VTIAX", qty=Decimal("200"), market_value=Decimal("12800")),
        dict(ticker="VBTLX", qty=Decimal("100"), market_value=Decimal("4600")),
        dict(ticker="VMFXX", qty=Decimal("2900"), market_value=Decimal("2900")),
        dict(ticker="VFIFX", qty=Decimal("300"), market_value=Decimal("7400")),
    ]

    MARKET_DATA = [
        dict(series_id="T10Y3M", observed_date=date(2024, 12, 31), value=Decimal("0.25")),
        dict(series_id="BAMLH0A0HYM2", observed_date=date(2024, 12, 31), value=Decimal("3.10")),
        dict(series_id="BAMLH0A0HYM2", observed_date=date(2024, 11, 30), value=Decimal("3.05")),
        dict(series_id="VIXCLS", observed_date=date(2024, 12, 31), value=Decimal("17.5")),
        dict(series_id="UNRATE", observed_date=date(2024, 12, 31), value=Decimal("4.1")),
    ]

    console.print("[bold]Seeding demo database...[/bold]")

    for session in get_session():
        # ImportBatch — required FK for PositionSnapshot
        batch = session.execute(
            select(ImportBatch).where(ImportBatch.file_hash == "demo-synthetic-v1")
        ).scalar_one_or_none()
        if not batch:
            batch = ImportBatch(
                file_hash="demo-synthetic-v1",
                original_filename="demo_synthetic.csv",
                file_type="positions",
                row_count=5,
                imported_at=datetime(2024, 12, 31),
            )
            session.add(batch)
            session.flush()

        # Instruments
        ticker_to_id: dict[str, int] = {}
        for d in INSTRUMENTS:
            inst = session.execute(
                select(Instrument).where(Instrument.ticker == d["ticker"])
            ).scalar_one_or_none()
            if not inst:
                inst = Instrument(
                    ticker=d["ticker"],
                    name=d["name"],
                    instrument_type=d["instrument_type"],
                    sleeve=d["sleeve"],
                    is_cash_equivalent=d["is_cash_equivalent"],
                    needs_unwind=False,
                    aliases=[],
                    bundle_tags=[],
                )
                session.add(inst)
                session.flush()
            ticker_to_id[d["ticker"]] = inst.id

        # Account
        acct = session.execute(
            select(Account).where(Account.account_key == "DEMO_VANGUARD")
        ).scalar_one_or_none()
        if not acct:
            acct = Account(account_key="DEMO_VANGUARD", broker="vanguard",
                           tax_type="taxable", label="Vanguard Brokerage (Demo)")
            session.add(acct)
            session.flush()

        # PositionSnapshots + Holdings
        as_of = date(2024, 12, 31)
        for h in HOLDINGS:
            inst_id = ticker_to_id[h["ticker"]]
            snap = session.execute(
                select(PositionSnapshot).where(
                    PositionSnapshot.batch_id == batch.id,
                    PositionSnapshot.instrument_id == inst_id,
                )
            ).scalar_one_or_none()
            if not snap:
                session.add(PositionSnapshot(
                    batch_id=batch.id,
                    account_id=str(acct.id),
                    instrument_id=inst_id,
                    raw_instrument=h["ticker"],
                    as_of=as_of,
                    qty=h["qty"],
                    market_value=h["market_value"],
                ))
            holding = session.execute(
                select(Holding).where(Holding.instrument_id == inst_id)
            ).scalar_one_or_none()
            if not holding:
                session.add(Holding(
                    instrument_id=inst_id,
                    account_id=str(acct.id),
                    qty=h["qty"],
                    market_value=h["market_value"],
                    as_of=as_of,
                ))

        # Market observations
        for obs in MARKET_DATA:
            existing = session.execute(
                select(MarketObservation).where(
                    MarketObservation.series_id == obs["series_id"],
                    MarketObservation.observed_date == obs["observed_date"],
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(MarketObservation(
                    source="fred",
                    series_id=obs["series_id"],
                    observed_date=obs["observed_date"],
                    value=obs["value"],
                    degraded=False,
                ))

    console.print("[green]OK[/green] Instruments, holdings, and market data seeded")

    # Run convene non-interactively
    console.print("[bold]Running committee convene...[/bold]")
    result = subprocess.run(
        [sys.executable, "-m", "committee.cli", "convene", "--db", str(db)],
        capture_output=False,
    )
    if result.returncode != 0:
        console.print("[yellow]convene finished with warnings — dashboard will still show data[/yellow]")

    # Build dashboard if dist/ is absent
    dist_dir = Path("dashboard/dist")
    if not dist_dir.exists():
        console.print("[bold]Building dashboard (npm install && npm run build)...[/bold]")
        console.print("[dim]Requires Node.js v18+. Run once; subsequent demo runs skip this.[/dim]")
        npm = subprocess.run(["npm", "install"], cwd="dashboard", check=False)
        if npm.returncode != 0:
            console.print("[red]npm install failed — is Node.js v18+ installed?[/red]")
            raise typer.Exit(1)
        build = subprocess.run(["npm", "run", "build"], cwd="dashboard", check=False)
        if build.returncode != 0:
            console.print("[red]npm run build failed[/red]")
            raise typer.Exit(1)
        console.print("[green]OK[/green] Dashboard built")

    if not open_browser:
        console.print(
            f"\n[bold green]Demo ready.[/bold green] "
            f"Run: [cyan]committee serve --db {db}[/cyan]"
        )
        return

    # Start server in background, probe readiness, then open browser
    resolved = db.resolve()
    init_db(db)
    init_engine(resolved)
    os.environ["COMMITTEE_DB"] = str(resolved)

    import uvicorn  # noqa: PLC0415

    port = 7777
    url = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs=dict(
            app="committee.api.app:app",
            host="127.0.0.1",
            port=port,
            log_level="warning",
        ),
        daemon=True,
    )
    server_thread.start()

    console.print(f"\n[bold green]Delphic Ledger[/bold green] — {url}")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    for _ in range(50):  # 10s max
        time.sleep(0.2)
        try:
            _requests.get(url, timeout=0.5)
            break
        except Exception:
            continue

    webbrowser.open(url)

    with contextlib.suppress(KeyboardInterrupt):
        server_thread.join()


@app.command("serve")
def serve(
    db: Path = _DB_PATH_OPT,
    port: int = typer.Option(7777, "--port", help="Port to bind (127.0.0.1 only)"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser on start"),
) -> None:
    """Launch the local dashboard (127.0.0.1 only, read-only).

    Starts the FastAPI API server and serves the React frontend from dashboard/dist/.
    During development, run `cd dashboard && npm run dev` separately for hot reload.
    """
    import os

    import uvicorn

    from committee.api.deps import init_engine
    from committee.db import init_db

    resolved = db.resolve()
    init_db(db)
    init_engine(resolved)
    os.environ["COMMITTEE_DB"] = str(resolved)

    url = f"http://127.0.0.1:{port}"
    console.print(f"\n[bold green]Delphic Ledger[/bold green] — {url}")
    console.print(f"[dim]API docs: {url}/api/docs[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    if open_browser:
        import threading
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "committee.api.app:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    app()
