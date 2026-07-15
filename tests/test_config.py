from unittest.mock import patch

from guiltyspark.config import Settings


def test_use_specific_models_override_legacy_model() -> None:
    with patch("guiltyspark.config._load_env_files"), patch.dict(
        "os.environ",
        {
            "GUILTYSPARK_MODEL": "general-model",
            "GUILTYSPARK_ANALYSIS_MODEL": "analysis-model",
            "GUILTYSPARK_REMEDIATION_MODEL": "gpt-5.6-luna",
        },
        clear=True,
    ):
        settings = Settings.from_env()

    assert settings.analysis_model_name == "analysis-model"
    assert settings.remediation_model_name == "gpt-5.6-luna"


def test_use_specific_models_fall_back_to_legacy_model() -> None:
    with patch("guiltyspark.config._load_env_files"), patch.dict(
        "os.environ", {"GUILTYSPARK_MODEL": "general-model"}, clear=True
    ):
        settings = Settings.from_env()

    assert settings.analysis_model_name == "general-model"
    assert settings.remediation_model_name == "general-model"


def test_unset_models_allow_codex_cli_default() -> None:
    with patch("guiltyspark.config._load_env_files"), patch.dict(
        "os.environ", {}, clear=True
    ):
        settings = Settings.from_env()

    assert settings.analysis_model_name is None
    assert settings.remediation_model_name is None
