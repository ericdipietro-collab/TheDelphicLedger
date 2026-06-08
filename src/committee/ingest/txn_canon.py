"""Canonicalize raw transaction type strings."""

from __future__ import annotations

TYPE_SYNONYMS: dict[str, str] = {
    # buy
    "buy": "buy",
    "purchase": "buy",
    "bought": "buy",
    "you bought": "buy",
    "bought to open": "buy",
    # sell
    "sell": "sell",
    "sale": "sell",
    "sold": "sell",
    "you sold": "sell",
    "sold to close": "sell",
    # dividend
    "dividend": "dividend",
    "cash dividend": "dividend",
    "dividend received": "dividend",
    "qualified dividend": "dividend",
    "ordinary dividend": "dividend",
    "special dividend": "dividend",
    # interest
    "interest": "interest",
    "interest earned": "interest",
    "interest income": "interest",
    "margin interest": "interest",
    "credit interest": "interest",
    # fee / commission
    "fee": "fee",
    "commission": "fee",
    "fees & commissions": "fee",
    "advisory fee": "fee",
    "service fee": "fee",
    # transfer in
    "transfer in": "transfer_in",
    "acats in": "transfer_in",
    "incoming transfer": "transfer_in",
    "journal": "transfer_in",
    # transfer out
    "transfer out": "transfer_out",
    "acats out": "transfer_out",
    "outgoing transfer": "transfer_out",
    # split
    "split": "split",
    "stock split": "split",
    "reverse split": "split",
    "forward split": "split",
    # reinvest
    "reinvest shares": "reinvest",
    "reinvest dividend": "reinvest",
    "reinvestment": "reinvest",
    "drip": "reinvest",
    "dividend reinvestment": "reinvest",
    "automatic reinvestment": "reinvest",
    # sweep / money market
    "sweep": "sweep",
    "money market purchase": "sweep",
    "money market redemption": "sweep",
    # tax withholding
    "tax withholding": "tax_withholding",
    "federal tax withheld": "tax_withholding",
    "nra tax withheld": "tax_withholding",
    # return of capital
    "return of capital": "return_of_capital",
    # corporate action
    "merger": "corporate_action",
    "acquisition": "corporate_action",
    "spin-off": "corporate_action",
    "spinoff": "corporate_action",
    "tender offer": "corporate_action",
    "rights": "corporate_action",
    # cash
    "cash": "cash_deposit",
    "deposit": "cash_deposit",
    "withdrawal": "cash_withdrawal",
    "wire": "cash_deposit",
}


def canonicalize_type(raw_type: str, extra_aliases: dict[str, str] | None = None) -> str | None:
    """Return canonical type string, or None if unknown (goes to queue).

    extra_aliases: template-level overrides applied before built-in dict.
    """
    normalized = raw_type.strip().lower()
    if extra_aliases and normalized in {k.lower() for k in extra_aliases}:
        for k, v in extra_aliases.items():
            if k.lower() == normalized:
                return v
    return TYPE_SYNONYMS.get(normalized)
