from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar


class NaturalDataError(ValueError):
    pass


class NaturalWorkspaceData:
    """Versioned WebUI metadata with fail-closed LoRA identity bindings."""

    KINDS: ClassVar[dict[str, str]] = {
        "lora_profiles": "lora_profiles_v3.json",
        "identities": "identity_bindings_v3.json",
        "prompt_lab": "prompt_lab.json",
    }

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, kind: str) -> Path:
        try:
            return self.data_dir / self.KINDS[kind]
        except KeyError as exc:
            raise NaturalDataError("unknown natural workspace data kind") from exc

    @staticmethod
    def _strings(value: Any, *, limit: int = 64) -> list[str]:
        if value is None:
            return []
        values = (value,) if isinstance(value, str) else value
        if isinstance(values, Mapping):
            raise NaturalDataError("expected text or a text array")
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = " ".join(str(raw or "").replace("\x00", " ").split())
            key = text.casefold()
            if text and key not in seen:
                result.append(text)
                seen.add(key)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _sha256(value: Any) -> str:
        text = re.sub(r"\s+", "", str(value or "")).casefold()
        return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""

    @classmethod
    def _activation_terms(cls, value: Any, *, limit: int) -> list[str]:
        raw_values = (value,) if isinstance(value, str) else (value or ())
        for raw in raw_values:
            text = str(raw or "")
            if "\x00" in text or "<lora:" in text.casefold():
                raise NaturalDataError(
                    "activation_terms cannot contain control data or LoRA syntax"
                )
        terms = cls._strings(raw_values, limit=limit)
        if any(len(term) > 300 for term in terms):
            raise NaturalDataError("activation_terms entries cannot exceed 300 characters")
        return terms

    @staticmethod
    def _tag_key(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
        return re.sub(r"[\s-]+", "_", text)

    @classmethod
    def _semantic_fingerprint(cls, profile: Mapping[str, Any]) -> str:
        payload = {
            "filename": str(profile.get("filename") or "").replace("\\", "/"),
            "activation_terms": cls._strings(profile.get("activation_terms")),
            "identity_tags": cls._strings(profile.get("identity_tags")),
            "style_tags": cls._strings(profile.get("style_tags")),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _read(self, kind: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        path = self._path(kind)
        if not path.is_file():
            return {}, []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NaturalDataError(f"unable to read {path.name}") from exc
        items = payload.get("items") if isinstance(payload, Mapping) else None
        return (
            dict(payload) if isinstance(payload, Mapping) else {},
            [dict(item) for item in items or [] if isinstance(item, Mapping)],
        )

    def _load(self, kind: str) -> list[dict[str, Any]]:
        document, records = self._read(kind)
        if kind not in {"lora_profiles", "identities"}:
            return records
        migrated = [
            self._normalize(kind, item, str(item.get("id") or ""), migrating=True)
            for item in records
        ]
        if migrated != records or document.get("schema_version") != 3:
            self._save(kind, migrated)
        return migrated

    def _save(self, kind: str, items: list[dict[str, Any]]) -> None:
        path = self._path(kind)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"schema_version": 3, "items": items},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._load(kind)

    def get(self, kind: str, item_id: str) -> dict[str, Any]:
        with self._lock:
            for item in self._load(kind):
                if str(item.get("id") or "") == str(item_id):
                    return dict(item)
        raise KeyError(item_id)

    def upsert(
        self, kind: str, payload: Mapping[str, Any], item_id: str = ""
    ) -> dict[str, Any]:
        with self._lock:
            items = self._load(kind)
            previous: dict[str, Any] | None = None
            normalized = self._normalize(kind, payload, item_id)
            for index, item in enumerate(items):
                if item.get("id") == normalized["id"]:
                    previous = item
                    items[index] = normalized
                    break
            else:
                items.append(normalized)
            self._save(kind, items)
            if kind == "lora_profiles" and previous is not None:
                reasons: list[str] = []
                if previous.get("semantic_fingerprint") != normalized.get(
                    "semantic_fingerprint"
                ):
                    reasons.append("semantic_fingerprint_changed")
                for field, reason in (
                    ("sha256", "lora_sha256_changed"),
                    ("source_fingerprint", "source_fingerprint_changed"),
                ):
                    before = str(previous.get(field) or "")
                    after = str(normalized.get(field) or "")
                    if before and after and before != after:
                        reasons.append(reason)
                if reasons:
                    self._mark_bindings_stale(normalized["id"], reasons)
            return normalized

    def delete(self, kind: str, item_id: str) -> None:
        with self._lock:
            items = self._load(kind)
            remaining = [item for item in items if item.get("id") != item_id]
            if len(remaining) == len(items):
                raise KeyError(item_id)
            self._save(kind, remaining)
            if kind == "lora_profiles":
                self._mark_bindings_stale(item_id, ("lora_profile_missing",))

    def confirm_prompt_lab(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            items = self._load("prompt_lab")
            for item in items:
                if item.get("id") == item_id:
                    item["status"] = "confirmed"
                    item["confirmed_at"] = time.time()
                    self._save("prompt_lab", items)
                    return item
        raise KeyError(item_id)

    @classmethod
    def _normalize(
        cls,
        kind: str,
        payload: Mapping[str, Any],
        item_id: str,
        *,
        migrating: bool = False,
        trust_verification: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        identifier = item_id or str(
            payload.get("id") or f"{kind[:4]}_{uuid.uuid4().hex[:12]}"
        )
        if kind == "lora_profiles":
            filename = str(payload.get("filename") or "").strip().replace("\\", "/")
            if not filename or filename.startswith("/") or ".." in filename.split("/"):
                raise NaturalDataError(
                    "LoRA filename must be an exact relative catalog path"
                )
            status = str(payload.get("file_status") or "unverified")
            if status not in {"current", "missing", "stale", "unverified"}:
                status = "unverified"
            record = {
                "id": identifier,
                "schema_version": 3,
                "filename": filename,
                "display_name": str(
                    payload.get("display_name") or filename
                ).strip()[:200],
                "activation_terms": cls._activation_terms(
                    payload.get("activation_terms", payload.get("trigger_words")),
                    limit=64,
                ),
                "identity_tags": cls._strings(payload.get("identity_tags"), limit=64),
                "style_tags": cls._strings(payload.get("style_tags"), limit=64),
                "preview_asset_ids": cls._strings(
                    payload.get("preview_asset_ids"), limit=12
                ),
                "sha256": cls._sha256(payload.get("sha256")),
                "source_fingerprint": cls._sha256(
                    payload.get("source_fingerprint")
                ),
                "file_status": status,
                "invalid_reasons": cls._strings(
                    payload.get("invalid_reasons"), limit=16
                ),
                "updated_at": float(payload.get("updated_at") or now)
                if migrating
                else now,
            }
            record["semantic_fingerprint"] = cls._semantic_fingerprint(record)
            return record
        if kind == "identities":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise NaturalDataError("identity binding name is required")
            profile_ids = cls._strings(
                payload.get("lora_profile_ids")
                or (
                    [payload.get("lora_profile_id")]
                    if payload.get("lora_profile_id")
                    else []
                ),
                limit=32,
            )
            character = str(
                payload.get("character_canonical")
                or payload.get("canonical_tag")
                or ""
            ).strip()[:300]
            status = str(payload.get("verification_status") or "review_needed")
            if status not in {"verified", "review_needed", "stale"}:
                status = "review_needed"
            if not trust_verification and not migrating:
                status = "review_needed"
            reasons = cls._strings(payload.get("invalid_reasons"), limit=16)
            if not profile_ids:
                reasons.append("lora_profile_required")
            if not character:
                reasons.append("character_canonical_required")
            if status != "verified" and not reasons:
                reasons.append("exact_verification_required")
            return {
                "id": identifier,
                "schema_version": 3,
                "name": name[:200],
                "character_canonical": character,
                "canonical_tag": character,
                "copyright_canonical": str(
                    payload.get("copyright_canonical") or ""
                ).strip()[:300],
                "activation_terms": cls._activation_terms(
                    payload.get("activation_terms"), limit=24
                ),
                "lora_profile_id": profile_ids[0] if len(profile_ids) == 1 else "",
                "lora_profile_ids": profile_ids,
                "aliases": cls._strings(payload.get("aliases"), limit=64),
                "source": str(payload.get("source") or "manual")
                if str(payload.get("source") or "manual")
                in {"manual", "observed"}
                else "manual",
                "verification_status": status,
                "verified_sha256": cls._sha256(payload.get("verified_sha256")),
                "verified_source_fingerprint": cls._sha256(
                    payload.get("verified_source_fingerprint")
                ),
                "verified_semantic_fingerprint": cls._sha256(
                    payload.get("verified_semantic_fingerprint")
                ),
                "verified_revision": str(
                    payload.get("verified_revision") or ""
                )[:128],
                "verified_at": str(payload.get("verified_at") or "")[:64],
                "invalid_reasons": list(dict.fromkeys(reasons)),
                "updated_at": float(payload.get("updated_at") or now)
                if migrating
                else now,
            }
        if kind == "prompt_lab":
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                raise NaturalDataError("Prompt Lab candidate cannot be empty")
            return {
                "id": identifier,
                "prompt": prompt[:12000],
                "negative_prompt": str(
                    payload.get("negative_prompt") or ""
                ).strip()[:12000],
                "status": str(payload.get("status") or "candidate")
                if migrating
                else "candidate",
                "source_plan_id": str(payload.get("source_plan_id") or "")[:100],
                "created_at": float(payload.get("created_at") or now),
            }
        raise NaturalDataError("unknown natural workspace data kind")

    @staticmethod
    def _lookup_field(lookup: Any, name: str, default: Any = "") -> Any:
        if isinstance(lookup, Mapping):
            return lookup.get(name, default)
        return getattr(lookup, name, default)

    @classmethod
    def _require_exact_lookup(
        cls, lookup: Any, requested: str, category: str
    ) -> str:
        canonical = str(cls._lookup_field(lookup, "canonical_tag") or "")
        verified = bool(cls._lookup_field(lookup, "verified", False))
        actual_category = str(
            cls._lookup_field(lookup, "category") or ""
        ).casefold()
        matched_by = str(
            cls._lookup_field(lookup, "matched_by")
            or cls._lookup_field(lookup, "match_type")
            or ""
        ).casefold()
        if (
            not verified
            or actual_category != category
            or matched_by != "canonical_exact"
        ):
            raise NaturalDataError(
                f"{category} canonical must be a Danbooru canonical exact match"
            )
        if cls._tag_key(canonical) != cls._tag_key(requested):
            raise NaturalDataError(
                f"{category} canonical must exactly equal the local Danbooru canonical"
            )
        return canonical

    def _mark_bindings_stale(self, profile_id: str, reasons: Any) -> int:
        reason_values = self._strings(reasons, limit=16)
        identities = self._load("identities")
        changed = 0
        for item in identities:
            if str(profile_id) not in {
                str(value) for value in item.get("lora_profile_ids") or []
            }:
                continue
            merged = list(
                dict.fromkeys((*item.get("invalid_reasons", ()), *reason_values))
            )
            if item.get("verification_status") != "stale" or merged != item.get(
                "invalid_reasons"
            ):
                item["verification_status"] = "stale"
                item["invalid_reasons"] = merged
                item["updated_at"] = time.time()
                changed += 1
        if changed:
            self._save("identities", identities)
        return changed

    def reconcile_lora_profile(
        self,
        profile_id: str,
        *,
        sha256: str,
        source_fingerprint: str,
        present: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            profiles = self._load("lora_profiles")
            for index, profile in enumerate(profiles):
                if str(profile.get("id") or "") != str(profile_id):
                    continue
                digest = self._sha256(sha256)
                source = self._sha256(source_fingerprint)
                reasons: list[str] = []
                if not present:
                    reasons.append("lora_file_missing")
                if profile.get("sha256") and digest and profile["sha256"] != digest:
                    reasons.append("lora_sha256_changed")
                if (
                    profile.get("source_fingerprint")
                    and source
                    and profile["source_fingerprint"] != source
                ):
                    reasons.append("source_fingerprint_changed")
                updated = {
                    **profile,
                    "sha256": digest if present else str(profile.get("sha256") or ""),
                    "source_fingerprint": source
                    if present
                    else str(profile.get("source_fingerprint") or ""),
                    "file_status": "current"
                    if present and digest and source
                    else ("unverified" if present else "missing"),
                    "invalid_reasons": reasons,
                    "updated_at": time.time(),
                }
                profiles[index] = updated
                self._save("lora_profiles", profiles)
                if reasons:
                    self._mark_bindings_stale(profile_id, reasons)
                return dict(updated)
        raise KeyError(profile_id)

    def upsert_verified_identity(
        self,
        payload: Mapping[str, Any],
        item_id: str = "",
        *,
        character_lookup: Any,
        copyright_lookup: Any | None,
        lora_detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            profile_ids = self._strings(
                payload.get("lora_profile_ids")
                or (
                    [payload.get("lora_profile_id")]
                    if payload.get("lora_profile_id")
                    else []
                ),
                limit=2,
            )
            if len(profile_ids) != 1:
                raise NaturalDataError(
                    "a verified identity binding must target exactly one LoRA profile"
                )
            profile = self.get("lora_profiles", profile_ids[0])
            detail_filename = str(
                lora_detail.get("filename") or lora_detail.get("name") or ""
            ).replace("\\", "/")
            if detail_filename.casefold() != str(profile["filename"]).casefold():
                raise NaturalDataError("LoRA detail does not match the selected profile")
            digest = self._sha256(lora_detail.get("sha256"))
            source_fingerprint = self._sha256(
                lora_detail.get("source_fingerprint")
            )
            if not digest or not source_fingerprint:
                raise NaturalDataError(
                    "verified identity binding requires the current LoRA SHA-256 and semantic source fingerprint"
                )
            profile = self.reconcile_lora_profile(
                profile["id"],
                sha256=digest,
                source_fingerprint=source_fingerprint,
                present=True,
            )
            character_requested = str(
                payload.get("character_canonical")
                or payload.get("canonical_tag")
                or ""
            ).strip()
            character = self._require_exact_lookup(
                character_lookup, character_requested, "character"
            )
            copyright_requested = str(
                payload.get("copyright_canonical") or ""
            ).strip()
            copyright = ""
            if copyright_requested:
                if copyright_lookup is None:
                    raise NaturalDataError("copyright exact lookup is required")
                copyright = self._require_exact_lookup(
                    copyright_lookup, copyright_requested, "copyright"
                )
            qualified = re.search(r"_\(([^()]*)\)$", self._tag_key(character))
            if (
                qualified
                and copyright
                and self._tag_key(qualified.group(1)) != self._tag_key(copyright)
            ):
                raise NaturalDataError(
                    "character and copyright canonicals do not describe the same Danbooru identity"
                )
            trusted = {
                **dict(payload),
                "character_canonical": character,
                "canonical_tag": character,
                "copyright_canonical": copyright,
                "lora_profile_id": profile["id"],
                "lora_profile_ids": [profile["id"]],
                "verification_status": "verified",
                "verified_sha256": profile["sha256"],
                "verified_source_fingerprint": profile["source_fingerprint"],
                "verified_semantic_fingerprint": profile["semantic_fingerprint"],
                "verified_revision": f"sha256:{profile['sha256']}",
                "verified_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "invalid_reasons": [],
            }
            normalized = self._normalize(
                "identities",
                trusted,
                item_id,
                trust_verification=True,
            )
            identities = self._load("identities")
            prospective = [
                item for item in identities if item.get("id") != normalized["id"]
            ] + [normalized]
            bound = [
                item
                for item in prospective
                if profile["id"] in item.get("lora_profile_ids", ())
                and item.get("verification_status") == "verified"
            ]
            canonical_keys = [
                self._tag_key(item.get("character_canonical")) for item in bound
            ]
            if len(canonical_keys) != len(set(canonical_keys)):
                raise NaturalDataError(
                    "one LoRA profile cannot bind the same character more than once"
                )
            if len(bound) > 1:
                activation_sets: list[set[str]] = []
                for item in bound:
                    terms = {
                        self._tag_key(term)
                        for term in item.get("activation_terms") or []
                        if self._tag_key(term)
                    }
                    if not terms:
                        raise NaturalDataError(
                            "every character in a multi-character LoRA requires dedicated activation_terms"
                        )
                    if any(terms & current for current in activation_sets):
                        raise NaturalDataError(
                            "multi-character LoRA activation_terms must be exclusive per character"
                        )
                    activation_sets.append(terms)
            replaced = False
            for index, item in enumerate(identities):
                if item.get("id") == normalized["id"]:
                    identities[index] = normalized
                    replaced = True
                    break
            if not replaced:
                identities.append(normalized)
            self._save("identities", identities)
            return dict(normalized)

    def active_identity_bindings(self, profile_id: str) -> list[dict[str, Any]]:
        with self._lock:
            profile = self.get("lora_profiles", profile_id)
            result: list[dict[str, Any]] = []
            for item in self._load("identities"):
                if profile_id not in item.get("lora_profile_ids", ()):
                    continue
                if item.get("verification_status") != "verified":
                    continue
                if (
                    item.get("verified_sha256") != profile.get("sha256")
                    or item.get("verified_source_fingerprint")
                    != profile.get("source_fingerprint")
                    or item.get("verified_semantic_fingerprint")
                    != profile.get("semantic_fingerprint")
                    or profile.get("file_status") != "current"
                ):
                    continue
                result.append(dict(item))
            return result

    def redacted_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        path = self.data_dir / "task_events.jsonl"
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-max(1, min(500, limit)) :]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, Mapping):
                rows.append(
                    {
                        "job_id": str(item.get("job_id") or ""),
                        "stage": str(item.get("stage") or ""),
                        "message": str(item.get("message") or "")[:500],
                        "timestamp": item.get("timestamp"),
                        "details": dict(item.get("details") or {}),
                    }
                )
        return rows
