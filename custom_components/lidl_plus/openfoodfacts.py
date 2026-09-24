"""Product data by barcode from Open Food Facts and its sister databases for cosmetics and other products."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ._lidlplus.articles import NUTRIENTS

_LOGGER = logging.getLogger(__name__)

# Food first, then cosmetics and cleaning agents (with their ingredients), then everything else
DATABASES = (
    ("Open Food Facts", "https://world.openfoodfacts.org"),
    ("Open Beauty Facts", "https://world.openbeautyfacts.org"),
    ("Open Products Facts", "https://world.openproductsfacts.org"),
)
_FIELDS = ",".join(
    (
        "code",
        "product_name",
        "product_name_de",
        "generic_name_de",
        "brands",
        "quantity",
        "nutriments",
        "ingredients_text",
        "ingredients_text_de",
        "allergens_tags",
        "nutriscore_grade",
        "image_front_url",
        "image_nutrition_url",
        "image_ingredients_url",
    )
)
# Values per 100 g (or 100 ml for drinks) in the nutriments of Open Food Facts
_NUTRIMENTS = {
    "energy_kj": ("energy-kj_100g", "energy_100g"),
    "energy_kcal": ("energy-kcal_100g",),
    "fat": ("fat_100g",),
    "saturated_fat": ("saturated-fat_100g",),
    "carbohydrates": ("carbohydrates_100g",),
    "sugars": ("sugars_100g",),
    "fiber": ("fiber_100g",),
    "protein": ("proteins_100g",),
    "salt": ("salt_100g",),
}
_KJ_PER_KCAL = 4.184
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class LookupFailed(Exception):
    """None of the databases could be asked"""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number >= 0 else None


def _nutrition(nutriments: Any) -> dict[str, float]:
    nutriments = nutriments if isinstance(nutriments, dict) else {}
    values: dict[str, float] = {}
    for nutrient, keys in _NUTRIMENTS.items():
        number = next((_number(nutriments[key]) for key in keys if _number(nutriments.get(key)) is not None), None)
        if number is not None:
            values[nutrient] = number
    # Packages show both, the database often only one of them
    if "energy_kcal" in values and "energy_kj" not in values:
        values["energy_kj"] = round(values["energy_kcal"] * _KJ_PER_KCAL)
    elif "energy_kj" in values and "energy_kcal" not in values:
        values["energy_kcal"] = round(values["energy_kj"] / _KJ_PER_KCAL)
    return {nutrient: values[nutrient] for nutrient in NUTRIENTS if nutrient in values}


def _https(url: Any) -> str:
    return url if isinstance(url, str) and url.startswith("https://") else ""


def normalize_product(product: dict[str, Any], source: str, url: str) -> dict[str, Any]:
    """The fields of a product of Open Food Facts (API v2) that can be taken for an article"""
    quantity = str(product.get("quantity") or "").strip()
    # The values of drinks are per 100 ml
    liquid = any(unit in f" {quantity.lower()}" for unit in ("ml", " l", "cl", "liter"))
    brands = str(product.get("brands") or "")
    grade = str(product.get("nutriscore_grade") or "")
    return {
        "source": source,
        "url": url,
        "code": str(product.get("code") or ""),
        "name": str(
            product.get("product_name_de") or product.get("product_name") or product.get("generic_name_de") or ""
        ).strip(),
        "brand": brands.split(",")[0].strip(),
        "quantity": quantity,
        "nutrition": _nutrition(product.get("nutriments")),
        "nutrition_basis": "100ml" if liquid else "100g",
        "ingredients": str(product.get("ingredients_text_de") or product.get("ingredients_text") or "").strip(),
        "allergens": [
            str(tag).split(":", 1)[-1] for tag in product.get("allergens_tags") or [] if isinstance(tag, str)
        ],
        "nutriscore": grade if len(grade) == 1 and grade in "abcde" else "",
        "image": _https(product.get("image_front_url")),
        "image_nutrition": _https(product.get("image_nutrition_url")),
        "image_ingredients": _https(product.get("image_ingredients_url")),
    }


async def async_lookup(session: aiohttp.ClientSession, code: str, user_agent: str) -> dict[str, Any] | None:
    """
    The product of a barcode from the first database that knows it, None if none knows it.
    Raises LookupFailed if no database could be asked.
    """
    errors = []
    for source, base in DATABASES:
        try:
            async with session.get(
                f"{base}/api/v2/product/{code}.json",
                params={"fields": _FIELDS},
                headers={"User-Agent": user_agent, "Accept": "application/json"},
                timeout=_TIMEOUT,
            ) as response:
                if response.status == 404:
                    continue  # unknown product, answered with status 0
                if response.status != 200:
                    errors.append(f"{source}: HTTP {response.status}")
                    continue
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            errors.append(f"{source}: {str(err) or type(err).__name__}")
            continue
        if isinstance(data, dict) and data.get("status") == 1 and isinstance(data.get("product"), dict):
            return normalize_product(data["product"], source, f"{base}/product/{code}")
    if len(errors) == len(DATABASES):
        _LOGGER.debug("Barcode %s could not be looked up: %s", code, "; ".join(errors))
        raise LookupFailed("; ".join(errors))
    return None
