"""The contract for each raw source: where its files live, the exact header
they must have, and how to tell which business date a file covers.

The loader deliberately does not import the generator. A real loader only
knows what the sources promise, and a header that drifts from this contract
is rejected rather than guessed at.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Source:
    table: str
    pattern: str  # glob relative to the raw directory
    columns: tuple[str, ...]
    dated_by: str  # "filename" | "rows" | "none"

    def file_date(self, path: Path, text: str) -> date | None:
        if self.dated_by == "filename":
            match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
            return date.fromisoformat(match.group(1)) if match else None
        if self.dated_by == "rows":
            # Processor B names files by batch number; the batch's CET day is in its rows.
            # Prefer the closing payout row, else the first row with a date.
            reader = csv.DictReader(io.StringIO(text))
            first = None
            for row in reader:
                created = (row.get("Creation Date") or "")[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
                    continue
                if row.get("Type") == "MerchantPayout":
                    return date.fromisoformat(created)
                first = first or created
            return date.fromisoformat(first) if first else None
        return None


_APP_DB = {
    "plans": ("plan_id", "name", "fee_bps"),
    "plan_fixed_fees": ("plan_id", "currency", "fixed_fee_minor"),
    "sellers": ("seller_id", "name", "country", "plan_id", "status", "created_at", "churned_at"),
    "seller_plan_changes": ("seller_id", "plan_id", "effective_from"),
    "products": ("product_id", "seller_id", "name", "product_type", "price_usd_minor",
                 "created_at"),
    "orders": ("order_id", "seller_id", "product_id", "buyer_id", "buyer_country", "currency",
               "amount_minor", "platform_fee_minor", "plan_id", "processor",
               "processor_charge_ref", "order_type", "status", "created_at"),
    "refunds": ("refund_id", "order_id", "amount_minor", "currency", "processor_refund_ref",
                "created_at"),
    "seller_payouts": ("payout_id", "seller_id", "amount_minor", "currency", "status",
                       "initiated_at", "settled_at", "returned_at"),
}

SOURCES: tuple[Source, ...] = (
    Source(
        "processor_a_balance_transactions", "processor_a/balance_transactions_*.csv",
        ("balance_transaction_id", "created_utc", "available_on_utc", "currency", "gross", "fee",
         "net", "reporting_category", "source_id", "description", "customer_facing_amount",
         "customer_facing_currency", "automatic_payout_id", "payment_metadata[kiln_order_id]"),
        "filename",
    ),
    Source(
        "processor_a_payouts", "processor_a/payouts_*.csv",
        ("payout_id", "created_utc", "arrival_date", "amount", "currency", "status",
         "failure_code", "status_changed_utc"),
        "filename",
    ),
    Source(
        "processor_b_settlement_details", "processor_b/settlement_detail_report_batch_*.csv",
        ("Company Account", "Merchant Account", "Psp Reference", "Merchant Reference",
         "Payment Method", "Creation Date", "TimeZone", "Type", "Modification Reference",
         "Gross Currency", "Gross Debit (GC)", "Gross Credit (GC)", "Exchange Rate",
         "Net Currency", "Net Debit (NC)", "Net Credit (NC)", "Commission (NC)", "Markup (NC)",
         "Scheme Fees (NC)", "Interchange (NC)", "Batch Number"),
        "rows",
    ),
    Source(
        "bank_statements", "bank/bank_statement_*.csv",
        ("bank_transaction_id", "account_number", "booking_date", "value_date", "amount",
         "currency", "counterparty_name", "end_to_end_reference", "description"),
        "filename",
    ),
    Source(
        "fx_reference_rates", "fx/reference_rates_*.csv",
        ("rate_date", "base_currency", "quote_currency", "rate"),
        "filename",
    ),
    *(Source(f"app_db_{name}", f"app_db/{name}.csv", cols, "none")
      for name, cols in _APP_DB.items()),
)


def sanitize(column: str) -> str:
    """'Gross Debit (GC)' -> 'gross_debit_gc'. BigQuery-safe, stable, lossless enough."""
    return re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
