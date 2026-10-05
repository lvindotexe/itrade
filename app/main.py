#!/usr/bin/env python3
"""iTrade: an educational stock screener using the supplied stocks.csv.

Run: python3 app/main.py
The app asks for all preferences interactively. There are no command-line options.
Requires Python 3.10+ and only the standard library.
"""
from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from math import ceil
from pathlib import Path
import re
import sys


DISCLAIMER = (
    "Educational screening only; not financial advice. Historical returns do not "
    "predict future returns. Rankings do not measure risk or diversification."
)


@dataclass(frozen=True)
class Stock:
    id: str
    performance: Decimal
    industry: str
    year: int
    environment: Decimal
    social: Decimal
    governance: Decimal


@dataclass(frozen=True)
class Preferences:
    n: int
    industry_best: bool = False
    founded_by: int | None = None
    esg: bool = False

    def __post_init__(self):
        if type(self.n) is not int or not 1 <= self.n <= 100:
            raise ValueError("Number of stocks must be an integer from 1 to 100.")

        # None means that the user has not enabled the year filter.
        if self.founded_by is not None:
            if type(self.founded_by) is not int or not 1800 <= self.founded_by <= 2020:
                raise ValueError("Establishment cutoff must be an integer from 1800 to 2020.")


@dataclass(frozen=True)
class Recommendation:
    stocks: list[Stock]
    eligible_count: int


def parse_csv(text: str) -> list[Stock]:
    """Validate the whole dataset before recommending; never silently drop rows."""
    fields = {
        "id": {"id", "stockid", "companyid", "ticker", "symbol"},
        "performance": {"performance", "return", "returns", "performancepercent"},
        "industry": {"industry", "sector"},
        "year": {"foundationyear", "establishmentyear", "founded", "year", "yearfounded"},
        "environment": {"environmentalrating", "environmentrating", "environment", "environmental", "e"},
        "social": {"socialrating", "social", "s"},
        "governance": {"governancerating", "governance", "g"},
    }

    def number(value: str, label: str, percent: bool = False) -> Decimal:
        text = value.strip()
        if percent and text.endswith("%"):
            text = text[:-1].strip()
        try:
            result = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{label} must be a number, got {value!r}.") from None
        if not result.is_finite():
            raise ValueError(f"{label} must be finite.")
        return result

    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect, strict=True)
    if not reader.fieldnames:
        raise ValueError("CSV is empty or has no header.")
    columns = {}
    seen_headers = set()
    for header in reader.fieldnames:
        key = re.sub(r"[^a-z0-9]", "", header.lower().lstrip("\ufeff"))
        if key in seen_headers:
            raise ValueError(f"Duplicate CSV header: {header!r}.")
        seen_headers.add(key)
        for field, aliases in fields.items():
            if key in aliases:
                if field in columns:
                    raise ValueError(f"More than one column maps to {field}.")
                columns[field] = header
    for field in fields:
        if field not in columns:
            raise ValueError(f"Missing CSV column: {field}.")

    stocks = []
    ids = set()
    for row in reader:
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Row has the wrong number of columns.")
            data = {key: row[column].strip() for key, column in columns.items()}
            ident = data["id"].upper()
            if not re.fullmatch(r"[A-Z]{3}", ident):
                raise ValueError("ID must contain exactly three ASCII letters.")
            if ident in ids:
                raise ValueError(f"Duplicate stock ID: {ident}.")
            if not data["industry"] or not data["industry"].isprintable():
                raise ValueError("Industry must be nonempty, printable text.")
            # The user cutoff is limited to 1800–2020, not the dataset's years.
            if not re.fullmatch(r"[0-9]{1,4}", data["year"]):
                raise ValueError("Foundation year must be an integer from 1 to 9999.")
            year = int(data["year"])
            if year < 1:
                raise ValueError("Foundation year must be from 1 to 9999.")
            environment = number(data["environment"], "Environment")
            social = number(data["social"], "Social")
            governance = number(data["governance"], "Governance")
            for rating in (environment, social, governance):
                if not 1 <= rating <= 10:
                    raise ValueError("Every ESG rating must be between 1 and 10.")

            stock = Stock(
                id=ident,
                performance=number(data["performance"], "Performance", percent=True),
                industry=" ".join(data["industry"].split()),
                year=year,
                environment=environment,
                social=social,
                governance=governance,
            )
            stocks.append(stock)
            ids.add(ident)
        except ValueError as exc:
            raise ValueError(f"CSV line {reader.line_num}: {exc}") from None
    if not stocks:
        raise ValueError("CSV contains no stocks.")
    return stocks


StockFilter = Callable[[Stock], bool]


def ranking_order(stock: Stock) -> tuple[Decimal, str]:
    """Rank by descending performance, then ascending ID for ties."""
    return (-stock.performance, stock.id)


def get_industry_leaders(stocks: list[Stock]) -> set[str]:
    """Find the top quarter (including cutoff ties) in each original industry."""
    groups: dict[str, list[Stock]] = {}
    for stock in stocks:
        groups.setdefault(stock.industry.casefold(), []).append(stock)

    leaders: set[str] = set()
    for group in groups.values():
        ranked = sorted(group, key=ranking_order)
        places = ceil(len(group) / 4)
        cutoff = ranked[places - 1].performance
        leaders.update(stock.id for stock in group if stock.performance >= cutoff)
    return leaders


def build_filters(stocks: list[Stock], preferences: Preferences) -> list[StockFilter]:
    """Turn enabled preferences into predicates using the untouched dataset."""
    filters: list[StockFilter] = []
    if preferences.industry_best:
        # Calculate leaders before applying any year or ESG criteria.
        leaders = get_industry_leaders(stocks)

        def industry_filter(stock: Stock) -> bool:
            return stock.id in leaders

        filters.append(industry_filter)

    if preferences.founded_by is not None:
        cutoff_year = preferences.founded_by

        def year_filter(stock: Stock) -> bool:
            return stock.year <= cutoff_year

        filters.append(year_filter)

    if preferences.esg:
        def esg_filter(stock: Stock) -> bool:
            return (
                stock.environment >= Decimal("7")
                and stock.social >= Decimal("7")
                and stock.governance >= Decimal("7")
            )

        filters.append(esg_filter)

    return filters


def apply_filters(stocks: list[Stock], filters: list[StockFilter]) -> list[Stock]:
    """Keep stocks passing every predicate; an empty filter list keeps all."""
    return [stock for stock in stocks if all(filter_fn(stock) for filter_fn in filters)]


def recommend(stocks: list[Stock], preferences: Preferences) -> Recommendation:
    """Apply all enabled criteria, rank matches, and select up to n stocks."""
    filters = build_filters(stocks, preferences)
    eligible = apply_filters(stocks, filters)
    ranked = sorted(eligible, key=ranking_order)
    return Recommendation(stocks=ranked[:preferences.n], eligible_count=len(eligible))


def format_results(result: Recommendation, prefs: Preferences, source: str, stock_count: int) -> str:
    """Format an existing result; do not run the recommendation again."""
    selected = result.stocks
    total = result.eligible_count
    active = []
    if prefs.industry_best:
        active.append("industry top 25% (rounded up, boundary ties included)")
    if prefs.founded_by is not None:
        active.append(f"founded <= {prefs.founded_by}")
    if prefs.esg:
        active.append("E, S and G each >= 7")
    lines = ["iTrade | STOCK SCREEN", "=" * 76, f"Data: {source}",
             f"Loaded: {stock_count} stocks", "Filters: " + ("; ".join(active) or "none"),
             f"Requested: {prefs.n} | Eligible: {total} | Showing: {len(selected)}"]
    if stock_count != 500:
        lines.append("NOTE: Assignment expects 500 stocks; using all supplied records.")
    if total < prefs.n:
        lines.append(f"SHORTFALL: only {total} match. No filters were relaxed.")
    if not selected:
        lines.append("No matches. Change your preferences explicitly and run again.")
    else:
        width = 8
        for stock in selected:
            width = max(width, len(stock.industry))
        width = min(width, 22)
        lines += ["", f"Rank  ID    Return  {'Industry':<{width}}  Founded   E   S   G",
                  "-" * (50 + width)]
        for rank, stock in enumerate(selected, 1):
            industry = stock.industry
            if len(industry) > width:
                industry = industry[:width - 3] + "..."
            lines.append(f"{rank:>4}  {stock.id}  {stock.performance:>7.2f}%  "
                         f"{industry:<{width}}  {stock.year:>7}  "
                         f"{stock.environment:>2g}  {stock.social:>2g}  {stock.governance:>2g}")
        for stock in selected:
            if stock.performance < 0:
                lines.append("CAUTION: Some selected stocks have negative historical returns.")
                break
    return "\n".join(lines + ["", DISCLAIMER])


def get_preferences() -> Preferences:
    def ask_int(prompt: str, low: int, high: int) -> int:
        while True:
            try:
                value = int(input(prompt).strip())
                if low <= value <= high:
                    return value
            except ValueError:
                pass
            print(f"Enter a whole number from {low} to {high}.")

    def ask_yes(prompt: str) -> bool:
        while True:
            answer = input(prompt + " [y/n]: ").strip().lower()
            if answer in {"y", "yes", "n", "no"}:
                return answer in {"y", "yes"}
            print("Please enter y or n.")

    print("\niTrade preferences (all enabled filters must be satisfied)")
    n = ask_int("How many stocks (1–100)? ", 1, 100)
    industry = ask_yes("Only the top 25% in each industry (including ties)?")
    year = None
    if ask_yes("Filter by establishment year?"):
        year = ask_int("Founded by year (1800–2020)? ", 1800, 2020)
    esg = ask_yes("Require each ESG score to be at least 7/10?")
    return Preferences(n=n, industry_best=industry, founded_by=year, esg=esg)


def main() -> int:
    path = Path(__file__).resolve().with_name("stocks.csv")

    try:
        # The application pipeline: load, choose preferences, recommend, print.
        stocks = parse_csv(path.read_text(encoding="utf-8-sig"))
        preferences = get_preferences()
        result = recommend(stocks, preferences)
        print(format_results(result, preferences, path.name, len(stocks)))
        return 0
    except FileNotFoundError:
        print("Error: stocks.csv must be in the same folder as main.py.", file=sys.stderr)
    except (ValueError, OSError, UnicodeError, csv.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No recommendation was completed.", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
