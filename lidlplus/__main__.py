#!/usr/bin/env python3
"""
lidl plus command line tool
"""

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path

import requests

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
# pylint: disable=wrong-import-position
from lidlplus import LidlPlusApi, analytics, export
from lidlplus.exceptions import WebBrowserException, LoginError, LegalTermsException, MissingLogin

EXPORT_DATASETS = export.DATASETS
# The store search sorts by distance and needs a position, the middle of the country is used without one
COUNTRY_POSITIONS = {
    "AT": (47.52, 14.55),
    "BE": (50.50, 4.47),
    "CZ": (49.82, 15.47),
    "DE": (51.16, 10.45),
    "ES": (40.46, -3.75),
    "FR": (46.23, 2.21),
    "IT": (41.87, 12.57),
    "NL": (52.13, 5.29),
    "PL": (51.92, 19.15),
}


def get_arguments():
    """Get parsed arguments."""
    parser = argparse.ArgumentParser(
        prog="lidl-plus",
        description="Lidl Plus API",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=28),
    )
    parser.add_argument("-c", "--country", metavar="CC", help="country (DE, BE, NL, AT, ...)")
    parser.add_argument("-l", "--language", metavar="LANG", help="language (de, en, fr, it, ...)")
    parser.add_argument("-u", "--user", help="Lidl Plus login username")
    parser.add_argument("-p", "--password", metavar="XXX", help="Lidl Plus login password")
    parser.add_argument(
        "--2fa",
        choices=["phone", "email"],
        default="phone",
        help="choose two factor auth method",
    )
    parser.add_argument("-r", "--refresh-token", metavar="TOKEN", help="refresh token to authenticate")
    parser.add_argument("--skip-verify", help="skip ssl verification", action="store_true")
    parser.add_argument(
        "--not-accept-legal-terms",
        help="not auto accept legal terms updates",
        action="store_true",
    )
    parser.add_argument("-d", "--debug", help="debug mode (shows browser window)", action="store_true")
    parser.add_argument("--cache", metavar="FILE", help="path to cache file (e.g. lidlplus_cache.json)")
    subparser = parser.add_subparsers(title="commands", metavar="command", required=True)
    auth = subparser.add_parser("auth", help="authenticate and get token")
    auth.set_defaults(func=print_refresh_token)
    loyalty_id = subparser.add_parser("id", help="show loyalty ID")
    loyalty_id.set_defaults(func=print_loyalty_id)
    receipt = subparser.add_parser("receipt", help="output last receipts as json")
    receipt.set_defaults(func=print_tickets)
    receipt.add_argument("-a", "--all", help="fetch all receipts", action="store_true")
    coupon = subparser.add_parser("coupon", help="activate coupons")
    coupon.set_defaults(func=activate_coupons)
    coupon.add_argument("-a", "--all", help="activate all coupons", action="store_true")
    sync = subparser.add_parser("sync", help="sync new tickets to cache")
    sync.set_defaults(func=sync_cache)
    sync.add_argument("--store", metavar="KEY", action="append", help="also save the offers of a store (repeatable)")
    sync.add_argument("--leaflets", help="also save the leaflets", action="store_true")
    stats = subparser.add_parser("stats", help="show analytics from cache")
    stats.set_defaults(func=print_stats)
    stores = subparser.add_parser("stores", help="search stores by city, postal code or street")
    stores.set_defaults(func=print_stores)
    stores.add_argument("query", help="city, postal code or street")
    stores.add_argument("--position", metavar="LAT,LON", help="sort by distance to this position")
    offers = subparser.add_parser("offers", help="show current and announced offers of a store as json")
    offers.set_defaults(func=print_offers)
    offers.add_argument("store", metavar="KEY", help="store key like DE1234 (see command stores)")
    leaflets = subparser.add_parser("leaflets", help="show the current and upcoming leaflets as json")
    leaflets.set_defaults(func=print_leaflets)
    leaflets.add_argument("--search", metavar="TEXT", help="search the pages and products of these leaflets")
    export_parser = subparser.add_parser("export", help="export the cache as CSV, JSON or ZIP")
    export_parser.set_defaults(func=export_cache)
    export_parser.add_argument(
        "output", metavar="FILE", help="output file, the extension selects the format (.csv .json .zip)"
    )
    export_parser.add_argument(
        "--dataset",
        choices=EXPORT_DATASETS,
        help="table for CSV and JSON, default: the file name if it is a table, else items",
    )
    return vars(parser.parse_args())


def check_auth():
    """check auth package is installed"""
    try:
        # pylint: disable=import-outside-toplevel, unused-import
        import oic
        import seleniumwire
        import getuseragent
        import webdriver_manager
    except ImportError:
        print(
            "To login and receive a refresh token you need to install all auth requirements:\n"
            '  pip install "lidl-plus[auth]"\n'
            "You also need google chrome to be installed."
        )
        sys.exit(1)


def lidl_plus_login(args):
    """handle authentication"""
    if not args.get("refresh_token"):
        check_auth()
    if args.get("skip_verify"):
        os.environ["WDM_SSL_VERIFY"] = "0"
        os.environ["CURL_CA_BUNDLE"] = ""
    language = args.get("language") or input("Enter your language (de, en, ...): ")
    country = args.get("country") or input("Enter your country (DE, AT, ...): ")
    if args.get("refresh_token"):
        args["api"] = LidlPlusApi(language, country, args.get("refresh_token"), cache_file=args.get("cache"))
        return args["api"]
    username = args.get("user") or input("Enter your lidl plus username (phone number): ")
    password = args.get("password") or getpass("Enter your lidl plus password: ")
    lidl_plus = LidlPlusApi(language, country, cache_file=args.get("cache"))
    try:
        text = f"Enter the verify code you received via {args['2fa']}: "
        lidl_plus.login(
            username,
            password,
            verify_token_func=lambda: input(text),
            verify_mode=args["2fa"],
            headless=not args.get("debug"),
            accept_legal_terms=not args.get("not_accept_legal_terms"),
        )
    except WebBrowserException:
        print("Can't connect to web browser. Please install Chrome, Chromium or Firefox")
        sys.exit(101)
    except LoginError as error:
        print(f"Login failed - {error}")
        sys.exit(102)
    except LegalTermsException as error:
        print(f"Legal terms not accepted - {error}")
        sys.exit(103)
    return lidl_plus


def require_cache(args, command):
    """exit if no cache file is given"""
    if not args.get("cache"):
        print(f"Error: --cache FILE is required for {command}")
        sys.exit(1)


def print_refresh_token(args):
    """pretty print refresh token"""
    lidl_plus = lidl_plus_login(args)
    length = len(token := lidl_plus.refresh_token) - len("refresh token")
    print(f"{'-' * (length // 2)} refresh token {'-' * (length // 2 - 1)}\n" f"{token}\n" f"{'-' * len(token)}")


def print_loyalty_id(args):
    """print loyalty ID"""
    lidl_plus = lidl_plus_login(args)
    print(lidl_plus.loyalty_id())


def print_tickets(args):
    """pretty print as json"""
    lidl_plus = lidl_plus_login(args)
    ticket_list = lidl_plus.tickets()
    if not ticket_list:
        print("No receipts found.")
        return
    if args.get("all"):
        tickets = [lidl_plus.ticket(ticket["id"]) for ticket in ticket_list]
    else:
        tickets = lidl_plus.ticket(ticket_list[0]["id"])
    print(json.dumps(tickets, indent=4))


def activate_coupons(args):
    """List coupons or activate all available coupons"""
    lidl_plus = lidl_plus_login(args)
    if not args.get("all"):
        print(json.dumps(lidl_plus.coupons(), indent=4))
        return
    result = lidl_plus.activate_all_coupons()
    for title in result["activated"]:
        print("activated coupon:", title)
    for title in result["failed"]:
        print("could not activate coupon:", title)
    print(f"Activated {len(result['activated'])} coupons")


def sync_cache(args):
    """Sync new tickets to cache"""
    require_cache(args, "sync")
    lidl_plus = lidl_plus_login(args)
    print("Syncing tickets...")
    new_count = lidl_plus.sync()
    print(f"Done. {new_count} new ticket(s) added to cache.")
    if args.get("store"):
        print(f"{lidl_plus.sync_offers(args['store'])} new offer(s) added to cache.")
    if args.get("leaflets"):
        print(f"{lidl_plus.sync_leaflets()} new leaflet(s) added to cache.")


def public_api(args):
    """API client for data that needs no login"""
    return LidlPlusApi(args.get("language") or "de", args.get("country") or "DE", cache_file=args.get("cache"))


def print_stores(args):
    """print stores matching the query"""
    lidl_plus = public_api(args)
    if args.get("position"):
        latitude, longitude = (float(value) for value in args["position"].split(","))
    else:
        latitude, longitude = COUNTRY_POSITIONS.get((args.get("country") or "DE").upper(), (50.0, 10.0))
    for store in lidl_plus.search_stores(args["query"], latitude, longitude):
        address = ", ".join(filter(None, [store.get("address"), store.get("postalCode"), store.get("locality")]))
        print(f"{store.get('storeKey')}\t{store.get('name')}\t{address}")


def print_offers(args):
    """print offers of a store as json"""
    offers = [analytics.normalize_offer(offer) for offer in public_api(args).store_offers(args["store"])]
    for offer in offers:
        offer["status"] = analytics.offer_status(offer)
    print(json.dumps(offers, indent=4, ensure_ascii=False))


def print_leaflets(args):
    """print the current and upcoming leaflets, or the pages and products that match the search"""
    lidl_plus = public_api(args)
    leaflets = analytics.leaflet_overview(lidl_plus.leaflets())
    for leaflet in leaflets:
        leaflet["status"] = analytics.leaflet_status(leaflet)
    leaflets = [leaflet for leaflet in leaflets if leaflet["status"] != "expired"]
    if not args.get("search"):
        print(json.dumps(leaflets, indent=4, ensure_ascii=False))
        return
    for leaflet in leaflets:
        if leaflet["identifier"]:
            leaflet.update(analytics.leaflet_details(lidl_plus.leaflet(leaflet["identifier"])))
    print(json.dumps(analytics.search_leaflets(leaflets, args["search"]), indent=4, ensure_ascii=False))


def export_cache(args):
    """Export the cache as CSV, JSON or ZIP"""
    require_cache(args, "export")
    output = Path(args["output"])
    if output.resolve() == Path(args["cache"]).resolve():
        print("Error: the export would replace the cache file, choose another file name")
        sys.exit(1)
    file_format = output.suffix.lower().lstrip(".")
    if file_format not in export.FORMATS:
        file_format = "csv"
    # "receipts.csv" exports the receipts
    dataset = args.get("dataset") or (output.stem.lower() if output.stem.lower() in EXPORT_DATASETS else "items")
    output.write_bytes(export.export_file(public_api(args).cached_data(), dataset, file_format))
    print(f"Exported to {output}")


def print_stats(args):
    """Print analytics from cache"""
    require_cache(args, "stats")
    # Only reads the local cache, no login necessary
    lidl_plus = LidlPlusApi(args.get("language") or "de", args.get("country") or "DE", cache_file=args["cache"])
    items = lidl_plus.all_ticket_items()
    stats = {
        "current_month_spending": lidl_plus.current_month_spending(),
        "average_basket": lidl_plus.average_basket(),
        "shopping_frequency_days": lidl_plus.shopping_frequency_days(),
        "spending_by_month": lidl_plus.spending_by_month(),
        "spending_by_store": lidl_plus.spending_by_store(),
        "category_spending": analytics.category_spending(items),
        "savings_total": analytics.total_savings(items),
        "savings_by_month": analytics.savings_by_month(items),
        "stores": analytics.visited_stores(lidl_plus.cached_tickets()),
        "frequently_bought_top10": lidl_plus.frequently_bought(10),
        "restock_suggestions": lidl_plus.restock_suggestions(),
    }
    print(json.dumps(stats, indent=4, ensure_ascii=False))


def print_replaced_token(args):
    """show the new refresh token if the auth server replaced the given one"""
    api = args.get("api")
    if api and api.refresh_token != args.get("refresh_token"):
        print(
            f"Lidl Plus replaced the refresh token, the old one may not work anymore. New token:\n{api.refresh_token}",
            file=sys.stderr,
        )


def main():
    """argument commands"""
    args = get_arguments()
    try:
        args["func"](args)
    finally:
        print_replaced_token(args)


def start():
    """wrapper for cmd tool"""
    try:
        main()
    except KeyboardInterrupt:
        print("Aborted.")
    except (LoginError, MissingLogin) as error:
        print(f"Authentication failed - {error}")
        sys.exit(102)
    except requests.RequestException as error:
        print(f"Request to Lidl Plus failed - {error}")
        sys.exit(104)


if __name__ == "__main__":
    start()
