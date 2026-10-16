"""Plaid's categories folded into a few groups for the cash-flow chart.

Money in is split by source (paychecks, gambling winnings, the rest); money
out by group and item, below.

Plaid files spending under some 15 primary and 100-odd detailed categories,
too many to read on one chart. Each detailed category maps to a group (the
middle column) and an item within it (the right-hand column), named for
what the money is rather than how Plaid files it. Anything not listed falls
back on its primary category, and failing that lands in Misc.

Item names are unique across groups, since the chart keys nodes by name.
"""
from __future__ import annotations

DEBT = "Debt Payments"
UTILITIES = "Utilities"
FOOD = "Food"
TRANSPORT = "Transportation"
SHOPPING = "Shopping"
TRANSFERS = "Transfers & Cash"
MISC = "Misc."

# Biggest-first is decided per month; this is only the colour order, so a
# group keeps its colour from month to month.
GROUPS = [DEBT, UTILITIES, FOOD, SHOPPING, TRANSPORT, TRANSFERS, MISC]

_DETAILED: dict[str, tuple[str, str]] = {
    # Debt
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": (DEBT, "Credit Cards"),
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": (DEBT, "Mortgage"),
    "LOAN_PAYMENTS_CAR_PAYMENT": (DEBT, "Car Payment"),
    "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT": (DEBT, "Personal Loans"),
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": (DEBT, "Student Loans"),
    "LOAN_PAYMENTS_OTHER_PAYMENT": (DEBT, "Other Loans"),
    # Utilities. Gas and electricity is Xcel, which supplies the gas; the
    # city bills electric and water together.
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": (UTILITIES, "Gas"),
    "RENT_AND_UTILITIES_OTHER_UTILITIES": (UTILITIES, "Electric & Water"),
    "RENT_AND_UTILITIES_WATER": (UTILITIES, "Electric & Water"),
    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT": (UTILITIES, "Trash & Sewer"),
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": (UTILITIES, "Internet"),
    "RENT_AND_UTILITIES_TELEPHONE": (UTILITIES, "Phone"),
    "RENT_AND_UTILITIES_RENT": (UTILITIES, "Rent"),
    # Insurance (Allstate) is a monthly bill like the rest.
    "GENERAL_SERVICES_INSURANCE": (UTILITIES, "Insurance"),
    # Food
    "FOOD_AND_DRINK_GROCERIES": (FOOD, "Groceries"),
    "FOOD_AND_DRINK_RESTAURANT": (FOOD, "Restaurants"),
    "FOOD_AND_DRINK_FAST_FOOD": (FOOD, "Restaurants"),
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": (FOOD, "Alcohol"),
    "FOOD_AND_DRINK_COFFEE": (FOOD, "Coffee & Treats"),
    "FOOD_AND_DRINK_VENDING_MACHINES": (FOOD, "Coffee & Treats"),
    "FOOD_AND_DRINK_OTHER_FOOD_AND_DRINK": (FOOD, "Coffee & Treats"),
    # Transportation
    "TRANSPORTATION_GAS": (TRANSPORT, "Fuel"),
    "GENERAL_SERVICES_AUTOMOTIVE": (TRANSPORT, "Car Care"),
    "TRANSPORTATION_PARKING": (TRANSPORT, "Parking & Tolls"),
    "TRANSPORTATION_TOLLS": (TRANSPORT, "Parking & Tolls"),
    "TRANSPORTATION_PUBLIC_TRANSIT": (TRANSPORT, "Transit & Rides"),
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": (TRANSPORT, "Transit & Rides"),
    "TRANSPORTATION_BIKES_AND_SCOOTERS": (TRANSPORT, "Transit & Rides"),
    # Shopping
    "GENERAL_MERCHANDISE_SUPERSTORES": (SHOPPING, "Costco, Target & Walmart"),
    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": (SHOPPING, "Amazon & Online"),
    "GENERAL_MERCHANDISE_SPORTING_GOODS": (SHOPPING, "Sporting Goods"),
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": (SHOPPING, "Clothing"),
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": (SHOPPING, "Clothing"),
    "GENERAL_MERCHANDISE_PET_SUPPLIES": (SHOPPING, "Pets"),
    "HOME_IMPROVEMENT_HARDWARE": (SHOPPING, "Home & Garden"),
    "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE": (SHOPPING, "Home & Garden"),
    "HOME_IMPROVEMENT_FURNITURE": (SHOPPING, "Home & Garden"),
    # Money leaving for somewhere not linked
    "TRANSFER_OUT_TRANSFER_OUT_FROM_APPS": (TRANSFERS, "Venmo & Apps"),
    "TRANSFER_OUT_WITHDRAWAL": (TRANSFERS, "Cash (ATM)"),
    "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS": (TRANSFERS, "Investing"),
    "TRANSFER_OUT_SAVINGS": (TRANSFERS, "Transfers out"),
    "TRANSFER_OUT_ACCOUNT_TRANSFER": (TRANSFERS, "Transfers out"),
    # Card interest is the cost of debt.
    "BANK_FEES_INTEREST_CHARGE": (DEBT, "Interest & Fees"),
}

_PRIMARY: dict[str, tuple[str, str]] = {
    "LOAN_PAYMENTS": (DEBT, "Other Loans"),
    # Card annual and balance-transfer fees and interest: the cost of debt.
    "BANK_FEES": (DEBT, "Interest & Fees"),
    "RENT_AND_UTILITIES": (UTILITIES, "Other Utilities"),
    "FOOD_AND_DRINK": (FOOD, "Coffee & Treats"),
    "TRANSPORTATION": (TRANSPORT, "Transit & Rides"),
    "GENERAL_MERCHANDISE": (SHOPPING, "Other Shopping"),
    "HOME_IMPROVEMENT": (SHOPPING, "Home & Garden"),
    "TRANSFER_OUT": (TRANSFERS, "Transfers out"),
    "ENTERTAINMENT": (MISC, "Entertainment"),
    "GENERAL_SERVICES": (MISC, "Services"),
    "MEDICAL": (MISC, "Medical"),
    "PERSONAL_CARE": (MISC, "Personal Care"),
    "TRAVEL": (MISC, "Travel"),
    "GOVERNMENT_AND_NON_PROFIT": (MISC, "Government & Giving"),
}

# What a group's smaller items are rolled into, past the chart's limit.
OTHER = {
    DEBT: "Other Debt",
    UTILITIES: "Other Utilities",
    FOOD: "Other Food",
    TRANSPORT: "Other Transportation",
    SHOPPING: "Other Shopping",
    TRANSFERS: "Other Transfers",
    MISC: "Other",
}


PAYCHECKS = "Paychecks"
WINNINGS = "Gambling Winnings"
OTHER_INCOME = "Other Income"
# Order of the income sources, top to bottom: the colour order too.
INCOME_SOURCES = [PAYCHECKS, WINNINGS, OTHER_INCOME]
_WAGES = {"INCOME_SALARY", "INCOME_WAGES"}


def income_source_of(primary: str, detailed: str, name: str | None) -> str:
    """Where money in came from, for the chart's left-hand column.

    Payroll is Plaid's salary or wages category, or a deposit whose
    description says payroll. Gambling winnings are filed by a rule (FanDuel
    cash-outs). Everything else — interest, refunds, Venmo — is other income.
    """
    if detailed in _WAGES or "payroll" in (name or "").lower():
        return PAYCHECKS
    if primary == "GAMBLING_WINNINGS":
        return WINNINGS
    return OTHER_INCOME


def group_of(primary: str, detailed: str) -> tuple[str, str]:
    """(group, item) for a transaction's effective category.

    A custom category from an override keeps its own name as the item,
    under Misc.
    """
    if detailed in _DETAILED:
        return _DETAILED[detailed]
    if primary in _PRIMARY:
        return _PRIMARY[primary]
    return MISC, primary.replace("_", " ").title() if primary.isupper() else primary
