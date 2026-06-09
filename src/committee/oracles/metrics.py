"""Raw metric computation from the database.

Returns dict[instrument_id → raw float | None] for each metric.
None means the data needed to compute this metric is unavailable.
'n/a' (string) is handled at the scoring layer; here we return None.
All financial arithmetic uses float (these are ratios, not dollar amounts).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from committee.core.types import ScenarioContext
from committee.models import Fundamental, Holding, Instrument, MarketObservation


def _latest_prices(session: Session, pit_date: date | None = None) -> dict[int, float]:
    """Latest USD_adj_close per instrument, optionally bounded to pit_date."""
    where = [
        MarketObservation.instrument_id.isnot(None),
        MarketObservation.unit == "USD_adj_close",
        MarketObservation.degraded == False,  # noqa: E712
    ]
    if pit_date is not None:
        where.append(MarketObservation.observed_date <= pit_date)
    subq = (
        select(
            MarketObservation.instrument_id,
            func.max(MarketObservation.observed_date).label("max_date"),
        )
        .where(*where)
        .group_by(MarketObservation.instrument_id)
        .subquery()
    )
    rows = session.execute(
        select(MarketObservation.instrument_id, MarketObservation.value)
        .join(
            subq,
            (MarketObservation.instrument_id == subq.c.instrument_id)
            & (MarketObservation.observed_date == subq.c.max_date),
        )
        .where(MarketObservation.unit == "USD_adj_close")
    ).all()
    return {row[0]: float(row[1]) for row in rows if row[0] is not None}


def _latest_fundamental(
    session: Session, metric_name: str, pit_date: date | None = None
) -> dict[int, float]:
    """Latest value of a named fundamental metric per instrument.

    When pit_date is set, filters on filed_at (publication date) to prevent
    lookahead; falls back to period_end for rows where filed_at is NULL.
    """
    where = [Fundamental.metric == metric_name]
    if pit_date is not None:
        where.append(or_(
            and_(Fundamental.filed_at.isnot(None), Fundamental.filed_at <= pit_date),
            and_(Fundamental.filed_at.is_(None), Fundamental.period_end <= pit_date),
        ))
    subq = (
        select(
            Fundamental.instrument_id,
            func.max(Fundamental.period_end).label("max_end"),
        )
        .where(*where)
        .group_by(Fundamental.instrument_id)
        .subquery()
    )
    rows = session.execute(
        select(Fundamental.instrument_id, Fundamental.value)
        .join(
            subq,
            (Fundamental.instrument_id == subq.c.instrument_id)
            & (Fundamental.period_end == subq.c.max_end),
        )
        .where(Fundamental.metric == metric_name)
    ).all()
    return {row[0]: float(row[1]) for row in rows}


def _price_history(
    session: Session, days_back: int = 400, pit_date: date | None = None
) -> dict[int, list[tuple[date, float]]]:
    """Recent price observations per instrument, sorted ascending by date."""
    upper = pit_date if pit_date is not None else date.today()
    cutoff = upper - timedelta(days=days_back)
    rows = session.execute(
        select(
            MarketObservation.instrument_id,
            MarketObservation.observed_date,
            MarketObservation.value,
        )
        .where(
            MarketObservation.instrument_id.isnot(None),
            MarketObservation.unit == "USD_adj_close",
            MarketObservation.observed_date >= cutoff,
            MarketObservation.observed_date <= upper,
            MarketObservation.degraded == False,  # noqa: E712
        )
        .order_by(
            MarketObservation.instrument_id,
            MarketObservation.observed_date,
        )
    ).all()
    result: dict[int, list[tuple[date, float]]] = {}
    for iid, obs_date, val in rows:
        if iid is not None:
            result.setdefault(iid, []).append((obs_date, float(val)))
    return result


def _distribution_yields(
    session: Session, pit_date: date | None = None
) -> dict[int, float]:
    """Latest distribution_yield per instrument (bulk fetch — fund-specific unit)."""
    where = [
        MarketObservation.instrument_id.isnot(None),
        MarketObservation.unit == "distribution_yield",
    ]
    if pit_date is not None:
        where.append(MarketObservation.observed_date <= pit_date)
    subq = (
        select(
            MarketObservation.instrument_id,
            func.max(MarketObservation.observed_date).label("max_date"),
        )
        .where(*where)
        .group_by(MarketObservation.instrument_id)
        .subquery()
    )
    rows = session.execute(
        select(MarketObservation.instrument_id, MarketObservation.value)
        .join(
            subq,
            (MarketObservation.instrument_id == subq.c.instrument_id)
            & (MarketObservation.observed_date == subq.c.max_date),
        )
        .where(MarketObservation.unit == "distribution_yield")
    ).all()
    return {row[0]: float(row[1]) for row in rows if row[0] is not None}


def _annual_dividends(
    session: Session, pit_date: date | None = None
) -> dict[int, list[tuple[date, float]]]:
    """All USD_dividend observations per instrument."""
    where = [
        MarketObservation.instrument_id.isnot(None),
        MarketObservation.unit == "USD_dividend",
    ]
    if pit_date is not None:
        where.append(MarketObservation.observed_date <= pit_date)
    rows = session.execute(
        select(
            MarketObservation.instrument_id,
            MarketObservation.observed_date,
            MarketObservation.value,
        )
        .where(*where)
        .order_by(MarketObservation.instrument_id, MarketObservation.observed_date)
    ).all()
    result: dict[int, list[tuple[date, float]]] = {}
    for iid, obs_date, val in rows:
        if iid is not None:
            result.setdefault(iid, []).append((obs_date, float(val)))
    return result


def _compute_rsi14(prices: list[float]) -> float | None:
    """RSI(14) from a list of closing prices (at least 15 values)."""
    if len(prices) < 15:
        return None
    gains, losses = [], []
    for i in range(1, 15):
        diff = prices[-15 + i] - prices[-15 + i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _compute_ma_cross(prices: list[float]) -> float | None:
    """50/200 MA cross signal: (50MA - 200MA) / 200MA.

    Returns positive when golden cross, negative when death cross.
    Requires 200+ price observations.
    """
    if len(prices) < 200:
        return None
    ma200 = sum(prices[-200:]) / 200
    ma50 = sum(prices[-50:]) / 50
    if ma200 == 0:
        return None
    return (ma50 - ma200) / ma200


def _compute_beta(
    instrument_prices: list[float], spy_prices: list[float], min_obs: int = 60
) -> float | None:
    """Beta vs SPY.  Requires aligned price lists of equal length."""
    n = min(len(instrument_prices), len(spy_prices))
    if n < min_obs + 1:
        return None
    inst = instrument_prices[-n:]
    spy = spy_prices[-n:]
    # Convert to returns
    inst_ret = [(inst[i] - inst[i - 1]) / inst[i - 1] for i in range(1, n)]
    spy_ret = [(spy[i] - spy[i - 1]) / spy[i - 1] for i in range(1, n)]
    n_ret = len(inst_ret)
    if n_ret < min_obs:
        return None
    mean_inst = sum(inst_ret) / n_ret
    mean_spy = sum(spy_ret) / n_ret
    cov = sum((inst_ret[i] - mean_inst) * (spy_ret[i] - mean_spy) for i in range(n_ret)) / n_ret
    var_spy = sum((r - mean_spy) ** 2 for r in spy_ret) / n_ret
    if var_spy == 0:
        return None
    return cov / var_spy


def _compute_momentum(prices: list[float], start_offset: int, end_offset: int) -> float | None:
    """Return price[end_offset] / price[start_offset] - 1 using negative offsets."""
    n = len(prices)
    abs_start = n + start_offset  # start_offset is negative
    abs_end = n + end_offset
    if abs_start < 0 or abs_end < 0 or abs_start >= n or abs_end >= n:
        return None
    p_start = prices[abs_start]
    p_end = prices[abs_end]
    if p_start == 0:
        return None
    return p_end / p_start - 1.0


def compute_universe_metrics(
    session: Session,
    scenario: ScenarioContext | None = None,
    pit_date: date | None = None,
) -> dict[int, dict[str, float | None]]:
    """Compute all raw metrics for all instruments with any data.

    Returns {instrument_id: {metric_name: value_or_none}}.
    None means data is unavailable for that metric for that instrument.
    When scenario is provided, sleeve shocks are applied to market values and
    latest prices; price history stays on real data (Invariant H: no fabricated paths).
    When pit_date is set, all data queries are bounded to that date (backtest mode).
    """
    prices = _latest_prices(session, pit_date=pit_date)
    price_history = _price_history(session, days_back=400, pit_date=pit_date)
    dividends = _annual_dividends(session, pit_date=pit_date)
    dist_yields = _distribution_yields(session, pit_date=pit_date)

    eps = _latest_fundamental(session, "eps_diluted", pit_date=pit_date)
    equity = _latest_fundamental(session, "equity", pit_date=pit_date)
    total_debt = _latest_fundamental(session, "total_debt", pit_date=pit_date)
    expense_raw = _latest_fundamental(session, "expense_ratio", pit_date=pit_date)
    cfo = _latest_fundamental(session, "cfo", pit_date=pit_date)
    capex = _latest_fundamental(session, "capex", pit_date=pit_date)
    net_income = _latest_fundamental(session, "net_income", pit_date=pit_date)
    total_assets = _latest_fundamental(session, "total_assets", pit_date=pit_date)
    operating_income = _latest_fundamental(session, "operating_income", pit_date=pit_date)
    shares_outstanding = _latest_fundamental(session, "shares_outstanding", pit_date=pit_date)

    # Revenue + gross_profit: two most-recent annual periods per instrument for YoY
    def _fetch_two_period_fundamental(metric: str) -> dict[int, list[tuple[date, float]]]:
        where = [Fundamental.metric == metric]
        if pit_date is not None:
            where.append(or_(
                and_(Fundamental.filed_at.isnot(None), Fundamental.filed_at <= pit_date),
                and_(Fundamental.filed_at.is_(None), Fundamental.period_end <= pit_date),
            ))
        rows = session.execute(
            select(
                Fundamental.instrument_id,
                Fundamental.period_end,
                Fundamental.value,
            )
            .where(*where)
            .order_by(Fundamental.instrument_id, Fundamental.period_end.desc())
        ).all()
        result: dict[int, list[tuple[date, float]]] = {}
        for iid, pend, val in rows:
            result.setdefault(iid, []).append((pend, float(val)))
        return result

    revenue_by_inst = _fetch_two_period_fundamental("revenue")
    gross_profit_by_inst = _fetch_two_period_fundamental("gross_profit")

    # Find SPY instrument for beta computation (ticker = "SPY")
    spy_row = session.execute(
        select(Instrument.id).where(Instrument.ticker == "SPY")
    ).scalar_one_or_none()
    spy_prices_list = (
        [p for _, p in price_history[spy_row]] if spy_row and spy_row in price_history else []
    )

    # Holdings for portfolio-level metrics (include sleeve for scenario shock mapping)
    holdings_rows = session.execute(
        select(Holding.instrument_id, Holding.market_value, Instrument.instrument_type, Instrument.sleeve)
        .join(Instrument, Holding.instrument_id == Instrument.id)
        .where(Holding.market_value.isnot(None))
    ).all()

    # Build sleeve map and apply scenario shocks to MVs if provided
    holding_sleeves: dict[int, str] = {}
    raw_mvs: dict[int, float] = {}
    for h in holdings_rows:
        sleeve = h.sleeve or "other"
        holding_sleeves[h.instrument_id] = sleeve
        raw_mvs[h.instrument_id] = raw_mvs.get(h.instrument_id, 0.0) + float(h.market_value or 0)

    if scenario:
        shocked_mvs = {
            iid: mv * (1.0 + float(scenario.sleeve_shocks.get(holding_sleeves.get(iid, ""), 0)))
            for iid, mv in raw_mvs.items()
        }
        effective_prices = {
            iid: p * (1.0 + float(scenario.sleeve_shocks.get(holding_sleeves.get(iid, ""), 0)))
            for iid, p in prices.items()
        }
    else:
        shocked_mvs = raw_mvs
        effective_prices = prices

    total_mv = sum(shocked_mvs.values())
    holding_weights: dict[int, float] = {}
    holding_types: dict[int, str | None] = {}
    for h in holdings_rows:
        iid = h.instrument_id
        if total_mv > 0:
            holding_weights[iid] = shocked_mvs.get(iid, 0.0) / total_mv
        else:
            holding_weights[iid] = 0.0
        holding_types[iid] = h.instrument_type

    individual_stock_pct = sum(
        w for iid, w in holding_weights.items()
        if holding_types.get(iid) == "stock"
    )
    # holding_weights already computed; per-holding concentration contribution = weight**2

    # Collect all known instrument IDs (sorted for deterministic output order)
    all_ids: set[int] = set()
    all_ids.update(prices.keys(), eps.keys(), equity.keys(), expense_raw.keys())
    all_ids.update(total_debt.keys(), price_history.keys(), revenue_by_inst.keys())
    all_ids.update(total_assets.keys(), operating_income.keys(), net_income.keys())
    for h in holdings_rows:
        all_ids.add(h.instrument_id)

    effective_date = pit_date if pit_date is not None else date.today()
    one_year_ago = effective_date - timedelta(days=365)

    result: dict[int, dict[str, float | None]] = {}

    for iid in sorted(all_ids):
        m: dict[str, float | None] = {}

        price = effective_prices.get(iid)

        # P/E
        e = eps.get(iid)
        if price is not None and e is not None and e > 0:
            m["pe_ratio"] = price / e
        else:
            m["pe_ratio"] = None

        # D/E
        d = total_debt.get(iid)
        eq = equity.get(iid)
        if d is not None and eq is not None and eq > 0:
            m["debt_equity"] = d / eq
        else:
            m["debt_equity"] = None

        # Revenue YoY
        rev_periods = revenue_by_inst.get(iid, [])
        if len(rev_periods) >= 2:
            cur_rev = rev_periods[0][1]
            prior_rev = rev_periods[1][1]
            if prior_rev > 0:
                m["revenue_yoy"] = (cur_rev - prior_rev) / prior_rev
            else:
                m["revenue_yoy"] = None
        else:
            m["revenue_yoy"] = None

        # Dividend yield (trailing 12 months)
        divs = dividends.get(iid, [])
        trailing_divs = [v for d_, v in divs if d_ >= one_year_ago]
        annual_div = sum(trailing_divs)
        if price and price > 0 and annual_div > 0:
            m["dividend_yield"] = annual_div / price
        else:
            m["dividend_yield"] = None

        # Payout ratio: annual div per share / EPS
        if e is not None and e > 0 and annual_div > 0:
            m["payout_ratio"] = annual_div / e
        else:
            m["payout_ratio"] = None

        # Consecutive dividend-increase years (simplified: count years of div growth)
        if divs:
            yearly: dict[int, float] = {}
            for d_, v in divs:
                yearly[d_.year] = yearly.get(d_.year, 0.0) + v
            sorted_years = sorted(yearly.keys(), reverse=True)
            streak = 0
            for i in range(len(sorted_years) - 1):
                if yearly[sorted_years[i]] > yearly[sorted_years[i + 1]]:
                    streak += 1
                else:
                    break
            m["dividend_growth_years"] = float(streak) if streak > 0 else None
        else:
            m["dividend_growth_years"] = None

        # Expense ratio (fund-specific; will be marked n/a for stocks at scoring layer)
        er = expense_raw.get(iid)
        m["expense_ratio"] = er

        # Price-derived: need history
        hist = price_history.get(iid, [])
        hist_prices = [p for _, p in hist]

        # RSI(14)
        m["rsi14"] = _compute_rsi14(hist_prices) if hist_prices else None

        # 50/200 MA cross
        m["ma_cross"] = _compute_ma_cross(hist_prices) if hist_prices else None

        # 6-month momentum
        n = len(hist_prices)
        m["momentum_6m"] = (
            _compute_momentum(hist_prices, -127, -1) if n >= 127 else None
        )

        # 12-1 momentum
        m["momentum_12_1"] = (
            _compute_momentum(hist_prices, -253, -22) if n >= 253 else None
        )

        # Beta vs SPY
        m["beta_vs_spy"] = (
            _compute_beta(hist_prices, spy_prices_list) if spy_prices_list else None
        )

        # Portfolio-level metrics (same value for every holding)
        w = holding_weights.get(iid)
        m["concentration_hhi"] = w * w if w is not None else None  # weight^2 = HHI contribution
        m["individual_stock_pct"] = individual_stock_pct if iid in holding_weights else None

        # Fund distribution yield (pre-fetched in bulk — no per-instrument query)
        m["fund_distribution_yield"] = dist_yields.get(iid)

        # Gross margin YoY: gross_profit / revenue per period → YoY of that ratio
        gp_periods = gross_profit_by_inst.get(iid, [])
        rev_periods = revenue_by_inst.get(iid, [])
        if len(gp_periods) >= 2 and len(rev_periods) >= 2:
            rev_cur = rev_periods[0][1]
            rev_prior = rev_periods[1][1]
            gp_cur = gp_periods[0][1]
            gp_prior = gp_periods[1][1]
            if rev_cur > 0 and rev_prior > 0:
                gm_cur = gp_cur / rev_cur
                gm_prior = gp_prior / rev_prior
                m["gross_margin_yoy"] = gm_cur - gm_prior
            else:
                m["gross_margin_yoy"] = None
        else:
            m["gross_margin_yoy"] = None

        # P/B and FCF yield: need shares outstanding
        shares = shares_outstanding.get(iid)
        if price is not None and shares is not None and shares > 0:
            market_cap = price * shares
            eq = equity.get(iid)
            if eq is not None and eq > 0:
                m["pb_ratio"] = market_cap / eq
            else:
                m["pb_ratio"] = None
            cfo_val = cfo.get(iid)
            cap_val = capex.get(iid)
            if cfo_val is not None and cap_val is not None and market_cap > 0:
                m["fcf_yield"] = (cfo_val - cap_val) / market_cap
            else:
                m["fcf_yield"] = None
        else:
            m["pb_ratio"] = None
            m["fcf_yield"] = None

        m["implied_turnover"] = None

        # ── Quality factor metrics ────────────────────────────────────────────
        eq = equity.get(iid)
        debt = total_debt.get(iid)
        assets = total_assets.get(iid)
        op_inc = operating_income.get(iid)
        ni = net_income.get(iid)
        cfo_val = cfo.get(iid)

        # ROIC = operating_income / (equity + total_debt)
        if op_inc is not None and eq is not None and debt is not None:
            invested_capital = eq + debt
            m["roic"] = op_inc / invested_capital if invested_capital > 0 else None
        else:
            m["roic"] = None

        # Gross profitability (Novy-Marx) = gross_profit / total_assets
        gp_periods = gross_profit_by_inst.get(iid, [])
        gp = gp_periods[0][1] if gp_periods else None
        if gp is not None and assets is not None and assets > 0:
            m["gross_profitability"] = gp / assets
        else:
            m["gross_profitability"] = None

        # Accruals ratio = (net_income - cfo) / total_assets
        # Negative means cash-backed earnings (good); positive means accruals dominate (bad)
        if ni is not None and cfo_val is not None and assets is not None and assets > 0:
            m["accruals_ratio"] = (ni - cfo_val) / assets
        else:
            m["accruals_ratio"] = None

        result[iid] = m

    return result
