"""Sensor entities for Lidl Plus integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, MAX_LENGTH_STATE_STATE, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    KEY_AVERAGE_BASKET,
    KEY_BUSY_TIMES,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_CURRENT_MONTH_START,
    KEY_FREQUENTLY_BOUGHT,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_LEAFLET_REGION,
    KEY_LEAFLETS,
    KEY_LOG,
    KEY_LOYALTY_ID,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_OFFER_STORES,
    KEY_OFFERS,
    KEY_OFFERS_CURRENT,
    KEY_OFFERS_FOR_YOU,
    KEY_OFFERS_UPCOMING,
    KEY_PRICE_CHANGES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_RESTOCK_SUGGESTIONS,
    KEY_SAVINGS_BY_MONTH,
    KEY_SAVINGS_MONTH,
    KEY_SAVINGS_TOTAL,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_TOTAL_TICKETS,
)
from ._lidlplus.analytics import coupon_is_activated
from .coordinator import LidlPlusConfigEntry, LidlPlusCoordinator

# Data comes from the coordinator, entities never poll themselves
PARALLEL_UPDATES = 0

# The receipt and product lists in the attributes are limited, the panel shows everything
ATTR_RECEIPT_LIMIT = 50
ATTR_PRODUCT_LIMIT = 100


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp, receipt times without offset are local time."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _truncate(value: str | None) -> str | None:
    """Home Assistant rejects states longer than 255 characters."""
    if value is None or len(value) <= MAX_LENGTH_STATE_STATE:
        return value
    return value[: MAX_LENGTH_STATE_STATE - 1] + "…"


def _month_start(data: dict) -> datetime | None:
    return _parse_timestamp(data.get(KEY_CURRENT_MONTH_START))


def _offer_summary(offer: dict) -> dict:
    """The fields of an offer that are useful in templates and notifications"""
    return {
        "title": offer["title"],
        "brand": offer["brand"],
        "price": offer["price"],
        "regular_price": offer["regular_price"],
        "price_text": offer["price_text"],
        "discount": offer["discount"],
        "start": offer["start"],
        "end": offer["end"],
        "bought_products": [product["name"] for product in offer.get("bought_products", [])],
    }


def _leaflet_summary(leaflet: dict) -> dict:
    """The fields of a leaflet that are useful in templates and notifications"""
    return {
        "name": leaflet["name"],
        "title": leaflet["title"],
        "category": leaflet["category"],
        "status": leaflet["status"],
        "start": leaflet["start"],
        "end": leaflet["end"],
        "pdf": leaflet["pdf"],
        "url": leaflet["url"],
        "products": len(leaflet.get("products") or []),
    }


def _busyness_now(data: dict) -> int | None:
    """Expected busyness of the store in this hour, from the forecast of BestTime.app"""
    forecast = (data.get(KEY_BUSY_TIMES) or {}).get("forecast")
    if not forecast:
        return None
    now = dt_util.now()
    return forecast["hours"][now.weekday()][now.hour]


def _busyness_attributes(data: dict) -> dict:
    busy = data.get(KEY_BUSY_TIMES) or {}
    forecast = busy.get("forecast") or {}
    return {
        "store": (busy.get("store") or {}).get("name"),
        # Busyness of every hour of today, for cards and automations
        "today": forecast["hours"][dt_util.now().weekday()] if forecast else None,
        "forecast_updated": forecast.get("updated"),
        "source": "BestTime.app" if forecast else None,
    }


@dataclass(frozen=True, kw_only=True)
class LidlPlusSensorDescription(SensorEntityDescription):
    """Sensor description with value and attribute callables."""

    value_fn: Callable[[dict], Any] = lambda _: None
    attrs_fn: Callable[[dict], dict] | None = None
    last_reset_fn: Callable[[dict], datetime | None] | None = None
    # The value depends on the hour of the day, the state is written every hour
    hourly: bool = False


SENSOR_DESCRIPTIONS: tuple[LidlPlusSensorDescription, ...] = (
    # ── Monatliche Ausgaben ──────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_CURRENT_MONTH_SPENDING,
        translation_key=KEY_CURRENT_MONTH_SPENDING,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:calendar-month",
        value_fn=lambda d: d[KEY_CURRENT_MONTH_SPENDING],
        attrs_fn=lambda d: {"spending_by_month": d[KEY_SPENDING_BY_MONTH]},
        last_reset_fn=_month_start,
    ),
    # ── Durchschnittlicher Einkauf ───────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_AVERAGE_BASKET,
        translation_key=KEY_AVERAGE_BASKET,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:basket",
        value_fn=lambda d: d[KEY_AVERAGE_BASKET],
        attrs_fn=lambda d: {"total_receipts": d[KEY_TOTAL_TICKETS]},
    ),
    # ── Einkaufsfrequenz ─────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_SHOPPING_FREQUENCY,
        translation_key=KEY_SHOPPING_FREQUENCY,
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-clock",
        value_fn=lambda d: d[KEY_SHOPPING_FREQUENCY],
    ),
    # ── Ausgaben Lebensmittel (ermäßigter Steuersatz, in Deutschland A = 7 %) ──
    LidlPlusSensorDescription(
        key=KEY_CATEGORY_FOOD_SPENDING,
        translation_key=KEY_CATEGORY_FOOD_SPENDING,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:food",
        value_fn=lambda d: d[KEY_CATEGORY_FOOD_SPENDING],
    ),
    # ── Ausgaben Non-Food (Regelsteuersatz, in Deutschland B = 19 %) ──────────
    LidlPlusSensorDescription(
        key=KEY_CATEGORY_NONFOOD_SPENDING,
        translation_key=KEY_CATEGORY_NONFOOD_SPENDING,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:package-variant",
        value_fn=lambda d: d[KEY_CATEGORY_NONFOOD_SPENDING],
    ),
    # ── Kassenbons gesamt ────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_TOTAL_TICKETS,
        translation_key=KEY_TOTAL_TICKETS,
        native_unit_of_measurement="receipts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:receipt-text",
        value_fn=lambda d: d[KEY_TOTAL_TICKETS],
    ),
    # ── Neue Kassenbons seit letzter Sync ────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_NEW_TICKETS_LAST_SYNC,
        translation_key=KEY_NEW_TICKETS_LAST_SYNC,
        native_unit_of_measurement="receipts",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:receipt-text-plus",
        value_fn=lambda d: d[KEY_NEW_TICKETS_LAST_SYNC],
    ),
    # ── Verfügbare Coupons ───────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_COUPONS_AVAILABLE,
        translation_key=KEY_COUPONS_AVAILABLE,
        native_unit_of_measurement="coupons",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ticket-percent",
        value_fn=lambda d: d[KEY_COUPONS_AVAILABLE],
        attrs_fn=lambda d: {
            "coupons": [
                {
                    "id": c.get("id"),
                    "title": c.get("title"),
                    "end": c.get("endValidityDate") or c.get("end"),
                }
                for c in d.get(KEY_COUPONS, [])
                if not coupon_is_activated(c)
            ][:20]
        },
    ),
    # ── Aktivierte Coupons ───────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_COUPONS_ACTIVATED,
        translation_key=KEY_COUPONS_ACTIVATED,
        native_unit_of_measurement="coupons",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ticket-confirmation",
        value_fn=lambda d: d[KEY_COUPONS_ACTIVATED],
    ),
    # ── Preisänderungen erkannt ──────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="price_changes_count",
        translation_key="price_changes_count",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-multiple",
        value_fn=lambda d: len(d[KEY_PRICE_CHANGES]),
        attrs_fn=lambda d: {
            "price_changes": d[KEY_PRICE_CHANGES],
            "items_with_increase": sum(1 for c in d[KEY_PRICE_CHANGES] if c["change_pct"] > 0),
            "items_with_decrease": sum(1 for c in d[KEY_PRICE_CHANGES] if c["change_pct"] < 0),
        },
    ),
    # ── Nachkauf-Vorschläge ──────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="restock_suggestions_count",
        translation_key="restock_suggestions_count",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cart-arrow-down",
        value_fn=lambda d: len(d[KEY_RESTOCK_SUGGESTIONS]),
        attrs_fn=lambda d: {
            "suggestions": d[KEY_RESTOCK_SUGGESTIONS][:10],
            "most_overdue": (d[KEY_RESTOCK_SUGGESTIONS][0]["name"] if d[KEY_RESTOCK_SUGGESTIONS] else None),
        },
    ),
    # ── Ausgaben nach Monat (mit vollständiger Monatsliste) ──────────────────
    LidlPlusSensorDescription(
        key=KEY_SPENDING_BY_MONTH,
        translation_key=KEY_SPENDING_BY_MONTH,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:chart-bar",
        value_fn=lambda d: d[KEY_CURRENT_MONTH_SPENDING],
        attrs_fn=lambda d: {"months": d[KEY_SPENDING_BY_MONTH]},
        last_reset_fn=_month_start,
    ),
    # ── Hauptfiliale (mit vollständiger Filialliste) ─────────────────────────
    LidlPlusSensorDescription(
        key=KEY_SPENDING_BY_STORE,
        translation_key=KEY_SPENDING_BY_STORE,
        icon="mdi:store",
        value_fn=lambda d: _truncate(next(iter(d[KEY_SPENDING_BY_STORE]), None)),
        attrs_fn=lambda d: {
            "stores": d[KEY_SPENDING_BY_STORE],
            "top_store_total": next(iter(d[KEY_SPENDING_BY_STORE].values()), 0.0),
        },
    ),
    # ── Meistgekaufter Artikel (mit Top-10-Liste) ────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_FREQUENTLY_BOUGHT,
        translation_key=KEY_FREQUENTLY_BOUGHT,
        icon="mdi:star",
        value_fn=lambda d: (_truncate(d[KEY_FREQUENTLY_BOUGHT][0]["name"]) if d[KEY_FREQUENTLY_BOUGHT] else None),
        attrs_fn=lambda d: {"items": d[KEY_FREQUENTLY_BOUGHT]},
    ),
    # ── Produkt-Übersicht ────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_PRODUCTS,
        translation_key=KEY_PRODUCTS,
        native_unit_of_measurement="Artikel",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:food-variant",
        value_fn=lambda d: len(d.get(KEY_PRODUCTS, [])),
        attrs_fn=lambda d: {
            # Top 100 nach Kaufhäufigkeit — für Automationen und Lovelace
            "products": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "purchase_count": p["purchase_count"],
                    "total_quantity": p["total_quantity"],
                    "total_spent": p["total_spent"],
                    "avg_price": p["avg_price"],
                    "last_price": p["last_price"],
                    "last_date": p["last_date"],
                    "last_store": p["last_store"],
                }
                for p in d.get(KEY_PRODUCTS, [])[:ATTR_PRODUCT_LIMIT]
            ],
        },
    ),
    # ── Kassenbons ───────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_RECEIPTS,
        translation_key=KEY_RECEIPTS,
        native_unit_of_measurement="Bons",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:receipt-text-multiple",
        value_fn=lambda d: len(d.get(KEY_RECEIPTS, [])),
        attrs_fn=lambda d: {
            # Die letzten 50 Kassenbons — alle Bons zeigt das Panel
            "receipts": [
                {
                    "id": r["id"],
                    "date": r["date"],
                    "store": r["store"],
                    "total": r["total"],
                    "item_count": len(r.get("items", [])),
                    "items": [
                        {
                            "id": i["id"],
                            "name": i["name"],
                            "quantity": i["quantity"],
                            "unit_price": i["unit_price"],
                        }
                        for i in r.get("items", [])
                    ],
                }
                for r in d.get(KEY_RECEIPTS, [])[:ATTR_RECEIPT_LIMIT]
            ],
        },
    ),
    # ── Letzter Einkauf ──────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="last_receipt",
        translation_key="last_receipt",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:store-clock",
        value_fn=lambda d: (_parse_timestamp(d[KEY_RECEIPTS][0]["date"]) if d.get(KEY_RECEIPTS) else None),
        attrs_fn=lambda d: (
            {
                "store": d[KEY_RECEIPTS][0]["store"],
                "total": d[KEY_RECEIPTS][0]["total"],
                "items": d[KEY_RECEIPTS][0].get("items", []),
            }
            if d.get(KEY_RECEIPTS)
            else {}
        ),
    ),
    # ── Letzter Fehler ───────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LAST_ERROR,
        translation_key=KEY_LAST_ERROR,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle",
        value_fn=lambda d: _truncate(d.get(KEY_LAST_ERROR) or "OK"),
        attrs_fn=lambda d: {"message": d.get(KEY_LAST_ERROR)},
    ),
    # ── Protokoll ────────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LOG,
        translation_key=KEY_LOG,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:text-box-outline",
        value_fn=lambda d: _truncate((d.get(KEY_LOG) or [""])[-1]),  # letzter Eintrag als State
        attrs_fn=lambda d: {"entries": list(reversed(d.get(KEY_LOG) or []))},
    ),
    # ── Letzte Sync ──────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LAST_SYNC,
        translation_key=KEY_LAST_SYNC,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sync",
        value_fn=lambda d: _parse_timestamp(d[KEY_LAST_SYNC]),
    ),
    # ── Loyalty-ID ───────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LOYALTY_ID,
        translation_key=KEY_LOYALTY_ID,
        icon="mdi:card-account-details",
        value_fn=lambda d: d[KEY_LOYALTY_ID],
    ),
    # ── Ersparnis durch Rabatte ──────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_SAVINGS_TOTAL,
        translation_key=KEY_SAVINGS_TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:piggy-bank",
        value_fn=lambda d: d[KEY_SAVINGS_TOTAL],
        attrs_fn=lambda d: {"savings_by_month": d[KEY_SAVINGS_BY_MONTH]},
    ),
    LidlPlusSensorDescription(
        key=KEY_SAVINGS_MONTH,
        translation_key=KEY_SAVINGS_MONTH,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:piggy-bank-outline",
        value_fn=lambda d: d[KEY_SAVINGS_MONTH],
        last_reset_fn=_month_start,
    ),
    # ── Angebote der Filiale ─────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_OFFERS_CURRENT,
        translation_key=KEY_OFFERS_CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-heart",
        value_fn=lambda d: d[KEY_OFFERS_CURRENT],
        attrs_fn=lambda d: {
            "stores": d[KEY_OFFER_STORES],
            "offers": [_offer_summary(offer) for offer in d[KEY_OFFERS] if offer["status"] == "current"],
        },
    ),
    LidlPlusSensorDescription(
        key=KEY_OFFERS_UPCOMING,
        translation_key=KEY_OFFERS_UPCOMING,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-arrow-right",
        value_fn=lambda d: d[KEY_OFFERS_UPCOMING],
        attrs_fn=lambda d: {
            "stores": d[KEY_OFFER_STORES],
            "offers": [_offer_summary(offer) for offer in d[KEY_OFFERS] if offer["status"] == "upcoming"],
        },
    ),
    # ── Angebote für Artikel, die du schon gekauft hast ──────────────────────
    LidlPlusSensorDescription(
        key=KEY_OFFERS_FOR_YOU,
        translation_key=KEY_OFFERS_FOR_YOU,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cart-heart",
        value_fn=lambda d: len(d[KEY_OFFERS_FOR_YOU]),
        attrs_fn=lambda d: {"offers": [_offer_summary(offer) for offer in d[KEY_OFFERS_FOR_YOU]]},
    ),
    # ── Stoßzeiten: erwartete Auslastung der Filiale in dieser Stunde ─────────
    LidlPlusSensorDescription(
        key="store_busyness",
        translation_key="store_busyness",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:account-group",
        value_fn=_busyness_now,
        attrs_fn=_busyness_attributes,
        hourly=True,
    ),
    # ── Aktuelle und kommende Prospekte ──────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LEAFLETS,
        translation_key=KEY_LEAFLETS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:newspaper-variant-multiple",
        value_fn=lambda d: len(d[KEY_LEAFLETS]),
        attrs_fn=lambda d: {
            "leaflets": [_leaflet_summary(leaflet) for leaflet in d[KEY_LEAFLETS]],
            # The weekly leaflets differ between the offer regions of Lidl
            "region": (d.get(KEY_LEAFLET_REGION) or {}).get("name"),
        },
    ),
)


class LidlPlusSensor(CoordinatorEntity[LidlPlusCoordinator], SensorEntity):
    """A sensor that reads a single value from the Lidl Plus coordinator."""

    entity_description: LidlPlusSensorDescription
    _attr_has_entity_name = True
    # Large lists are useful for templates and cards, but must not bloat the recorder database
    _unrecorded_attributes = frozenset(
        {
            "coupons",
            "entries",
            "items",
            "leaflets",
            "months",
            "offers",
            "price_changes",
            "products",
            "receipts",
            "savings_by_month",
            "spending_by_month",
            "stores",
            "suggestions",
            "today",
        }
    )

    def __init__(
        self,
        coordinator: LidlPlusCoordinator,
        description: LidlPlusSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Lidl Plus",
            manufacturer="Lidl",
            model="Lidl Plus App",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.hourly:
            self.async_on_remove(async_track_time_change(self.hass, self._async_new_hour, minute=0, second=5))

    @callback
    def _async_new_hour(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.last_reset_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.last_reset_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        try:
            return self.entity_description.attrs_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LidlPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all Lidl Plus sensors."""
    coordinator = entry.runtime_data
    async_add_entities(LidlPlusSensor(coordinator, description, entry.entry_id) for description in SENSOR_DESCRIPTIONS)
