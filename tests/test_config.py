from app.config import Settings


def test_settings_reads_custom_openai_base_url() -> None:
    settings = Settings.model_validate({
        "OPENAI_API_KEY": "sk-chat-key",
        "OPENAI_API_BASE": "https://api.apinex.bond/v1",
    })

    assert settings.openai_api_base == "https://api.apinex.bond/v1"


def test_settings_support_separate_embedding_keys() -> None:
    settings = Settings.model_validate({
        "OPENAI_API_KEY": "sk-chat-key",
        "OPENAI_API_BASE": "https://api.apinex.bond/v1",
        "EMBEDDING_API_KEY": "sk-embed-key",
        "EMBEDDING_API_BASE": "https://api.openai.com/v1",
    })

    assert settings.embedding_api_key == "sk-embed-key"
    assert settings.embedding_api_base == "https://api.openai.com/v1"
