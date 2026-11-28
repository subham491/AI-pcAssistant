from pathlib import Path

import pytest

from assistant.config import AuthMode, ConfigError, EditMode, load_config

TEMPLATE = Path(__file__).resolve().parents[1] / "home_template"


def write(home: Path, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.brain.auth_mode is AuthMode.SUBSCRIPTION
    assert cfg.safety.edit_mode is EditMode.REVIEW
    assert cfg.inbox_dir == tmp_path.resolve() / "inbox"


def test_overrides_and_enum_coercion(tmp_path):
    write(tmp_path, '[brain]\nauth_mode = "api_key"\n[safety]\nedit_mode = "auto"\n[voice]\nend_silence_s = 1\n')
    cfg = load_config(tmp_path)
    assert cfg.brain.auth_mode is AuthMode.API_KEY
    assert cfg.safety.edit_mode is EditMode.AUTO
    assert cfg.voice.end_silence_s == 1.0 and isinstance(cfg.voice.end_silence_s, float)


@pytest.mark.parametrize("text, message", [
    ('[brain]\nauth_mod = "x"\n', "unknown key"),
    ('[brian]\n', "unknown section"),
    ('[safety]\nedit_mode = "yolo"\n', "not one of"),
    ('[voice]\nenabled = "yes"\n', "true or false"),
    ('[voice\n', "config.toml"),
])
def test_bad_config_is_loud(tmp_path, text, message):
    write(tmp_path, text)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_shipped_template_parses():
    cfg = load_config(TEMPLATE)
    assert cfg.remote.project_name == "sister-project"


def test_lean_profile_is_the_default(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.brain.full_profile is False
    # Lean = project only: the user's ~200 plugin skills and connectors (the "user" source) stay out.
    assert cfg.brain.setting_sources == ["project"]
    assert cfg.brain.profile_sources(False) == ["project"]
    # On demand, the full profile adds ~/.claude back on top.
    assert cfg.brain.profile_sources(True) == ["user", "project"]


def test_profile_sources_does_not_duplicate_user(tmp_path):
    write(tmp_path, '[brain]\nsetting_sources = ["user", "project"]\nfull_profile = true\n')
    cfg = load_config(tmp_path)
    assert cfg.brain.profile_sources(True) == ["user", "project"]
    assert cfg.brain.profile_sources(False) == ["user", "project"]


def test_click_to_talk_defaults_and_ui_section(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.voice.wake_word_enabled is False  # mic stays off until the click
    assert cfg.voice.stt_model == "auto" and cfg.voice.end_silence_s >= 2.0 and cfg.voice.stop_on_silence
    assert cfg.ui.name == "Hellbot"
    (tmp_path / "config.toml").write_text('[ui]\nname = "Friday"\ncorner = "top-left"\n', encoding="utf-8")
    assert load_config(tmp_path).ui.name == "Friday"
    (tmp_path / "config.toml").write_text('[ui]\ncorner = "middle"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="corner"):
        load_config(tmp_path)
