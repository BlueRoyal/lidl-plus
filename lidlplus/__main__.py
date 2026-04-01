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
from datetime import datetime, timezone

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
# pylint: disable=wrong-import-position
from lidlplus import LidlPlusApi
from lidlplus.exceptions import WebBrowserException, LoginError, LegalTermsException


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
    parser.add_argument("-d", "--debug", help="debug mode", action="store_true")
    parser.add_argument("--cache", metavar="FILE", help="path to cache file (e.g. lidlplus_cache.json)")
    subparser = parser.add_subparsers(title="commands", metavar="command", required=True)
    auth = subparser.add_parser("auth", help="authenticate and get token")
    auth.set_defaults(auth=True)
    loyalty_id = subparser.add_parser("id", help="show loyalty ID")
    loyalty_id.set_defaults(id=True)
    receipt = subparser.add_parser("receipt", help="output last receipts as json")
    receipt.set_defaults(receipt=True)
    receipt.add_argument("-a", "--all", help="fetch all receipts", action="store_true")
    coupon = subparser.add_parser("coupon", help="activate coupons")
    coupon.set_defaults(coupon=True)
    coupon.add_argument("-a", "--all", help="activate all coupons", action="store_true")
    sync = subparser.add_parser("sync", help="sync new tickets to cache")
    sync.set_defaults(sync=True)
    stats = subparser.add_parser("stats", help="show analytics from cache")
    stats.set_defaults(stats=True)
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
        return LidlPlusApi(language, country, args.get("refresh_token"), cache_file=args.get("cache"))
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
    if args.get("all"):
        tickets = [lidl_plus.ticket(ticket["id"]) for ticket in lidl_plus.tickets()]
    else:
        tickets = lidl_plus.ticket(lidl_plus.tickets()[0]["id"])
    print(json.dumps(tickets, indent=4))


def activate_coupons(args):
    """Activate all available coupons"""
    lidl_plus = lidl_plus_login(args)
    coupons = lidl_plus.coupons()
    if not args.get("all"):
        print(json.dumps(coupons, indent=4))
        return
    i = 0
    for section in coupons.get("sections", {}):
        for coupon in section.get("coupons", {}):
            if coupon["isActivated"]:
                continue
            if datetime.fromisoformat(coupon["startValidityDate"]) > datetime.now(timezone.utc):
                continue
            if datetime.fromisoformat(coupon["endValidityDate"]) < datetime.now(timezone.utc):
                continue
            print("activating coupon: ", coupon["title"])
            lidl_plus.activate_coupon(coupon["id"])
            i += 1
    # Some coupons are only available through V1 API
    coupons = lidl_plus.coupon_promotions_v1()
    for section in coupons.get("sections", {}):
        for coupon in section.get("promotions", {}):
            if coupon["isActivated"]:
                continue
            validity = coupon.get("validity", {})
            if datetime.fromisoformat(validity["start"]) > datetime.now(timezone.utc):
                continue
            if datetime.fromisoformat(validity["end"]) < datetime.now(timezone.utc):
                continue
            print("activating coupon v1: ", coupon["title"])
            lidl_plus.activate_coupon_promotion_v1(coupon["promotionId"])
            i += 1
    print(f"Activated {i} coupons")


def sync_cache(args):
    """Sync new tickets to cache"""
    if not args.get("cache"):
        print("Error: --cache FILE is required for sync")
        sys.exit(1)
    lidl_plus = lidl_plus_login(args)
    print("Syncing tickets...")
    new_count = lidl_plus.sync()
    print(f"Done. {new_count} new ticket(s) added to cache.")


def print_stats(args):
    """Print analytics from cache"""
    if not args.get("cache"):
        print("Error: --cache FILE is required for stats")
        sys.exit(1)
    lidl_plus = lidl_plus_login(args)
    stats = {
        "current_month_spending": lidl_plus.current_month_spending(),
        "average_basket": lidl_plus.average_basket(),
        "shopping_frequency_days": lidl_plus.shopping_frequency_days(),
        "spending_by_month": lidl_plus.spending_by_month(),
        "spending_by_store": lidl_plus.spending_by_store(),
        "frequently_bought_top10": lidl_plus.frequently_bought(10),
        "restock_suggestions": lidl_plus.restock_suggestions(),
    }
    print(json.dumps(stats, indent=4, ensure_ascii=False))


def main():
    """argument commands"""
    args = get_arguments()
    if args.get("auth"):
        print_refresh_token(args)
    elif args.get("id"):
        print_loyalty_id(args)
    elif args.get("receipt"):
        print_tickets(args)
    elif args.get("coupon"):
        activate_coupons(args)
    elif args.get("sync"):
        sync_cache(args)
    elif args.get("stats"):
        print_stats(args)


def start():
    """wrapper for cmd tool"""
    try:
        main()
    except KeyboardInterrupt:
        print("Aborted.")


if __name__ == "__main__":
    start()
