"""Embedding profile selection and index isolation tests."""

from __future__ import annotations

import pytest

from nanobot.dnd.rules import embedding


@pytest.fixture(autouse=True)
def _clean_embedding_environment(monkeypatch):
    for name in (
        "DND_EMBEDDING_MODE",
        "DND_EMBEDDING_MODEL",
        "DND_EMBEDDING_PROFILES",
        "DND_EMBEDDING_DEVICE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_profile_remains_bge_m3_on_cpu(monkeypatch) -> None:
    monkeypatch.setattr(embedding, "cuda_available", lambda: False)

    embedder = embedding.BgeM3Embedder(language="zh")

    assert embedder.profile == embedding.BGE_M3_PROFILE
    assert embedder.device == "cpu"
    assert embedder.dimensions == 1024


def test_dual_small_profiles_route_by_language(monkeypatch) -> None:
    monkeypatch.setenv(
        "DND_EMBEDDING_PROFILES",
        "bge_small_zh_v1_5,bge_small_en_v1_5",
    )

    assert embedding.profile_for_language("zh-CN") == embedding.BGE_SMALL_ZH_PROFILE
    assert embedding.profile_for_language("en-US") == embedding.BGE_SMALL_EN_PROFILE


def test_single_selected_small_profile_is_not_overridden_by_language(monkeypatch) -> None:
    monkeypatch.setenv("DND_EMBEDDING_PROFILES", "bge_small_en_v1_5")

    assert embedding.profile_for_language("zh") == embedding.BGE_SMALL_EN_PROFILE


def test_device_mode_does_not_force_model_choice(monkeypatch) -> None:
    monkeypatch.setenv("DND_EMBEDDING_MODE", "gpu")
    monkeypatch.setenv("DND_EMBEDDING_PROFILES", "bge_small_zh_v1_5")
    monkeypatch.setattr(embedding, "cuda_available", lambda: True)

    embedder = embedding.BgeM3Embedder(language="en")

    assert embedder.profile == embedding.BGE_SMALL_ZH_PROFILE
    assert embedder.device == "cuda"


def test_profile_collections_are_dimension_isolated() -> None:
    names = {
        embedding.collection_name("dnd_rules", profile)
        for profile in embedding.EMBEDDING_PROFILES.values()
    }

    assert len(names) == 3
    assert {profile.dimensions for profile in embedding.EMBEDDING_PROFILES.values()} == {
        384,
        512,
        1024,
    }


def test_unknown_profile_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("DND_EMBEDDING_PROFILES", "not-a-model")

    with pytest.raises(ValueError, match="unknown DND_EMBEDDING_PROFILES"):
        embedding.configured_profiles()
