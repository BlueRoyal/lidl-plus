"""Sensor entities for Lidl Plus integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


def _parse_dt(s: Any) -> datetime | None:
    """Parse an ISO datetime string to a timezone-aware datetime object."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_AVERAGE_BASKET,
    KEY_CATEGORY_FOOD_SPENDING,
    KEY_CATEGORY_NONFOOD_SPENDING,
    KEY_COUPONS_ACTIVATED,
    KEY_COUPONS_AVAILABLE,
    KEY_CURRENT_MONTH_SPENDING,
    KEY_FREQUENTLY_BOUGHT,
    KEY_LAST_ERROR,
    KEY_LAST_SYNC,
    KEY_LOYALTY_ID,
    KEY_NEW_TICKETS_LAST_SYNC,
    KEY_PRICE_CHANGES,
    KEY_PRODUCTS,
    KEY_RECEIPTS,
    KEY_RESTOCK_SUGGESTIONS,
    KEY_SHOPPING_FREQUENCY,
    KEY_SPENDING_BY_MONTH,
    KEY_SPENDING_BY_STORE,
    KEY_TOTAL_TICKETS,
)
from .coordinator import LidlPlusCoordinator


@dataclass(frozen=True, kw_only=True)
class LidlPlusSensorDescription(SensorEntityDescription):
    """Sensor description with value and attribute callables."""

    value_fn: Callable[[dict], Any] = lambda _: None
    attrs_fn: Callable[[dict], dict] | None = None


SENSOR_DESCRIPTIONS: tuple[LidlPlusSensorDescription, ...] = (
    # ── Monatliche Ausgaben ──────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_CURRENT_MONTH_SPENDING,
        name="Current Month Spending",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:calendar-month",
        value_fn=lambda d: d[KEY_CURRENT_MONTH_SPENDING],
        attrs_fn=lambda d: {"spending_by_month": d[KEY_SPENDING_BY_MONTH]},
    ),
    # ── Durchschnittlicher Einkauf ───────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_AVERAGE_BASKET,
        name="Average Basket",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:basket",
        value_fn=lambda d: d[KEY_AVERAGE_BASKET],
        attrs_fn=lambda d: {"total_receipts": d[KEY_TOTAL_TICKETS]},
    ),
    # ── Einkaufsfrequenz ─────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_SHOPPING_FREQUENCY,
        name="Shopping Frequency",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:calendar-clock",
        value_fn=lambda d: d[KEY_SHOPPING_FREQUENCY],
    ),
    # ── Ausgaben Lebensmittel (Steuerklasse B = 7 %) ─────────────────────────
    LidlPlusSensorDescription(
        key=KEY_CATEGORY_FOOD_SPENDING,
        name="Food Category Spending",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:food",
        value_fn=lambda d: d[KEY_CATEGORY_FOOD_SPENDING],
    ),
    # ── Ausgaben Non-Food (Steuerklasse A = 19 %) ────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_CATEGORY_NONFOOD_SPENDING,
        name="Non-Food Category Spending",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:package-variant",
        value_fn=lambda d: d[KEY_CATEGORY_NONFOOD_SPENDING],
    ),
    # ── Kassenbons gesamt ────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_TOTAL_TICKETS,
        name="Total Receipts",
        native_unit_of_measurement="receipts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:receipt-text",
        value_fn=lambda d: d[KEY_TOTAL_TICKETS],
    ),
    # ── Neue Kassenbons seit letzter Sync ────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_NEW_TICKETS_LAST_SYNC,
        name="New Receipts Last Sync",
        native_unit_of_measurement="receipts",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:receipt-text-plus",
        value_fn=lambda d: d[KEY_NEW_TICKETS_LAST_SYNC],
    ),
    # ── Verfügbare Coupons ───────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_COUPONS_AVAILABLE,
        name="Coupons Available",
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
                for c in d.get("coupons", [])
                if not (c.get("activated") or c.get("isActivated") or c.get("isActive"))
            ][:20]
        },
    ),
    # ── Aktivierte Coupons ───────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_COUPONS_ACTIVATED,
        name="Coupons Activated",
        native_unit_of_measurement="coupons",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ticket-confirmation",
        value_fn=lambda d: d[KEY_COUPONS_ACTIVATED],
    ),
    # ── Preisänderungen erkannt ──────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="price_changes_count",
        name="Price Changes Detected",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-multiple",
        value_fn=lambda d: len(d[KEY_PRICE_CHANGES]),
        attrs_fn=lambda d: {
            "price_changes": d[KEY_PRICE_CHANGES],
            "items_with_increase": sum(
                1 for c in d[KEY_PRICE_CHANGES] if c["change_pct"] > 0
            ),
            "items_with_decrease": sum(
                1 for c in d[KEY_PRICE_CHANGES] if c["change_pct"] < 0
            ),
        },
    ),
    # ── Nachkauf-Vorschläge ──────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="restock_suggestions_count",
        name="Restock Suggestions",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cart-arrow-down",
        value_fn=lambda d: len(d[KEY_RESTOCK_SUGGESTIONS]),
        attrs_fn=lambda d: {
            "suggestions": d[KEY_RESTOCK_SUGGESTIONS][:10],
            "most_overdue": (
                d[KEY_RESTOCK_SUGGESTIONS][0]["name"]
                if d[KEY_RESTOCK_SUGGESTIONS]
                else None
            ),
        },
    ),
    # ── Ausgaben nach Monat (mit vollständiger Monatsliste) ──────────────────
    LidlPlusSensorDescription(
        key=KEY_SPENDING_BY_MONTH,
        name="Spending by Month",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:chart-bar",
        value_fn=lambda d: d[KEY_CURRENT_MONTH_SPENDING],
        attrs_fn=lambda d: {"months": d[KEY_SPENDING_BY_MONTH]},
    ),
    # ── Hauptfiliale (mit vollständiger Filialliste) ─────────────────────────
    LidlPlusSensorDescription(
        key=KEY_SPENDING_BY_STORE,
        name="Top Store",
        icon="mdi:store",
        value_fn=lambda d: next(iter(d[KEY_SPENDING_BY_STORE]), None),
        attrs_fn=lambda d: {
            "stores": d[KEY_SPENDING_BY_STORE],
            "top_store_total": next(iter(d[KEY_SPENDING_BY_STORE].values()), 0.0),
        },
    ),
    # ── Meistgekaufter Artikel (mit Top-10-Liste) ────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_FREQUENTLY_BOUGHT,
        name="Most Bought Item",
        icon="mdi:star",
        value_fn=lambda d: (
            d[KEY_FREQUENTLY_BOUGHT][0]["name"] if d[KEY_FREQUENTLY_BOUGHT] else None
        ),
        attrs_fn=lambda d: {"items": d[KEY_FREQUENTLY_BOUGHT]},
    ),
    # ── Produkt-Übersicht ────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_PRODUCTS,
        name="Artikel gesamt",
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
                for p in d.get(KEY_PRODUCTS, [])[:100]
            ],
        },
    ),
    # ── Kassenbons ───────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_RECEIPTS,
        name="Kassenbons",
        native_unit_of_measurement="Bons",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:receipt-text-multiple",
        value_fn=lambda d: len(d.get(KEY_RECEIPTS, [])),
        attrs_fn=lambda d: {
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
                for r in d.get(KEY_RECEIPTS, [])
            ],
        },
    ),
    # ── Letzter Einkauf ──────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="last_receipt",
        name="Letzter Einkauf",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:store-clock",
        value_fn=lambda d: (
            _parse_dt(d.get(KEY_RECEIPTS, [{}])[0].get("date")) if d.get(KEY_RECEIPTS) else None
        ),
        attrs_fn=lambda d: (
            {
                "store": d[KEY_RECEIPTS][0]["store"],
                "total": d[KEY_RECEIPTS][0]["total"],
                "items": d[KEY_RECEIPTS][0].get("items", []),
            }
            if d.get(KEY_RECEIPTS) else {}
        ),
    ),
    # ── Letzter Fehler ───────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LAST_ERROR,
        name="Last Error",
        icon="mdi:alert-circle",
        value_fn=lambda d: d.get(KEY_LAST_ERROR) or "OK",
    ),
    # ── Protokoll ────────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key="log",
        name="Protokoll",
        icon="mdi:text-box-outline",
        value_fn=lambda d: (d.get("log") or [""])[-1],  # letzter Eintrag als State
        attrs_fn=lambda d: {"entries": list(reversed(d.get("log") or []))},
    ),
    # ── Letzte Sync ──────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LAST_SYNC,
        name="Last Sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:sync",
        value_fn=lambda d: _parse_dt(d[KEY_LAST_SYNC]),
    ),
    # ── Loyalty-ID ───────────────────────────────────────────────────────────
    LidlPlusSensorDescription(
        key=KEY_LOYALTY_ID,
        name="Loyalty ID",
        icon="mdi:card-account-details",
        value_fn=lambda d: d[KEY_LOYALTY_ID],
    ),
)


class LidlPlusSensor(CoordinatorEntity[LidlPlusCoordinator], SensorEntity):
    """A sensor that reads a single value from the Lidl Plus coordinator."""

    entity_description: LidlPlusSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LidlPlusCoordinator,
        description: LidlPlusSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Lidl Plus",
            "manufacturer": "Lidl",
            "model": "Lidl Plus App",
        }

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None

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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all Lidl Plus sensors."""
    coordinator: LidlPlusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LidlPlusSensor(coordinator, description, entry.entry_id)
        for description in SENSOR_DESCRIPTIONS
    )
