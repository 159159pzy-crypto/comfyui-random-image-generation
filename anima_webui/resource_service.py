"""Inventory orchestration shared by HTTP diagnostics and queue admission."""
from __future__ import annotations

import copy
from typing import Any

from .comfy import ComfyError
from .resource_resolution import build_resource_catalog, resolve_resources


class ResourceService:
    def __init__(self, client: Any, usage_store: Any):
        self.client = client
        self.usage_store = usage_store

    async def inventory(self, *, refresh: bool = False, modules: list[str] | None = None) -> dict[str, Any]:
        inventory = copy.deepcopy(await self.client.resource_inventory())
        try:
            classified = await self.client.resource_classifications("models", refresh=refresh)
        except ComfyError as error:
            classified = {"items": [], "source": "unavailable", "manager_url": ""}
            inventory["classification_error"] = str(error)
        from .resource_classification import technical_resource_metadata
        inventory["model_items"] = [technical_resource_metadata(item) for item in classified["items"]]
        inventory["source"] = classified.get("source", "")
        inventory["manager_url"] = classified.get("manager_url", "")
        inspection = getattr(self.client, "resource_inspection", None)
        if inspection:
            try:
                inventory["resource_inspection"] = await inspection(refresh=refresh, **({"modules": modules} if modules else {}))
            except ComfyError as error:
                inventory["resource_inspection"] = {"supported": False, "version": 1, "items": [], "error": str(error)}
        inventory["resource_catalog"] = build_resource_catalog(inventory, classified["items"], self.usage_store)
        inventory["capabilities"] = inventory.get("resource_inspection", {}).get("capabilities", {})
        lora_files = inventory.get("resource_inspection", {}).get("lora_files")
        if isinstance(lora_files, list):
            inventory["lora_files"] = copy.deepcopy(lora_files)
        # Compatibility is an architectural fact, independent of which model
        # happens to be the recommended download in a profile.
        catalog = inventory["resource_catalog"]
        inventory["profile_models"] = {
            profile: [item["name"] for item in catalog if f"{profile}.model" in item.get("roles", [])]
            for profile in ("anima",)
        }
        # Extended clients use the identified roles rather than the historic
        # filename heuristic. Keep compact legacy inventories usable in embeds.
        if inspection:
            inventory["seedvr2_models"] = [item["name"] for item in catalog if "seedvr2.model" in item.get("roles", [])]
            inventory["seedvr2_vaes"] = [item["name"] for item in catalog if "seedvr2.vae" in item.get("roles", [])]
        return inventory

    @staticmethod
    def resolve(settings: dict[str, Any], inventory: dict[str, Any], *, strict: bool = False, allow_basename: bool | None = None) -> dict[str, Any]:
        return resolve_resources(settings, inventory, strict=strict, allow_basename=allow_basename)
