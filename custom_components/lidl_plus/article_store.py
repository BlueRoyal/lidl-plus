"""Details of the articles added by hand: barcodes, package size, nutrition values, ingredients, notes and photos.

They belong to the household, not to a Lidl Plus account, so all accounts share them. The photos are files in the
config folder (part of the backups of Home Assistant), the rest is kept in the storage of Home Assistant.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ._lidlplus.articles import NUTRIENTS, NUTRITION_BASES, barcode_key, merge_user_data
from .const import DOMAIN

DATA_ARTICLE_STORE = f"{DOMAIN}_article_store"
STORAGE_KEY = f"{DOMAIN}_articles"
STORAGE_VERSION = 1
# Inside the config folder, so the photos are part of the backups
IMAGE_DIR = "lidl_plus/images"
IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_IMAGE_BYTES = 2_500_000
MAX_IMAGES = 20
# Types of photos by the first bytes of the file
_IMAGE_TYPES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
)
_TEXT_FIELDS = ("name", "brand", "package_size", "ingredients", "notes", "image")


def article_store(hass: HomeAssistant) -> ArticleStore:
    """The details of the articles, shared by all accounts"""
    if DATA_ARTICLE_STORE not in hass.data:
        hass.data[DATA_ARTICLE_STORE] = ArticleStore(hass)
    return hass.data[DATA_ARTICLE_STORE]


class ArticleError(Exception):
    """A change that is not possible, the message is shown to the user"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def image_type(content: bytes) -> tuple[str, str] | None:
    """Content type and file extension of a JPEG, PNG or WebP photo, None for other files"""
    for magic, content_type, extension in _IMAGE_TYPES:
        if content.startswith(magic):
            return content_type, extension
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


class ArticleStore:
    """The details of all articles by article key (see _lidlplus.articles)"""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._articles: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self.image_dir = Path(hass.config.path(IMAGE_DIR))
        # Merged articles of the last catalog, see async_articles
        self._merged: tuple[dict[str, Any], int, dict[str, dict[str, Any]]] | None = None
        self._version = 0

    async def async_load(self) -> dict[str, dict[str, Any]]:
        """The details of all articles"""
        if self._articles is None:
            async with self._lock:
                if self._articles is None:
                    data = await self._store.async_load() or {}
                    self._articles = dict(data.get("articles") or {})
        return self._articles

    async def async_articles(self, catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """The articles of a catalog with their details, see merge_user_data"""
        articles = await self.async_load()
        if self._merged and self._merged[0] is catalog and self._merged[1] == self._version:
            return self._merged[2]
        merged = merge_user_data(catalog, articles)
        self._merged = (catalog, self._version, merged)
        return merged

    async def _async_save(self) -> None:
        self._version += 1
        await self._store.async_save({"articles": self._articles})

    def barcode_owner(self, code: str, other_than: str | None = None) -> str | None:
        """Key of the article with a barcode"""
        wanted = barcode_key(code)
        for key, data in (self._articles or {}).items():
            if key != other_than and any(barcode_key(barcode) == wanted for barcode in data.get("barcodes") or []):
                return key
        return None

    async def async_update(self, key: str, changes: dict[str, Any]) -> dict[str, Any]:
        """
        Change the details of an article: given fields replace the old ones, empty ones remove them.
        Barcodes must be normalized already and must not belong to another article.
        """
        await self.async_load()
        async with self._lock:
            articles = self._articles if self._articles is not None else {}
            for code in changes.get("barcodes") or []:
                if owner := self.barcode_owner(code, other_than=key):
                    name = (articles[owner].get("name") or owner) if owner in articles else owner
                    raise ArticleError("barcode_in_use", f"Der Barcode {code} gehört schon zu {name}")
            data = dict(articles.get(key) or {})
            for field in _TEXT_FIELDS:
                if field in changes:
                    data[field] = str(changes[field] or "").strip()
            if "barcodes" in changes:
                data["barcodes"] = list(dict.fromkeys(changes["barcodes"]))
            if "nutrition" in changes:
                data["nutrition"] = {
                    nutrient: changes["nutrition"][nutrient]
                    for nutrient in NUTRIENTS
                    if changes["nutrition"].get(nutrient) is not None
                }
            if changes.get("nutrition_basis") in NUTRITION_BASES:
                data["nutrition_basis"] = changes["nutrition_basis"]
            if "sources" in changes:
                data["sources"] = {**(data.get("sources") or {}), **changes["sources"]}
            return await self._async_store(key, data)

    async def _async_store(self, key: str, data: dict[str, Any]) -> dict[str, Any]:
        """Save the details of an article (with the lock), without empty fields"""
        data = {field: value for field, value in data.items() if value not in ("", [], {}, None)}
        now = dt_util.utcnow().isoformat()
        data.setdefault("created", now)
        data["updated"] = now
        articles = self._articles if self._articles is not None else {}
        articles[key] = data
        self._articles = articles
        await self._async_save()
        return data

    async def async_link_leaflet(
        self, key: str, leaflet: dict[str, Any], page: int | None, remove: bool = False
    ) -> dict[str, Any]:
        """
        Add an article to a page of a leaflet (it is printed there, but Lidl does not know it), or remove it from a
        page again; without page from all pages of the leaflet
        """
        await self.async_load()
        async with self._lock:
            data = dict((self._articles or {}).get(key) or {})
            links = [dict(link) for link in data.get("leaflets") or []]
            link = next((entry for entry in links if entry["id"] == leaflet["id"]), None)
            if link is None and not remove:
                link = {field: leaflet.get(field) or "" for field in ("id", "name", "title", "start", "end")}
                link["pages"] = []
                links.insert(0, link)
            if link is not None:
                if not remove:
                    link["pages"] = sorted({*link["pages"], page})
                else:
                    link["pages"] = [number for number in link["pages"] if page is not None and number != page]
            data["leaflets"] = [entry for entry in links if entry["pages"]]
            return await self._async_store(key, data)

    async def async_delete(self, key: str) -> None:
        """Remove the details and photos of an article, an article added by hand is removed completely"""
        await self.async_load()
        async with self._lock:
            data = (self._articles or {}).pop(key, None)
            if data is None:
                return
            await self._async_save()
        for image in data.get("images") or []:
            await self.hass.async_add_executor_job(self._remove_file, image)

    async def async_add_image(self, key: str, kind: str, content: bytes) -> dict[str, Any]:
        """Save a photo of an article, JPEG, PNG or WebP"""
        if len(content) > MAX_IMAGE_BYTES:
            raise ArticleError("too_large", f"Das Foto ist größer als {MAX_IMAGE_BYTES // 1_000_000} MB")
        detected = image_type(content)
        if detected is None:
            raise ArticleError("invalid_format", "Nur Fotos im Format JPEG, PNG oder WebP")
        await self.async_load()
        async with self._lock:
            articles = self._articles if self._articles is not None else {}
            data = dict(articles.get(key) or {})
            if len(data.get("images") or []) >= MAX_IMAGES:
                raise ArticleError("too_many", f"Ein Artikel kann höchstens {MAX_IMAGES} Fotos haben")
            image = {
                "id": secrets.token_hex(16),
                "kind": kind,
                "type": detected[0],
                "file": None,
                "added": dt_util.utcnow().isoformat(),
            }
            image["file"] = f"{image['id']}{detected[1]}"
            await self.hass.async_add_executor_job(self._write_file, image["file"], content)
            now = dt_util.utcnow().isoformat()
            data.update(images=[*(data.get("images") or []), image], updated=now)
            data.setdefault("created", now)
            articles[key] = data
            self._articles = articles
            await self._async_save()
            return image

    async def async_delete_image(self, key: str, image_id: str) -> bool:
        """Remove a photo of an article, False if it has no such photo"""
        await self.async_load()
        async with self._lock:
            data = (self._articles or {}).get(key)
            image = next((entry for entry in (data or {}).get("images") or [] if entry["id"] == image_id), None)
            if data is None or image is None:
                return False
            data["images"] = [entry for entry in data["images"] if entry["id"] != image_id]
            data["updated"] = dt_util.utcnow().isoformat()
            if not data["images"]:
                del data["images"]
            await self._async_save()
        await self.hass.async_add_executor_job(self._remove_file, image)
        return True

    def image(self, image_id: str) -> tuple[Path, str] | None:
        """File and content type of a photo"""
        if not IMAGE_ID.match(image_id):
            return None
        for data in (self._articles or {}).values():
            for image in data.get("images") or []:
                if image["id"] == image_id:
                    return self.image_dir / image["file"], image["type"]
        return None

    def _write_file(self, name: str, content: bytes) -> None:
        self.image_dir.mkdir(parents=True, exist_ok=True)
        path = self.image_dir / name
        temp = path.with_suffix(path.suffix + ".tmp")
        with open(temp, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)

    def _remove_file(self, image: dict[str, Any]) -> None:
        try:
            (self.image_dir / image["file"]).unlink()
        except FileNotFoundError:
            pass
