"""JSON CLI for campaign and complete snapshot management."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from nanobot.dnd.db.campaigns import CampaignService
from nanobot.dnd.db.characters import CharacterService
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.events import CampaignEventService
from nanobot.dnd.db.models import (
    Campaign,
    EmbeddingModel,
    ModuleChunk,
    ModuleSource,
    RuleChunk,
    RulePublication,
    RuleSection,
    RuleSet,
    RuleSource,
)
from nanobot.dnd.db.module_content import ModuleImportService
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.db.undo import UndoManager
from nanobot.dnd.db.user_context import read_player_roles, write_player_roles
from nanobot.dnd.modules.search import ModuleSearchService
from nanobot.dnd.rules.ingest import RuleIngestService
from nanobot.dnd.rules.search import RuleSearchService
from nanobot.dnd.vector.client import VectorStore


def _emit(value: Any) -> None:
    # ASCII-safe JSON keeps the CLI reliable under the legacy Windows console
    # code pages commonly used to launch NanoBot. JSON consumers recover Unicode.
    print(json.dumps(value, ensure_ascii=True, indent=2, default=str))


def _sync_user_to_database(database: Database, campaign_id: str, workspace: str) -> None:
    roles = read_player_roles(workspace, campaign_id)
    with database.transaction() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        config = dict(campaign.config or {})
        config["user_md_player_roles"] = roles
        campaign.config = config


def _sync_database_to_user(database: Database, campaign_id: str, workspace: str) -> Path:
    with database.transaction() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        roles = str((campaign.config or {}).get("user_md_player_roles", ""))
    return write_player_roles(workspace, campaign_id, roles)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nanobot.dnd.db.cli")
    parser.add_argument("--database-url", help="Override DND_DATABASE_URL")
    commands = parser.add_subparsers(dest="area", required=True)

    campaign = commands.add_parser("campaign")
    campaign_commands = campaign.add_subparsers(dest="action", required=True)
    create = campaign_commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--id")
    create.add_argument("--module")
    create.add_argument("--description")
    create.add_argument("--rule-set", dest="rule_set_id")
    create.add_argument("--publication", action="append", dest="publication_ids")
    create.add_argument("--locale")
    listing = campaign_commands.add_parser("list")
    listing.add_argument("--status", choices=("active", "archived"))
    show = campaign_commands.add_parser("show")
    show.add_argument("--campaign", required=True)
    status = campaign_commands.add_parser("status")
    status.add_argument("--campaign", required=True)
    status.add_argument("--set", required=True, choices=("active", "archived"))
    delete_cmd = campaign_commands.add_parser("delete", help="Delete a campaign and all its data")
    delete_cmd.add_argument("--campaign", required=True)

    character = commands.add_parser("character")
    character_commands = character.add_subparsers(dest="action", required=True)
    character_create = character_commands.add_parser("create")
    character_create.add_argument("--name", required=True)
    character_create.add_argument("--id")
    character_create.add_argument("--type", dest="character_type", choices=("pc", "npc"))
    character_create.add_argument("--campaign")
    character_create.add_argument("--player")
    character_create.add_argument("--class", dest="class_name")
    character_create.add_argument("--level", type=int)
    character_create.add_argument("--hp", type=int)
    character_create.add_argument("--max-hp", type=int)
    character_create.add_argument("--ac", type=int)
    sheet = character_create.add_mutually_exclusive_group()
    sheet.add_argument("--sheet-json")
    sheet.add_argument("--sheet-file")
    character_create.add_argument("--actor")
    # Lore fields
    character_create.add_argument("--race")
    character_create.add_argument("--background")
    character_create.add_argument("--alignment")
    character_create.add_argument("--personality", dest="personality_traits")
    character_create.add_argument("--ideals")
    character_create.add_argument("--bonds")
    character_create.add_argument("--flaws")
    character_create.add_argument("--appearance")
    character_create.add_argument("--backstory")
    character_create.add_argument("--goals")
    character_create.add_argument("--notes")
    character_create.add_argument("--portrait", dest="portrait_url")
    character_list = character_commands.add_parser("list")
    character_list.add_argument("--campaign")
    character_list.add_argument("--type", dest="character_type", choices=("pc", "npc"))
    character_show = character_commands.add_parser("show")
    character_show.add_argument("--character", required=True)
    character_bind = character_commands.add_parser("bind")
    character_bind.add_argument("--character", required=True)
    character_bind.add_argument("--campaign", required=True)
    character_unbind = character_commands.add_parser("unbind")
    character_unbind.add_argument("--character", required=True)

    event = commands.add_parser("event")
    event_commands = event.add_subparsers(dest="action", required=True)
    event_create = event_commands.add_parser("create")
    event_create.add_argument("--campaign", required=True)
    event_create.add_argument("--type", dest="event_type", required=True)
    event_create.add_argument("--content", required=True)
    event_create.add_argument("--actor-name", action="append", default=[])
    event_create.add_argument("--visibility", default="party")
    event_create.add_argument("--importance", type=int, default=3)
    event_create.add_argument("--metadata-json")
    event_create.add_argument("--session")
    event_create.add_argument("--actor")
    event_list = event_commands.add_parser("list")
    event_list.add_argument("--campaign", required=True)
    event_list.add_argument("--limit", type=int, default=50)

    module = commands.add_parser("module")
    module_commands = module.add_subparsers(dest="action", required=True)
    module_import = module_commands.add_parser("import")
    module_import.add_argument("--campaign", required=True)
    module_import.add_argument("--path", required=True)
    module_import.add_argument("--name")
    module_import.add_argument("--inactive", action="store_true")
    module_import.add_argument("--no-embed", action="store_true")
    module_import.add_argument("--actor")
    module_list = module_commands.add_parser("list")
    module_list.add_argument("--campaign", required=True)
    module_index = module_commands.add_parser("index")
    module_index.add_argument("--campaign", required=True)
    module_scene = module_commands.add_parser("scene")
    module_scene.add_argument("--campaign", required=True)
    module_scene.add_argument("--scene", required=True)
    module_search = module_commands.add_parser("search")
    module_search.add_argument("--campaign", required=True)
    module_search.add_argument("--query", required=True)
    module_search.add_argument("--top-k", type=int, default=5)
    module_search.add_argument("--no-dense", action="store_true")
    module_export = module_commands.add_parser("export-scenes")
    module_export.add_argument("--campaign", required=True)
    module_export.add_argument("--output", required=False, help="Optional JSON file path")
    module_delete_cmd = module_commands.add_parser("delete", help="Delete a module and its data")
    module_delete_cmd.add_argument("--campaign", required=True)
    module_delete_cmd.add_argument("--module", required=True, help="Module ID")
    module_deact = module_commands.add_parser("deactivate", help="Deactivate a module")
    module_deact.add_argument("--campaign", required=True)
    module_deact.add_argument("--module", required=True)
    module_act = module_commands.add_parser("activate", help="Activate a module")
    module_act.add_argument("--campaign", required=True)
    module_act.add_argument("--module", required=True)

    save = commands.add_parser("save")
    save_commands = save.add_subparsers(dest="action", required=True)
    save_create = save_commands.add_parser("create")
    save_create.add_argument("--campaign", required=True)
    save_create.add_argument("--label", default="")
    save_create.add_argument("--actor")
    save_create.add_argument("--workspace")
    save_list = save_commands.add_parser("list")
    save_list.add_argument("--campaign", required=True)
    save_verify = save_commands.add_parser("verify")
    save_verify.add_argument("--campaign", required=True)
    save_verify.add_argument("--slot", required=True, type=int)
    save_load = save_commands.add_parser("load")
    save_load.add_argument("--campaign", required=True)
    save_load.add_argument("--slot", required=True, type=int)
    save_load.add_argument("--actor")
    save_load.add_argument("--session")
    save_load.add_argument("--request")
    save_load.add_argument("--workspace")
    save_delete_cmd = save_commands.add_parser("delete", help="Delete a snapshot slot")
    save_delete_cmd.add_argument("--campaign", required=True)
    save_delete_cmd.add_argument("--slot", required=True, type=int)
    save_export_cmd = save_commands.add_parser("export", help="Export a snapshot to JSON")
    save_export_cmd.add_argument("--campaign", required=True)
    save_export_cmd.add_argument("--slot", required=True, type=int)
    save_export_cmd.add_argument("--output", required=True, help="Output JSON file path")

    undo = commands.add_parser("undo")
    undo.add_argument("--campaign", required=True)
    undo.add_argument("--count", type=int, default=1)
    undo.add_argument("--actor")
    undo.add_argument("--workspace")

    rules = commands.add_parser("rules")
    rule_commands = rules.add_subparsers(dest="action", required=True)
    ingest = rule_commands.add_parser("ingest-srd")
    ingest.add_argument("--references-dir")
    ingest.add_argument("--no-embed", action="store_true")
    ingest.add_argument("--force", action="store_true")
    ingest_zh = rule_commands.add_parser("ingest-zh-cn")
    ingest_zh.add_argument("--references-dir", required=True)
    ingest_zh.add_argument("--no-embed", action="store_true")
    ingest_zh.add_argument("--force", action="store_true")
    bind = rule_commands.add_parser("bind")
    bind.add_argument("--campaign", required=True)
    bind.add_argument("--rule-set", default="dnd5e-2024-srd-5.2.1")
    bind.add_argument("--publication", action="append")
    search = rule_commands.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--campaign")
    search.add_argument("--rule-set")
    search.add_argument("--publication", action="append")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--no-dense", action="store_true")
    search.add_argument(
        "--expand",
        choices=("chunk", "paragraph", "section", "section-with-children"),
    )
    expand = rule_commands.add_parser("expand")
    expand.add_argument("--chunk", required=True)
    expand.add_argument(
        "--mode",
        choices=("chunk", "paragraph", "section", "section-with-children"),
        default="section",
    )
    rule_commands.add_parser("tree")
    rule_commands.add_parser("status")

    vector = commands.add_parser("vector")
    vector_commands = vector.add_subparsers(dest="action", required=True)
    vector_migrate = vector_commands.add_parser("migrate")
    vector_migrate.add_argument(
        "--batch-size", type=int, default=100, help="Rows per ChromaDB upsert batch"
    )
    vector_commands.add_parser("status")
    vector_verify = vector_commands.add_parser("verify")
    vector_verify.add_argument(
        "--count", type=int, default=5, help="Number of spot-checks per collection"
    )
    vector_commands.add_parser("reindex")

    return parser


def _batched(iterable, size: int):
    """Yield successive batches from *iterable*."""
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _vector_status(store: VectorStore) -> dict:
    from nanobot.dnd.rules.embedding import (
        EMBEDDING_PROFILES,
        collection_name,
        configured_profiles,
        cuda_available,
        embedding_mode,
    )

    profiles = configured_profiles()
    has_cuda = cuda_available()
    return {
        "enabled": store.enabled,
        "url": store.configured_url(),
        "path": str(store.configured_path()) if store.configured_path() else None,
        "embedding": {
            "device_mode": embedding_mode(),
            "cuda_available": has_cuda,
            "selected_profiles": [profile.key for profile in profiles],
            "available_profiles": [
                {
                    "key": profile.key,
                    "model": profile.model_name,
                    "dimensions": profile.dimensions,
                    "language": profile.language,
                }
                for profile in EMBEDDING_PROFILES.values()
            ],
            "first_use_recommendation": (
                ["bge_m3"]
                if has_cuda
                else ["bge_small_zh_v1_5", "bge_small_en_v1_5"]
            ),
            "note": (
                "Model choices are optional and are not forced by the detected hardware. "
                "Changing profiles requires rebuilding dense indexes."
            ),
        },
        "collections": [
            store.collection_stats(collection_name(base, profile))
            for base in ("dnd_rules", "dnd_modules", "dnd_campaign_memories")
            for profile in profiles
        ],
    }


def _vector_migrate(database: Database, store: VectorStore, *, batch_size: int = 100) -> dict:
    """Copy existing embedding_json vectors into ChromaDB."""
    total_rules = 0
    total_modules = 0
    with database.transaction() as session:
        # Migrate rule chunks
        rule_rows = list(
            session.execute(
                select(RuleChunk.id, RuleChunk.embedding_json, RuleSource.rule_set_id,
                       RuleSource.publication_id, RuleChunk.source_id,
                       RuleChunk.section_id, RuleChunk.chunk_index,
                       RuleChunk.content_hash)
                .join(RuleSource, RuleSource.id == RuleChunk.source_id)
                .where(RuleChunk.embedding_json.is_not(None))
            )
        )
        for batch in _batched(rule_rows, batch_size):
            coll = store.collection("dnd_rules")
            ids = [str(row[0]) for row in batch]
            embeddings = [row[1] for row in batch]
            metadatas = [
                {
                    "chunk_id": str(row[0]),
                    "rule_set_id": str(row[2] or ""),
                    "publication_id": str(row[3] or ""),
                    "source_id": str(row[4] or ""),
                    "section_id": str(row[5] or ""),
                    "chunk_index": int(row[6] or 0),
                    "chunk_type": "section",
                    "content_hash": str(row[7] or ""),
                    "version": 1,
                }
                for row in batch
            ]
            coll.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)
            total_rules += len(batch)

        # Migrate module chunks
        module_rows = list(
            session.execute(
                select(ModuleChunk.id, ModuleChunk.embedding_json,
                       ModuleChunk.module_id, ModuleChunk.chapter_id,
                       ModuleChunk.scene_id, ModuleChunk.chunk_index,
                       ModuleChunk.chunk_type, ModuleChunk.content_hash,
                       ModuleSource.campaign_id)
                .join(ModuleSource, ModuleSource.id == ModuleChunk.module_id)
                .where(ModuleChunk.embedding_json.is_not(None))
            )
        )
        for batch in _batched(module_rows, batch_size):
            coll = store.collection("dnd_modules")
            ids = [str(row[0]) for row in batch]
            embeddings = [row[1] for row in batch]
            metadatas = [
                {
                    "chunk_id": str(row[0]),
                    "campaign_id": str(row[8] or ""),
                    "module_id": str(row[2] or ""),
                    "chapter_id": str(row[3] or ""),
                    "scene_id": str(row[4] or ""),
                    "chunk_index": int(row[5] or 0),
                    "chunk_type": str(row[6] or "narrative"),
                    "content_hash": str(row[7] or ""),
                    "version": 1,
                }
                for row in batch
            ]
            coll.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)
            total_modules += len(batch)

    return {"rules_migrated": total_rules, "modules_migrated": total_modules}


def _vector_verify(database: Database, store: VectorStore, *, sample_count: int = 5) -> dict:
    """Spot-check ChromaDB matches a random sample of SQL embeddings."""
    import random as _random

    results = {}
    for coll_name, model, source_join, scope_filter in (
        ("dnd_rules", RuleChunk, RuleSource, RuleSource.rule_set_id),
        ("dnd_modules", ModuleChunk, ModuleSource, ModuleSource.campaign_id),
    ):
        with database.transaction() as session:
            rows = list(
                session.execute(
                    select(model.id, model.embedding_json)
                    .join(source_join, source_join.id == model.source_id)
                    .where(model.embedding_json.is_not(None))
                )
            )
        if not rows:
            results[coll_name] = {"sampled": 0, "note": "no embedded rows in SQL"}
            continue
        sample = _random.sample(rows, min(sample_count, len(rows)))
        coll = store.collection(coll_name)
        checks = []
        for row in sample:
            chunk_id = str(row[0])
            chroma_result = coll.get(ids=[chunk_id], include=["embeddings"])
            found = bool(chroma_result and chroma_result.get("ids"))
            checks.append(
                {
                    "chunk_id": chunk_id,
                    "in_chromadb": found,
                }
            )
        results[coll_name] = {
            "sampled": len(sample),
            "total_in_sql": len(rows),
            "checks": checks,
        }
    return results


def _vector_reindex_legacy(database: Database, store: VectorStore) -> dict:
    """Drop and rebuild ChromaDB collections from SQL chunk content.

    This requires the embedding model (BGE-M3) to be available so that
    vectors can be regenerated from the chunk text stored in SQL.
    """
    from nanobot.dnd.rules.embedding import BgeM3Embedder

    embedder = BgeM3Embedder(show_progress=True)
    results: dict[str, int] = {}

    # ── Rules ───────────────────────────────────────────────────────
    store.drop_collection("dnd_rules")
    coll = store.collection("dnd_rules")
    total_rules = 0
    with database.transaction() as session:
        rows = list(
            session.execute(
                select(
                    RuleChunk.id, RuleChunk.chunk_text,
                    RuleSource.rule_set_id, RuleSource.publication_id,
                    RuleChunk.source_id, RuleChunk.section_id,
                    RuleChunk.chunk_index, RuleChunk.content_hash,
                    RuleSet, RulePublication,
                )
                .join(RuleSource, RuleSource.id == RuleChunk.source_id)
                .join(RuleSet, RuleSet.id == RuleSource.rule_set_id)
                .join(RulePublication, RulePublication.id == RuleSource.publication_id)
                .order_by(RuleChunk.source_id, RuleChunk.chunk_index)
            )
        )
    for batch in _batched(rows, 50):
        texts = []
        ids = []
        metadatas = []
        for row in batch:
            chunk_id = str(row[0])
            chunk_text = str(row[1] or "")
            rule_set = row[8]
            publication = row[9]
            breadcrumb = ""  # We don't have heading_path here; use minimal context
            embedding_text = (
                f"{rule_set.game_system} | {rule_set.edition} | {rule_set.release}\n"
                f"{publication.name} | {breadcrumb}\n{chunk_text}"
            )
            texts.append(embedding_text)
            ids.append(chunk_id)
            metadatas.append(
                {
                    "chunk_id": chunk_id,
                    "rule_set_id": str(row[2] or ""),
                    "publication_id": str(row[3] or ""),
                    "source_id": str(row[4] or ""),
                    "section_id": str(row[5] or ""),
                    "chunk_index": int(row[6] or 0),
                    "chunk_type": "section",
                    "content_hash": str(row[7] or ""),
                    "version": 1,
                }
            )
        vectors = embedder.encode(texts)
        coll.upsert(ids=ids, embeddings=vectors, metadatas=metadatas)
        total_rules += len(batch)
    results["dnd_rules"] = total_rules

    # ── Modules ─────────────────────────────────────────────────────
    store.drop_collection("dnd_modules")
    coll = store.collection("dnd_modules")
    total_modules = 0
    with database.transaction() as session:
        rows = list(
            session.execute(
                select(
                    ModuleChunk.id, ModuleChunk.chunk_text,
                    ModuleChunk.module_id, ModuleChunk.chapter_id,
                    ModuleChunk.scene_id, ModuleChunk.chunk_index,
                    ModuleChunk.chunk_type, ModuleChunk.content_hash,
                    ModuleSource.campaign_id, ModuleSource.name,
                )
                .join(ModuleSource, ModuleSource.id == ModuleChunk.module_id)
                .order_by(ModuleChunk.module_id, ModuleChunk.chunk_index)
            )
        )
    for batch in _batched(rows, 50):
        texts = []
        ids = []
        metadatas = []
        for row in batch:
            chunk_id = str(row[0])
            chunk_text = str(row[1] or "")
            module_name = str(row[9] or "")
            embedding_text = f"{module_name} | | \n{chunk_text}"
            texts.append(embedding_text)
            ids.append(chunk_id)
            metadatas.append(
                {
                    "chunk_id": chunk_id,
                    "campaign_id": str(row[8] or ""),
                    "module_id": str(row[2] or ""),
                    "chapter_id": str(row[3] or ""),
                    "scene_id": str(row[4] or ""),
                    "chunk_index": int(row[5] or 0),
                    "chunk_type": str(row[6] or "narrative"),
                    "content_hash": str(row[7] or ""),
                    "version": 1,
                }
            )
        vectors = embedder.encode(texts)
        coll.upsert(ids=ids, embeddings=vectors, metadatas=metadatas)
        total_modules += len(batch)
    results["dnd_modules"] = total_modules

    return results


def _vector_reindex(database: Database, store: VectorStore) -> dict:
    """Rebuild model-isolated Chroma indexes using the selected profiles."""
    from nanobot.dnd.rules.embedding import (
        BgeM3Embedder,
        collection_name,
        configured_profiles,
        detect_text_language,
        profile_for_language,
    )
    from nanobot.dnd.rules.ingest import _ensure_embedding_model

    for base_name in ("dnd_rules", "dnd_modules"):
        for profile in configured_profiles():
            store.drop_collection(collection_name(base_name, profile))

    results: dict[str, int] = {"dnd_rules": 0, "dnd_modules": 0}
    with database.transaction() as session:
        rule_rows = list(
            session.execute(
                select(RuleChunk, RuleSource, RuleSet, RulePublication)
                .join(RuleSource, RuleSource.id == RuleChunk.source_id)
                .join(RuleSet, RuleSet.id == RuleSource.rule_set_id)
                .join(RulePublication, RulePublication.id == RuleSource.publication_id)
                .order_by(RuleChunk.source_id, RuleChunk.chunk_index)
            )
        )
        grouped_rules: dict[Any, list[Any]] = {}
        for row in rule_rows:
            grouped_rules.setdefault(profile_for_language(row[2].locale), []).append(row)
        for profile, rows in grouped_rules.items():
            embedder = BgeM3Embedder(profile=profile, show_progress=True)
            model_row = _ensure_embedding_model(session, embedder)
            collection = store.collection_for("dnd_rules", profile)
            for batch in _batched(rows, 50):
                texts = [
                    (
                        f"{rule_set.game_system} | {rule_set.edition} | {rule_set.release}\n"
                        f"{publication.name} | {chunk.breadcrumb}\n{chunk.chunk_text}"
                    )
                    for chunk, _source, rule_set, publication in batch
                ]
                vectors = embedder.encode(texts)
                for (chunk, _source, _rule_set, _publication), vector in zip(
                    batch, vectors, strict=True
                ):
                    chunk.embedding_model_id = model_row.id
                    chunk.embedding_json = vector
                collection.upsert(
                    ids=[chunk.id for chunk, *_rest in batch],
                    embeddings=vectors,
                    metadatas=[
                        {
                            "chunk_id": chunk.id,
                            "rule_set_id": source.rule_set_id or "",
                            "publication_id": source.publication_id or "",
                            "source_id": source.id,
                            "section_id": chunk.section_id or "",
                            "chunk_index": chunk.chunk_index,
                            "chunk_type": "section",
                            "content_hash": chunk.content_hash,
                            "version": 1,
                        }
                        for chunk, source, *_rest in batch
                    ],
                )
                results["dnd_rules"] += len(batch)

        module_rows = list(
            session.execute(
                select(ModuleChunk, ModuleSource)
                .join(ModuleSource, ModuleSource.id == ModuleChunk.module_id)
                .order_by(ModuleChunk.module_id, ModuleChunk.chunk_index)
            )
        )
        module_texts: dict[str, str] = {}
        for chunk, source in module_rows:
            module_texts[source.id] = module_texts.get(source.id, "") + f"\n{chunk.chunk_text}"
        grouped_modules: dict[Any, list[Any]] = {}
        for row in module_rows:
            language = detect_text_language(module_texts[row[1].id])
            grouped_modules.setdefault(profile_for_language(language), []).append(row)
        for profile, rows in grouped_modules.items():
            embedder = BgeM3Embedder(profile=profile, show_progress=True)
            model_row = _ensure_embedding_model(session, embedder)
            collection = store.collection_for("dnd_modules", profile)
            for batch in _batched(rows, 50):
                texts = [
                    f"{source.name} | {chunk.breadcrumb}\n{chunk.chunk_text}"
                    for chunk, source in batch
                ]
                vectors = embedder.encode(texts)
                for (chunk, _source), vector in zip(batch, vectors, strict=True):
                    chunk.embedding_model_id = model_row.id
                    chunk.embedding_json = vector
                collection.upsert(
                    ids=[chunk.id for chunk, _source in batch],
                    embeddings=vectors,
                    metadatas=[
                        {
                            "chunk_id": chunk.id,
                            "campaign_id": source.campaign_id,
                            "module_id": chunk.module_id,
                            "chapter_id": chunk.chapter_id,
                            "scene_id": chunk.scene_id or "",
                            "chunk_index": chunk.chunk_index,
                            "chunk_type": chunk.chunk_type,
                            "content_hash": chunk.content_hash,
                            "version": 1,
                        }
                        for chunk, source in batch
                    ],
                )
                results["dnd_modules"] += len(batch)
        session.flush()
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = Database(args.database_url)
    try:
        database.upgrade_schema()
        campaigns = CampaignService(database)
        characters = CharacterService(database)
        events = CampaignEventService(database)
        modules = ModuleImportService(database)
        snapshots = CampaignSnapshotService(database)
        if args.area == "campaign":
            if args.action == "create":
                result = campaigns.create(
                    args.name,
                    campaign_id=args.id,
                    module_name=args.module,
                    description=args.description,
                    rule_set_id=getattr(args, "rule_set_id", None),
                    publication_ids=getattr(args, "publication_ids", None),
                    locale=getattr(args, "locale", None),
                )
            elif args.action == "list":
                result = campaigns.list(status=args.status)
            elif args.action == "show":
                result = campaigns.get(args.campaign)
            elif args.action == "delete":
                campaigns.delete(args.campaign)
                _emit({"deleted": args.campaign})
            else:
                result = campaigns.set_status(args.campaign, args.set)
            if args.action != "delete":
                _emit(asdict(result) if not isinstance(result, list) else [asdict(x) for x in result])
        elif args.area == "character":
            if args.action == "create":
                if args.sheet_file:
                    sheet_json = json.loads(Path(args.sheet_file).read_text(encoding="utf-8"))
                elif args.sheet_json:
                    sheet_json = json.loads(args.sheet_json)
                else:
                    sheet_json = {}
                result = characters.create(
                    args.name,
                    character_id=args.id,
                    character_type=getattr(args, "character_type", None) or "pc",
                    campaign_id=args.campaign,
                    player_name=args.player,
                    class_name=args.class_name,
                    level=args.level,
                    hp=args.hp,
                    max_hp=args.max_hp,
                    armor_class=args.ac,
                    sheet_json=sheet_json,
                    actor_id=args.actor,
                    race=getattr(args, "race", None),
                    background=getattr(args, "background", None),
                    alignment=getattr(args, "alignment", None),
                    personality_traits=getattr(args, "personality_traits", None),
                    ideals=getattr(args, "ideals", None),
                    bonds=getattr(args, "bonds", None),
                    flaws=getattr(args, "flaws", None),
                    appearance=getattr(args, "appearance", None),
                    backstory=getattr(args, "backstory", None),
                    goals=getattr(args, "goals", None),
                    notes=getattr(args, "notes", None),
                    portrait_url=getattr(args, "portrait_url", None),
                )
                _emit(asdict(result))
            elif args.action == "list":
                _emit([
                    asdict(item)
                    for item in characters.list(
                        campaign_id=args.campaign,
                        character_type=getattr(args, "character_type", None),
                    )
                ])
            elif args.action == "show":
                _emit(asdict(characters.get(args.character)))
            elif args.action == "bind":
                _emit(asdict(characters.bind_to_campaign(args.character, args.campaign)))
            else:
                _emit(asdict(characters.unbind_from_campaign(args.character)))
        elif args.area == "event":
            if args.action == "create":
                result = events.create(
                    args.campaign,
                    args.event_type,
                    args.content,
                    actors=args.actor_name,
                    visibility=args.visibility,
                    importance=args.importance,
                    metadata_json=(
                        json.loads(args.metadata_json) if args.metadata_json else {}
                    ),
                    session_id=args.session,
                    actor_id=args.actor,
                )
                _emit(asdict(result))
            else:
                _emit(
                    [
                        asdict(item)
                        for item in events.list(args.campaign, limit=args.limit)
                    ]
                )
        elif args.area == "module":
            if args.action == "import":
                result = modules.import_path(
                    args.campaign,
                    args.path,
                    name=args.name,
                    activate=not args.inactive,
                    embed=not args.no_embed,
                    actor_id=args.actor,
                )
                _emit(asdict(result))
            elif args.action == "list":
                _emit([asdict(item) for item in modules.list(args.campaign)])
            elif args.action == "index":
                _emit(modules.index(args.campaign))
            elif args.action == "scene":
                _emit(asdict(modules.read_scene(args.campaign, args.scene)))
            elif args.action == "export-scenes":
                _emit(modules.export_scene_index(args.campaign, output_path=args.output))
            elif args.action == "delete":
                modules.delete(args.campaign, args.module)
                _emit({"deleted": args.module})
            elif args.action == "deactivate":
                _emit(asdict(modules.set_active(args.campaign, args.module, active=False)))
            elif args.action == "activate":
                _emit(asdict(modules.set_active(args.campaign, args.module, active=True)))
            else:
                search = ModuleSearchService(database)
                _emit(
                    {
                        "query": args.query,
                        "hits": [
                            asdict(hit)
                            for hit in search.search(
                                args.query,
                                campaign_id=args.campaign,
                                top_k=args.top_k,
                                dense=not args.no_dense,
                            )
                        ],
                    }
                )
        elif args.area == "save":
            if args.action == "create":
                if args.workspace:
                    _sync_user_to_database(database, args.campaign, args.workspace)
                _emit(
                    asdict(
                        snapshots.create(
                            args.campaign, label=args.label, actor_id=args.actor
                        )
                    )
                )
            elif args.action == "list":
                _emit([asdict(item) for item in snapshots.list(args.campaign)])
            elif args.action == "verify":
                payload = snapshots.get(args.campaign, args.slot)
                _emit(
                    {
                        "valid": True,
                        "campaign_id": payload["campaign_id"],
                        "schema_version": payload["schema_version"],
                        "captured_at": payload["captured_at"],
                    }
                )
            elif args.action == "delete":
                snapshots.delete(args.campaign, args.slot)
                _emit({"deleted": True, "campaign_id": args.campaign, "slot": args.slot})
            elif args.action == "export":
                payload = snapshots.export(args.campaign, args.slot, args.output)
                _emit(
                    {
                        "exported": True,
                        "campaign_id": payload["campaign_id"],
                        "slot": args.slot,
                        "output": args.output,
                    }
                )
            else:
                result = snapshots.restore(
                    args.campaign,
                    args.slot,
                    actor_id=args.actor,
                    session_id=args.session,
                    request_id=args.request,
                )
                user_file = (
                    str(_sync_database_to_user(database, args.campaign, args.workspace))
                    if args.workspace
                    else None
                )
                _emit({**asdict(result), "user_md": user_file})
        elif args.area == "undo":
            result = UndoManager(database).undo(
                args.campaign, count=args.count, actor_id=args.actor
            )
            user_file = (
                str(_sync_database_to_user(database, args.campaign, args.workspace))
                if args.workspace
                else None
            )
            _emit({**asdict(result), "count": result.count, "user_md": user_file})
        elif args.area == "vector":
            store = VectorStore()
            if not store.enabled:
                _emit(
                    {
                        "error": "ChromaDBNotConfigured",
                        "message": (
                            "Set CHROMA_DB_URL (for HTTP) or CHROMA_DB_PATH "
                            "(for persistent local) to enable the vector store."
                        ),
                    }
                )
                return 1
            if args.action == "migrate":
                result = _vector_migrate(database, store, batch_size=args.batch_size)
                _emit(result)
            elif args.action == "status":
                _emit(_vector_status(store))
            elif args.action == "verify":
                _emit(_vector_verify(database, store, sample_count=args.count))
            else:
                _emit(_vector_reindex(database, store))
        else:
            ingest_service = RuleIngestService(database)
            search_service = RuleSearchService(database)
            if args.action == "ingest-srd":
                references_dir = args.references_dir or str(
                    Path(__file__).parents[2]
                    / "skills"
                    / "dnd-dm"
                    / "srd"
                    / "references"
                )
                _emit(
                    asdict(
                        ingest_service.ingest_srd(
                            references_dir,
                            embed=not args.no_embed,
                            force=args.force,
                        )
                    )
                )
            elif args.action == "ingest-zh-cn":
                _emit(
                    asdict(
                        ingest_service.ingest_directory_srd(
                            args.references_dir,
                            embed=not args.no_embed,
                            force=args.force,
                        )
                    )
                )
            elif args.action == "bind":
                profile_id = ingest_service.bind_campaign(
                    args.campaign,
                    rule_set_id=args.rule_set,
                    publication_ids=args.publication,
                )
                _emit({"campaign_id": args.campaign, "profile_id": profile_id})
            elif args.action == "search":
                hits = search_service.search(
                    args.query,
                    campaign_id=args.campaign,
                    rule_set_id=args.rule_set,
                    publication_ids=args.publication,
                    top_k=args.top_k,
                    dense=not args.no_dense,
                )
                payload: dict[str, Any] = {"query": args.query, "hits": [asdict(hit) for hit in hits]}
                if args.expand and hits:
                    payload["expanded"] = search_service.expand(
                        hits[0].chunk_id, mode=args.expand
                    )
                _emit(payload)
            elif args.action == "expand":
                _emit(search_service.expand(args.chunk, mode=args.mode))
            elif args.action == "tree":
                with database.transaction() as session:
                    tree = []
                    for rule_set in session.scalars(
                        select(RuleSet).order_by(RuleSet.game_system, RuleSet.edition)
                    ):
                        publications = []
                        for publication in session.scalars(
                            select(RulePublication)
                            .where(RulePublication.rule_set_id == rule_set.id)
                            .order_by(RulePublication.priority.desc(), RulePublication.name)
                        ):
                            section_count = session.scalar(
                                select(func.count())
                                .select_from(RuleSection)
                                .where(RuleSection.publication_id == publication.id)
                            )
                            top_level_sections = list(
                                dict.fromkeys(
                                    session.scalars(
                                        select(RuleSection.title)
                                        .where(
                                            RuleSection.publication_id == publication.id,
                                            RuleSection.depth == 1,
                                            ~RuleSection.title.like("Page %"),
                                        )
                                        .order_by(RuleSection.source_id, RuleSection.order_index)
                                    )
                                )
                            )
                            publications.append(
                                {
                                    "id": publication.id,
                                    "name": publication.name,
                                    "type": publication.publication_type,
                                    "parent_publication_id": publication.parent_publication_id,
                                    "sections": int(section_count or 0),
                                    "top_level_sections": top_level_sections,
                                }
                            )
                        tree.append(
                            {
                                "id": rule_set.id,
                                "game_system": rule_set.game_system,
                                "edition": rule_set.edition,
                                "release": rule_set.release,
                                "locale": rule_set.locale,
                                "publications": publications,
                            }
                        )
                _emit({"rule_sets": tree})
            else:
                with database.transaction() as session:
                    def count(model, *conditions) -> int:
                        statement = select(func.count()).select_from(model)
                        if conditions:
                            statement = statement.where(*conditions)
                        return int(session.scalar(statement) or 0)

                    _emit(
                        {
                            "rule_sets": count(RuleSet),
                            "publications": count(RulePublication),
                            "sources": count(RuleSource),
                            "sections": count(RuleSection),
                            "chunks": count(RuleChunk),
                            "embedded_chunks": count(
                                RuleChunk, RuleChunk.embedding_json.is_not(None)
                            ),
                            "embedding_models": [
                                {
                                    "id": model.id,
                                    "model_name": model.model_name,
                                    "dimensions": model.dimensions,
                                    "active": model.is_active,
                                }
                                for model in session.scalars(select(EmbeddingModel))
                            ],
                        }
                    )
        return 0
    except Exception as exc:
        _emit({"error": type(exc).__name__, "message": str(exc)})
        return 1
    finally:
        database.dispose()


if __name__ == "__main__":
    sys.exit(main())
