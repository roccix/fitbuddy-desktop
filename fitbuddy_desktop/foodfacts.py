"""Barcode lookup on Open Food Facts, mapped like the phone's OpenFoodFactsDataSource."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import model

NAME_FIELDS = ("product_name_it", "product_name", "product_name_en", "generic_name_it", "generic_name")
API = ("https://world.openfoodfacts.org/api/v2/product/{code}?fields=code,brands,serving_size,nutriments,"
       + ",".join(NAME_FIELDS))
USER_AGENT = "FitBuddyDesktop/0.2 (https://github.com/roccix/fitbuddy-desktop)"


class ProductNotFound(LookupError):
    pass


class ServiceUnavailable(OSError):
    """Open Food Facts answered, but with an error page (overloaded, rate-limited, maintenance)."""


def _number(value) -> float | None:
    """Nutriments are usually numbers, but some older products store them as strings."""
    if isinstance(value, str):
        try:
            value = float(value.replace(",", "."))
        except ValueError:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def product_name(product: dict) -> str:
    """Italian name first, then the main one; adds the first brand unless the name already has it."""
    name = next((v.strip() for f in NAME_FIELDS if isinstance(v := product.get(f), str) and v.strip()), "")
    brand = next((b.strip() for b in (product.get("brands") or "").split(",") if b.strip()), "")
    if brand and brand.casefold() not in name.casefold():
        name = f"{name} · {brand}" if name else brand
    return name or "Packaged food"


def parse_serving_grams(serving_size: str | None) -> int | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*g", serving_size or "", re.IGNORECASE)
    grams = model.round_half_up(float(match.group(1).replace(",", "."))) if match else 0
    return grams if grams > 0 else None


def product_to_food(code: str, product: dict) -> model.Food:
    """The food at one serving (or 100 g), with per-100 g rates so the weight can be changed."""
    name = product_name(product)
    serving = parse_serving_grams(product.get("serving_size"))
    nutriments = product.get("nutriments") or {}

    def per100(key: str) -> float | None:
        value = _number(nutriments.get(f"{key}_100g"))
        if value is not None:
            return value
        value = _number(nutriments.get(f"{key}_serving"))
        if serving and value is not None:
            return value * 100 / serving
        return None

    kcal = per100("energy-kcal")
    if kcal is None:
        # Many labels only list kJ; "energy" without a suffix is always kJ.
        kj = per100("energy-kj")
        kj = per100("energy") if kj is None else kj
        kcal = kj / 4.184 if kj is not None else None
    if kcal is None:
        raise ProductNotFound(code)
    ingredient = model.Ingredient(name, 1, serving or 100, kcal, per100("proteins") or 0.0,
                                  per100("carbohydrates") or 0.0, per100("fat") or 0.0)
    return model.Food.of(name, [ingredient])


def lookup(barcode: str, timeout: float = 10) -> model.Food:
    """Blocking; raises ProductNotFound, or OSError for network problems."""
    code = model.normalize_barcode(barcode)
    if not code:
        raise ValueError("Invalid barcode")
    request = urllib.request.Request(API.format(code=code), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ProductNotFound(code) from error
        raise ServiceUnavailable(f"HTTP {error.code}") from error
    except ValueError as error:
        # An HTML "temporarily unavailable" page instead of JSON.
        raise ServiceUnavailable("Not a JSON answer") from error
    if data.get("status") != 1 or not data.get("product"):
        raise ProductNotFound(code)
    return product_to_food(code, data["product"])
