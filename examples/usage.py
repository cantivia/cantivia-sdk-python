"""Show this API key's usage and the account's plan allowances.

CANTIVIA_API_KEY=... python examples/usage.py
"""

from __future__ import annotations

from cantivia import CantiviaClient


def main() -> None:
    with CantiviaClient() as client:
        usage = client.usage()

    print(f"Requests: {usage.requests.today} today, {usage.requests.month} this month")
    print(f"Rate limit: {usage.rate_limit.remaining} of {usage.rate_limit.limit} left this window")
    for product in usage.products:
        print(f"{product.product}: {product.status}")
        for unit in product.units:
            allowance = "no allowance" if unit.allowance is None else f"of {unit.allowance}"
            print(f"  {unit.unit}: {unit.used} used {allowance}, {unit.overage} over")


if __name__ == "__main__":
    main()
