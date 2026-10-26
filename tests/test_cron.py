"""Tests for the cron backend, using a fake crontab on PATH unless a runner is injected."""

from pathlib import Path

import pytest

from agent_session_backup.schedule import cron
from agent_session_backup.schedule.commands import CommandResult, run_command
from tests.conftest import FakeCrontab

EXE = "/home/agude/.local/bin/agent-session-backup"
BLOCK = "\n".join((cron.BEGIN, f"@hourly {EXE} run", cron.END))
ORIGINALS = [
    "",
    "MAILTO=ops@mail.invalid\n@daily /bin/true\n",
    "MAILTO=ops@mail.invalid\n@daily /bin/true",
    "@daily /bin/true\n\n",
]


@pytest.mark.parametrize("original", ORIGINALS)
def test_unrelated_lines_survive_install_and_uninstall_byte_for_byte(
    fake_crontab: FakeCrontab, original: str
) -> None:
    fake_crontab.write(original)
    assert cron.install(EXE) == "installed"
    assert fake_crontab.text().startswith(original)
    assert cron.uninstall() == "removed"
    assert fake_crontab.text() == original


def test_empty_crontab_gets_the_block(fake_crontab: FakeCrontab) -> None:
    assert cron.install(EXE) == "installed"
    assert fake_crontab.text() == BLOCK + "\n"


def test_second_install_is_unchanged_and_does_not_write(fake_crontab: FakeCrontab) -> None:
    cron.install(EXE)
    writes = fake_crontab.writes()
    assert cron.install(EXE) == "unchanged"
    assert fake_crontab.writes() == writes


def test_different_executable_updates_the_single_block(fake_crontab: FakeCrontab) -> None:
    cron.install(EXE)
    assert cron.install("/usr/local/bin/agent-session-backup") == "updated"
    text = fake_crontab.text()
    assert text.count(cron.BEGIN) == 1
    assert "/usr/local/bin/agent-session-backup run" in text
    assert EXE not in text


def test_update_keeps_a_block_that_ends_the_file_without_a_newline(
    fake_crontab: FakeCrontab,
) -> None:
    fake_crontab.write("MAILTO=ops@mail.invalid")
    cron.install(EXE)
    assert cron.install("/usr/local/bin/agent-session-backup") == "updated"
    assert fake_crontab.text().endswith(cron.END)
    assert cron.uninstall() == "removed"
    assert fake_crontab.text() == "MAILTO=ops@mail.invalid"


def test_uninstall_reports_removed_then_absent(fake_crontab: FakeCrontab) -> None:
    fake_crontab.write("MAILTO=ops@mail.invalid\n")
    cron.install(EXE)
    assert cron.uninstall() == "removed"
    assert fake_crontab.text() == "MAILTO=ops@mail.invalid\n"
    assert cron.uninstall() == "absent"


def test_two_blocks_are_refused_and_the_crontab_is_unchanged(fake_crontab: FakeCrontab) -> None:
    fake_crontab.write(BLOCK + "\n" + BLOCK + "\n")
    before = fake_crontab.text()
    with pytest.raises(cron.CronError, match="more than one"):
        cron.install(EXE)
    with pytest.raises(cron.CronError, match="more than one"):
        cron.uninstall()
    assert fake_crontab.text() == before


def test_unterminated_block_is_refused(fake_crontab: FakeCrontab) -> None:
    fake_crontab.write(f"{cron.BEGIN}\n@hourly {EXE} run\n")
    with pytest.raises(cron.CronError, match="unterminated"):
        cron.install(EXE)


def test_end_line_before_begin_line_is_refused(fake_crontab: FakeCrontab) -> None:
    fake_crontab.write(f"{cron.END}\n{cron.BEGIN}\n")
    with pytest.raises(cron.CronError, match="ends before it begins"):
        cron.install(EXE)


def test_missing_crontab_program_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    with pytest.raises(cron.CronError, match="crontab -l failed"):
        cron.install(EXE)


def test_read_failure_other_than_no_crontab_is_an_error() -> None:
    def runner(args: object, stdin: object) -> CommandResult:
        return CommandResult(2, "", "permission denied")

    with pytest.raises(cron.CronError, match="permission denied"):
        cron.install(EXE, runner)


def test_no_crontab_message_with_another_status_is_an_error() -> None:
    def runner(args: object, stdin: object) -> CommandResult:
        return CommandResult(1, "", "something else")

    with pytest.raises(cron.CronError, match="something else"):
        cron.uninstall(runner)


def test_failed_write_is_an_error() -> None:
    def runner(args: object, stdin: object) -> CommandResult:
        if args == ["crontab", "-l"]:
            return CommandResult(1, "", "no crontab for tester")
        return CommandResult(1, "", "write refused")

    with pytest.raises(cron.CronError, match="write refused"):
        cron.install(EXE, runner)


def test_default_runner_is_the_real_command_runner() -> None:
    assert cron.install.__defaults__ == (run_command,)
    assert cron.uninstall.__defaults__ == (run_command,)
