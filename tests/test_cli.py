"""Tests for the command line tool."""

import json
import sys
import zipfile

import pytest

from lidlplus import LidlPlusApi
from lidlplus import __main__ as cli
from lidlplus.exceptions import AuthenticationError
from sample_data import Receipt, flyer, leaflet, leaflet_overview, offer, ticket


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["lidl-plus", *args])
    cli.start()


def test_stats_needs_no_login(monkeypatch, tmp_path, capsys):
    cache = tmp_path / "cache.json"
    tickets = {"t1": ticket("t1", "2026-01-05T10:00:00+01:00", "9,99")}
    cache.write_text(json.dumps({"tickets": tickets}), encoding="utf-8")
    run_cli(monkeypatch, "--cache", str(cache), "stats")
    stats = json.loads(capsys.readouterr().out)
    assert stats["spending_by_month"] == {"2026-01": 9.99}
    assert stats["average_basket"] == 9.99


def test_stats_requires_cache(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "stats")
    assert "--cache FILE is required for stats" in capsys.readouterr().out


def test_activate_all_coupons(monkeypatch, capsys):
    monkeypatch.setattr(LidlPlusApi, "activate_all_coupons", lambda self: {"activated": ["A", "B"], "failed": ["C"]})
    run_cli(monkeypatch, "-l", "de", "-c", "DE", "-r", "token", "coupon", "--all")
    output = capsys.readouterr().out
    assert "activated coupon: A" in output
    assert "could not activate coupon: C" in output
    assert "Activated 2 coupons" in output


def test_replaced_token_is_shown(monkeypatch, capsys):
    def renew(self):
        self._refresh_token = "replaced-token"  # pylint: disable=protected-access
        return "4000000123456"

    monkeypatch.setattr(LidlPlusApi, "loyalty_id", renew)
    run_cli(monkeypatch, "-l", "de", "-c", "DE", "-r", "token", "id")
    output = capsys.readouterr()
    assert output.out.strip() == "4000000123456"
    assert "replaced-token" in output.err


def test_unchanged_token_is_not_shown(monkeypatch, capsys):
    monkeypatch.setattr(LidlPlusApi, "loyalty_id", lambda self: "4000000123456")
    run_cli(monkeypatch, "-l", "de", "-c", "DE", "-r", "token", "id")
    assert not capsys.readouterr().err


def test_rejected_token_is_reported(monkeypatch, capsys):
    def reject(self):
        raise AuthenticationError("invalid_grant")

    monkeypatch.setattr(LidlPlusApi, "loyalty_id", reject)
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "-l", "de", "-c", "DE", "-r", "token", "id")
    assert exit_info.value.code == 102
    assert "Authentication failed - invalid_grant" in capsys.readouterr().out


def write_cache(path):
    html = Receipt().article("0082052", "Feldsalat", "1,49").build()
    tickets = {"t1": {**ticket("t1", "2026-09-19T17:01:49", 1.49), "htmlPrintedReceipt": html}}
    path.write_text(json.dumps({"tickets": tickets}), encoding="utf-8")


@pytest.mark.parametrize(("name", "check"), [("export.zip", "zip"), ("items.csv", "csv"), ("items.json", "json")])
def test_export(monkeypatch, tmp_path, name, check):
    cache = tmp_path / "cache.json"
    write_cache(cache)
    output = tmp_path / name
    run_cli(monkeypatch, "--cache", str(cache), "export", str(output))
    if check == "zip":
        with zipfile.ZipFile(output) as archive:
            assert "lidl_plus_cache.json" in archive.namelist()
    elif check == "csv":
        lines = output.read_text(encoding="utf-8-sig").splitlines()
        assert lines[1].split(";")[5] == "Feldsalat"
    else:
        assert json.loads(output.read_text(encoding="utf-8"))[0]["name"] == "Feldsalat"


def test_export_table_of_the_file_name(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    write_cache(cache)
    run_cli(monkeypatch, "--cache", str(cache), "export", str(tmp_path / "receipts.csv"))
    assert (tmp_path / "receipts.csv").read_text(encoding="utf-8-sig").startswith("id;date;store_id;")
    # The option wins over the file name
    run_cli(monkeypatch, "--cache", str(cache), "export", str(tmp_path / "receipts.json"), "--dataset", "products")
    assert json.loads((tmp_path / "receipts.json").read_text(encoding="utf-8"))[0]["article_id"] == "0082052"


def test_stores(monkeypatch, capsys):
    calls = []

    def search(self, query, latitude, longitude):
        calls.append((query, latitude, longitude))
        return [{"storeKey": "DE1234", "name": "Musterstadt", "address": "Hauptstraße 1", "postalCode": "12345"}]

    monkeypatch.setattr(LidlPlusApi, "search_stores", search)
    run_cli(monkeypatch, "stores", "12345", "--position", "52.5,13.4")
    assert calls == [("12345", 52.5, 13.4)]
    assert capsys.readouterr().out.strip() == "DE1234\tMusterstadt\tHauptstraße 1, 12345"
    run_cli(monkeypatch, "-c", "AT", "stores", "Wien")
    assert calls[-1] == ("Wien", 47.52, 14.55)


def test_offers(monkeypatch, capsys):
    monkeypatch.setattr(
        LidlPlusApi,
        "store_offers",
        lambda self, key: [offer("o1", "Kaffee", ["1"], "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z")],
    )
    run_cli(monkeypatch, "offers", "DE1234")
    offers = json.loads(capsys.readouterr().out)
    assert (offers[0]["title"], offers[0]["status"], offers[0]["discount"]) == ("Kaffee", "current", "-46%")


def test_sync_with_offers(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(LidlPlusApi, "sync", lambda self: 2)
    stores = []
    monkeypatch.setattr(LidlPlusApi, "sync_offers", lambda self, keys: stores.extend(keys) or 5)
    run_cli(
        monkeypatch,
        "-l",
        "de",
        "-c",
        "DE",
        "-r",
        "token",
        "--cache",
        str(tmp_path / "c.json"),
        "sync",
        "--store",
        "DE1234",
    )
    assert stores == ["DE1234"]
    assert "5 new offer(s)" in capsys.readouterr().out


def test_sync_with_leaflets(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(LidlPlusApi, "sync", lambda self: 0)
    monkeypatch.setattr(LidlPlusApi, "sync_leaflets", lambda self: 3)
    run_cli(
        monkeypatch, "-l", "de", "-c", "DE", "-r", "token", "--cache", str(tmp_path / "c.json"), "sync", "--leaflets"
    )
    assert "3 new leaflet(s)" in capsys.readouterr().out


def test_leaflets(monkeypatch, capsys):
    overview = leaflet_overview(
        (
            "Filial-Angebote",
            [
                leaflet("l1", "Aktionsprospekt", "aktion-1", "2026-01-01", "2099-01-01"),
                leaflet("l2", "Alter Prospekt", "alt", "2026-01-01", "2026-01-03"),
            ],
        )
    )
    monkeypatch.setattr(LidlPlusApi, "leaflets", lambda self: overview)
    loaded = []

    def load(self, identifier):
        loaded.append(identifier)
        return flyer(("Kaffee Romana Salat", [("100001", "Akku-Bohrschrauber", "39.99")]))["flyer"]

    monkeypatch.setattr(LidlPlusApi, "leaflet", load)
    run_cli(monkeypatch, "leaflets")
    leaflets = json.loads(capsys.readouterr().out)
    # Leaflets that ended are left out
    assert [(entry["id"], entry["status"]) for entry in leaflets] == [("l1", "current")]
    assert not loaded

    run_cli(monkeypatch, "leaflets", "--search", "Kaffee")
    results = json.loads(capsys.readouterr().out)
    assert loaded == ["aktion-1"]
    assert results[0]["leaflet"]["id"] == "l1"
    assert results[0]["pages"][0]["number"] == 1


def test_export_does_not_replace_the_cache(monkeypatch, tmp_path, capsys):
    cache = tmp_path / "cache.json"
    write_cache(cache)
    before = cache.read_bytes()
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "--cache", str(cache), "export", str(tmp_path / "." / "cache.json"))
    assert exit_info.value.code == 1
    assert "would replace the cache" in capsys.readouterr().out
    assert cache.read_bytes() == before
