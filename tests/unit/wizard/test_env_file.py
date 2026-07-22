import errno

import pytest

import threadbare.wizard.env_file as env_file
from threadbare.wizard.env_file import rewrite_env_text, write_env_updates


def test_rewrite_env_text_replaces_matching_key_preserves_everything_else():
    text = (
        "# a comment\n"
        "DISCORD_BOT_TOKEN=\n"
        "\n"
        "DATABASE_URL=postgresql://x\n"
    )

    result = rewrite_env_text(text, {"DISCORD_BOT_TOKEN": "real-token"})

    assert result == (
        "# a comment\n"
        "DISCORD_BOT_TOKEN=real-token\n"
        "\n"
        "DATABASE_URL=postgresql://x\n"
    )


def test_rewrite_env_text_appends_missing_key():
    text = "DATABASE_URL=postgresql://x\n"

    result = rewrite_env_text(text, {"DISCORD_BOT_TOKEN": "real-token"})

    assert result == (
        "DATABASE_URL=postgresql://x\n"
        "\n"
        "# Added by threadbare setup wizard\n"
        "DISCORD_BOT_TOKEN=real-token\n"
    )


def test_rewrite_env_text_quotes_values_with_whitespace_or_hash():
    text = "FOO=\n"

    result = rewrite_env_text(text, {"FOO": "has space"})
    assert result == 'FOO="has space"\n'

    result = rewrite_env_text(text, {"FOO": "has#hash"})
    assert result == 'FOO="has#hash"\n'


def test_rewrite_env_text_is_idempotent_when_rerun_with_same_updates():
    text = "DISCORD_BOT_TOKEN=\n"
    updates = {"DISCORD_BOT_TOKEN": "real-token"}

    once = rewrite_env_text(text, updates)
    twice = rewrite_env_text(once, updates)

    assert once == twice


def test_write_env_updates_creates_file_from_template_when_absent(tmp_path):
    template = tmp_path / ".env.example"
    template.write_text("DISCORD_BOT_TOKEN=\nDATABASE_URL=postgresql://x\n")
    target = tmp_path / ".env"

    write_env_updates(target, {"DISCORD_BOT_TOKEN": "real-token"}, template_path=template)

    content = target.read_text()
    assert "DISCORD_BOT_TOKEN=real-token" in content
    assert "DATABASE_URL=postgresql://x" in content


def test_write_env_updates_is_atomic_leaves_no_temp_file_on_success(tmp_path):
    target = tmp_path / ".env"
    target.write_text("DISCORD_BOT_TOKEN=\n")

    write_env_updates(target, {"DISCORD_BOT_TOKEN": "real-token"})

    leftover = [p for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftover == []


def test_write_env_updates_overwrites_existing_file_in_place(tmp_path):
    target = tmp_path / ".env"
    target.write_text("DISCORD_BOT_TOKEN=old\nOTHER_VAR=keep-me\n")

    write_env_updates(target, {"DISCORD_BOT_TOKEN": "new"})

    content = target.read_text()
    assert "DISCORD_BOT_TOKEN=new" in content
    assert "OTHER_VAR=keep-me" in content


def test_write_env_updates_raises_when_no_file_or_template_exists(tmp_path):
    target = tmp_path / ".env"

    from threadbare.wizard.env_file import EnvFileError

    with pytest.raises(EnvFileError):
        write_env_updates(target, {"FOO": "bar"}, template_path=tmp_path / "nope.example")


@pytest.mark.parametrize("errno_code", [errno.EBUSY, errno.EXDEV])
def test_write_env_updates_falls_back_to_in_place_write_when_replace_cant_swap_target(
    tmp_path, monkeypatch, errno_code
):
    # Simulates the real production failure: `docker-compose.yml` bind-mounts
    # a single file (`./.env:/app/.env`), and Linux refuses to rename()
    # something onto an active bind-mount point -- os.replace surfaces this
    # as EBUSY (or EXDEV on some kernel/storage-driver combinations).
    target = tmp_path / ".env"
    target.write_text("DISCORD_BOT_TOKEN=old\n")

    def fake_replace(src, dst):
        raise OSError(errno_code, "simulated bind-mount rename failure")

    monkeypatch.setattr(env_file.os, "replace", fake_replace)

    write_env_updates(target, {"DISCORD_BOT_TOKEN": "new"})

    assert target.read_text() == "DISCORD_BOT_TOKEN=new\n"
    leftover = [p for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftover == []


def test_write_env_updates_reraises_other_os_errors_and_cleans_up_temp_file(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("DISCORD_BOT_TOKEN=old\n")

    def fake_replace(src, dst):
        raise OSError(errno.EACCES, "simulated unrelated failure")

    monkeypatch.setattr(env_file.os, "replace", fake_replace)

    with pytest.raises(OSError):
        write_env_updates(target, {"DISCORD_BOT_TOKEN": "new"})

    leftover = [p for p in tmp_path.iterdir() if p.name != ".env"]
    assert leftover == []
    assert target.read_text() == "DISCORD_BOT_TOKEN=old\n"
