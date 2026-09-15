import pytest

from talk_studio import cli


def test_help_lists_captions(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    assert "captions" in capsys.readouterr().out


def test_captions_delegates_to_the_caption_cli(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["captions", "--help"])
    assert exit_info.value.code == 0
    assert "talk-studio captions" in capsys.readouterr().out
