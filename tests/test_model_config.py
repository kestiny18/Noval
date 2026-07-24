import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from noval.model_config import (
    BUILTIN_PROFILES,
    CUSTOM_PROFILE_ID,
    Connection,
    ConfiguredModel,
    ModelConfiguration,
    ModelConfigurationConflict,
    ModelConfigurationError,
    ModelConfigurationStore,
    load_settings_document,
    packaged_model_configuration,
    packaged_settings,
    parse_model_configuration,
    public_provider_profiles,
)


def write_document(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


def test_missing_settings_loads_schema_v2_packaged_defaults(tmp_path):
    path = tmp_path / "settings.json"

    document = load_settings_document(path)
    configuration = parse_model_configuration(document["models"])

    assert document["schema_version"] == 2
    assert configuration.default_model_id == "model-deepseek-v4-pro-default"
    assert configuration.configured_model(
        configuration.default_model_id
    ).model == "deepseek-v4-pro"
    assert not path.exists()


def test_catalog_exposes_six_builtin_profiles_and_custom_sentinel():
    assert [profile.id for profile in BUILTIN_PROFILES] == [
        "deepseek",
        "qwen",
        "moonshot",
        "zhipu",
        "openai",
        "google",
    ]
    public = public_provider_profiles()
    assert [profile["id"] for profile in public[:-1]] == [
        profile.id for profile in BUILTIN_PROFILES
    ]
    assert public[-1] == {
        "schema_version": 2,
        "id": CUSTOM_PROFILE_ID,
        "label": "Custom",
        "kind": "custom",
        "adapter": "openai-compatible",
        "requires_base_url": True,
    }
    assert all("base_url" not in profile for profile in public[:-1])
    assert all("api_key_env" not in profile for profile in public[:-1])


@pytest.mark.parametrize("version", [None, 1, 3, "2"])
def test_settings_rejects_any_non_v2_schema_without_mutation(tmp_path, version):
    path = tmp_path / "settings.json"
    document = packaged_settings()
    if version is None:
        document.pop("schema_version")
    else:
        document["schema_version"] = version
    original = json.dumps(document)
    path.write_text(original, encoding="utf-8")

    with pytest.raises(
        ModelConfigurationError, match="unsupported_settings_schema"
    ):
        load_settings_document(path)

    assert path.read_text(encoding="utf-8") == original


def custom_configuration(*, base_url="https://gateway.example.test/v1"):
    return ModelConfiguration(
        connections=(
            Connection(
                id="connection-custom",
                revision=1,
                label="Custom",
                profile_id="custom",
                adapter="openai-compatible",
                base_url=base_url,
                api_key_env="CUSTOM_API_KEY",
            ),
        ),
        configured=(
            ConfiguredModel(
                id="model-custom",
                label="Custom Model",
                connection_id="connection-custom",
                model="model-name",
            ),
        ),
        default_model_id="model-custom",
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test/v1",
        "ftp://example.test/v1",
        "https://user:pass@example.test/v1",
        "https://example.test/v1?q=secret",
        "https://example.test/v1#fragment",
        "relative/path",
    ],
)
def test_custom_connections_reject_unsafe_base_urls(base_url):
    candidate = custom_configuration(base_url=base_url)

    with pytest.raises(ModelConfigurationError, match="invalid_base_url"):
        parse_model_configuration(
            {
                "connections": [
                    {
                        **candidate.connections[0].public_dict(),
                        "api_key_env": "CUSTOM_API_KEY",
                    }
                ],
                "configured": [
                    model.public_dict() for model in candidate.configured
                ],
                "default_model_id": candidate.default_model_id,
            }
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "https://gateway.example.test/v1/",
    ],
)
def test_custom_connections_accept_https_or_loopback(base_url):
    candidate = custom_configuration(base_url=base_url)
    document = {
        "connections": [
            {
                "id": candidate.connections[0].id,
                "revision": 1,
                "label": candidate.connections[0].label,
                "profile_id": "custom",
                "adapter": "openai-compatible",
                "base_url": base_url,
                "api_key_env": "CUSTOM_API_KEY",
            }
        ],
        "configured": [model.public_dict() for model in candidate.configured],
        "default_model_id": candidate.default_model_id,
    }

    parsed = parse_model_configuration(document)

    assert parsed.connections[0].base_url.endswith("/v1")


def test_reference_and_uniqueness_validation_is_strict():
    document = packaged_settings()["models"]
    document["connections"].append(dict(document["connections"][0]))
    with pytest.raises(ModelConfigurationError, match="duplicate_id"):
        parse_model_configuration(document)

    document = packaged_settings()["models"]
    document["configured"][0]["connection_id"] = "missing"
    with pytest.raises(ModelConfigurationError, match="connection_not_found"):
        parse_model_configuration(document)

    document = packaged_settings()["models"]
    document["default_model_id"] = "missing"
    with pytest.raises(
        ModelConfigurationError, match="configured_model_not_found"
    ):
        parse_model_configuration(document)


def test_builtin_transport_and_model_fields_cannot_drift():
    document = packaged_settings()["models"]
    document["connections"][0]["base_url"] = "https://gateway.example.test"
    with pytest.raises(ModelConfigurationError, match="builtin_profile_mismatch"):
        parse_model_configuration(document)

    document = packaged_settings()["models"]
    document["configured"][0]["model"] = "unknown-model"
    with pytest.raises(ModelConfigurationError, match="unsupported_profile_model"):
        parse_model_configuration(document)


def test_store_mutation_is_atomic_and_preserves_unrelated_settings(tmp_path):
    path = tmp_path / "settings.json"
    document = packaged_settings()
    document["max_steps"] = 17
    write_document(path, document)
    store = ModelConfigurationStore(path)
    original = store.snapshot()
    candidate = custom_configuration()

    updated = store.mutate(
        original.revision,
        lambda current: replace(
            current,
            connections=current.connections + candidate.connections,
            configured=current.configured + candidate.configured,
            default_model_id="model-custom",
        ),
    )

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert updated.revision == original.revision + 1
    assert updated.default_model_id == "model-custom"
    assert persisted["max_steps"] == 17
    assert persisted["models"]["revision"] == updated.revision
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "settings.json.lock").exists()


def test_failed_mutation_leaves_memory_and_disk_unchanged(tmp_path):
    path = tmp_path / "settings.json"
    document = packaged_settings()
    write_document(path, document)
    store = ModelConfigurationStore(path)
    original_text = path.read_text(encoding="utf-8")
    original = store.snapshot()

    with pytest.raises(ModelConfigurationError, match="configured_model_not_found"):
        store.mutate(
            original.revision,
            lambda current: replace(current, default_model_id="missing"),
        )

    assert store.snapshot() == original
    assert path.read_text(encoding="utf-8") == original_text


def test_store_detects_optimistic_revision_conflicts(tmp_path):
    path = tmp_path / "settings.json"
    write_document(path, packaged_settings())
    first = ModelConfigurationStore(path)
    second = ModelConfigurationStore(path)

    first.mutate(
        first.snapshot().revision,
        lambda current: replace(current, default_model_id=current.default_model_id),
    )

    with pytest.raises(ModelConfigurationConflict, match="configuration_conflict"):
        second.mutate(
            second.snapshot().revision,
            lambda current: replace(
                current, default_model_id=current.default_model_id
            ),
        )


def test_concurrent_stores_serialize_one_complete_transaction(tmp_path):
    path = tmp_path / "settings.json"
    write_document(path, packaged_settings())
    stores = (ModelConfigurationStore(path), ModelConfigurationStore(path))

    def rename(store, label):
        original = store.snapshot().connections[0]
        try:
            return store.upsert_connection(
                replace(original, label=label),
                expected_revision=store.snapshot().revision,
            )
        except ModelConfigurationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda arguments: rename(*arguments),
                zip(stores, ("First", "Second")),
            )
        )

    assert sum(isinstance(item, ModelConfiguration) for item in results) == 1
    assert sum(isinstance(item, ModelConfigurationConflict) for item in results) == 1
    persisted = load_settings_document(path)
    parsed = parse_model_configuration(persisted["models"])
    assert parsed.revision == 2
    assert parsed.connections[0].label in {"First", "Second"}


def test_store_explicit_create_activate_and_delete_operations(tmp_path):
    path = tmp_path / "settings.json"
    write_document(path, packaged_settings())
    store = ModelConfigurationStore(path)
    custom = custom_configuration()

    current = store.upsert_connection(
        custom.connections[0],
        expected_revision=store.snapshot().revision,
    )
    current = store.upsert_configured_model(
        custom.configured[0],
        expected_revision=current.revision,
    )
    current = store.set_default_model(
        custom.default_model_id,
        expected_revision=current.revision,
    )

    assert current.default_model_id == "model-custom"
    with pytest.raises(ModelConfigurationError, match="connection_in_use"):
        store.delete_connection(
            "connection-custom", expected_revision=current.revision
        )

    current = store.set_default_model(
        "model-deepseek-v4-pro-default",
        expected_revision=current.revision,
    )
    current = store.delete_configured_model(
        "model-custom", expected_revision=current.revision
    )
    current = store.delete_connection(
        "connection-custom", expected_revision=current.revision
    )
    assert all(item.id != "connection-custom" for item in current.connections)


def test_connection_revision_changes_only_for_transport_fields(tmp_path):
    path = tmp_path / "settings.json"
    write_document(path, packaged_settings())
    store = ModelConfigurationStore(path)
    original = store.snapshot().connections[0]

    current = store.upsert_connection(
        replace(original, label="Renamed"),
        expected_revision=store.snapshot().revision,
    )
    renamed = current.connection(original.id)
    assert renamed.revision == original.revision

    current = store.upsert_connection(
        replace(renamed, api_key="replacement-secret"),
        expected_revision=current.revision,
    )
    assert current.connection(original.id).revision == original.revision + 1


def test_credentials_are_absent_from_public_projection_repr_and_errors(
    tmp_path, monkeypatch
):
    secret = "credential-that-must-never-leak"
    monkeypatch.setenv("CUSTOM_API_KEY", "environment-secret")
    configuration = custom_configuration()
    connection = replace(configuration.connections[0], api_key=secret)
    configuration = replace(configuration, connections=(connection,))
    assert secret not in repr(connection)
    assert secret not in repr(configuration)
    assert secret not in json.dumps(configuration.public_dict())
    assert "environment-secret" not in json.dumps(configuration.public_dict())

    path = tmp_path / "settings.json"
    write_document(path, packaged_settings())
    store = ModelConfigurationStore(path)
    assert secret not in repr(store)
    with pytest.raises(ModelConfigurationError) as raised:
        store.resolve_api_key("missing")
    assert secret not in str(raised.value)


def test_store_resolves_stored_then_environment_credentials(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    document = packaged_settings()
    connection = document["models"]["connections"][0]
    connection["api_key"] = "stored-secret"
    write_document(path, document)
    store = ModelConfigurationStore(path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")

    assert store.resolve_api_key(connection["id"]) == "stored-secret"

    connection["api_key"] = ""
    write_document(path, document)
    store.reload()
    assert store.resolve_api_key(connection["id"]) == "environment-secret"


def test_packaged_configuration_is_valid():
    configuration = packaged_model_configuration()
    assert parse_model_configuration(
        packaged_settings()["models"]
    ) == configuration
