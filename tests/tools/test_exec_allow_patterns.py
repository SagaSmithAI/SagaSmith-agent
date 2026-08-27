"""Tests for allow_patterns priority over deny_patterns."""

from __future__ import annotations

import pytest

from nanobot.agent.tools.shell import ExecTool


def test_deny_patterns_block_rm_rf():
    """Baseline: rm -rf is blocked by default deny list."""
    tool = ExecTool()
    result = tool._guard_command("rm -rf /tmp/build", "/tmp")
    assert result is not None
    assert "deny pattern filter" in result.lower()


def test_allow_patterns_bypass_deny():
    """allow_patterns take priority: matching command skips deny check."""
    tool = ExecTool(allow_patterns=[r"rm\s+-rf\s+/tmp/.*"])
    result = tool._guard_command("rm -rf /tmp/build", "/tmp")
    assert result is None


def test_allow_patterns_must_match_to_bypass():
    """Non-matching allow_patterns do NOT bypass deny."""
    tool = ExecTool(allow_patterns=[r"rm\s+-rf\s+/opt/"])
    result = tool._guard_command("rm -rf /tmp/build", "/tmp")
    assert result is not None
    assert "deny pattern filter" in result.lower()


def test_extra_deny_patterns_from_config():
    """User-supplied deny patterns are appended to built-in list."""
    tool = ExecTool(deny_patterns=[r"\bping\b"])
    # ping is blocked by extra deny
    assert tool._guard_command("ping example.com", "/tmp") is not None
    # rm -rf still blocked by built-in deny
    assert tool._guard_command("rm -rf /tmp/x", "/tmp") is not None


def test_allow_patterns_bypass_extra_deny():
    """allow_patterns also bypasses user-supplied deny patterns."""
    tool = ExecTool(
        deny_patterns=[r"\bping\b"],
        allow_patterns=[r"\bping\s+example\.com\b"],
    )
    result = tool._guard_command("ping example.com", "/tmp")
    assert result is None


def test_allow_patterns_is_whitelist_only():
    """When allow_patterns is set, non-matching non-denied commands are blocked."""
    tool = ExecTool(allow_patterns=[r"echo\s+hello"])
    # echo matches allow → ok
    assert tool._guard_command("echo hello", "/tmp") is None
    # ls does not match allow and is not in deny → blocked by allowlist
    result = tool._guard_command("ls /tmp", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allow_patterns_do_not_allow_chained_command_bypass():
    """A whole-string match must not authorize a denied later segment."""
    tool = ExecTool(allow_patterns=[r"echo\s+hello[\s\S]*"])
    result = tool._guard_command("echo hello; rm -rf /", "/tmp")
    assert result is not None
    assert "deny pattern filter" in result.lower()


def test_allow_patterns_do_not_allow_comment_tail_bypass():
    """Comment tails must not make a non-allowlisted command match."""
    tool = ExecTool(allow_patterns=[r"echo allowlisted"])
    result = tool._guard_command("touch canary # echo allowlisted", "/tmp")
    assert result is not None
    assert "allowlist" in result.lower()


def test_deny_patterns_search_original_command_with_quoted_hash():
    """Deny checks must still inspect text after a quoted hash."""
    tool = ExecTool(deny_patterns=[r"\brm\s+-rf\s+/"])
    result = tool._guard_command('echo "#"; rm -rf /', "/tmp")
    assert result is not None
    assert "deny pattern filter" in result.lower()


def test_allow_patterns_fullmatch_allows_exact_command():
    """A full-command allow pattern can still exempt an exact denied command."""
    tool = ExecTool(allow_patterns=[r"rm\s+-rf\s+/tmp/build"])
    result = tool._guard_command("rm -rf /tmp/build", "/tmp")
    assert result is None


@pytest.mark.parametrize("operator", [";", "&&", "||", "|", "|&", "\n", "\r", "\r\n"])
def test_allow_patterns_require_every_shell_segment(operator: str):
    """A broad pattern for the first command cannot authorize a later command."""
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    result = tool._guard_command(f"echo allowlisted {operator} touch canary", "/tmp")

    assert result is not None
    assert "allowlist" in result.lower()


def test_allow_patterns_require_backgrounded_segments_to_match():
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    result = tool._guard_command("echo allowlisted & touch canary", "/tmp")

    assert result is not None
    assert "allowlist" in result.lower()


def test_allow_patterns_allow_multiple_matching_segments():
    tool = ExecTool(
        allow_patterns=[
            r"echo\s+first",
            r"echo\s+second",
        ]
    )

    assert tool._guard_command("echo first && echo second", "/tmp") is None


def test_allow_patterns_treat_pipe_stderr_as_one_separator():
    tool = ExecTool(allow_patterns=[r"echo\s+first", r"echo\s+second"])

    assert ExecTool._split_shell_segments("echo first |& echo second") == [
        "echo first",
        "echo second",
    ]
    assert tool._guard_command("echo first |& echo second", "/tmp", shell="bash") is None


@pytest.mark.parametrize(
    ("command", "segments"),
    [
        ('echo "a; b && c | d & e"', ['echo "a; b && c | d & e"']),
        ("echo 'a; b && c | d & e'", ["echo 'a; b && c | d & e'"]),
        (r"echo escaped\;value && echo done", [r"echo escaped\;value", "echo done"]),
        ("(echo one; echo two) || echo done", ["(echo one; echo two)", "echo done"]),
    ],
)
def test_split_shell_segments_respects_shell_boundaries(
    command: str,
    segments: list[str],
):
    assert ExecTool._split_shell_segments(command) == segments


def test_split_shell_segments_keep_line_continuation_intact():
    assert ExecTool._split_shell_segments("echo allowlisted \\\nextra") == [
        "echo allowlisted \\\nextra"
    ]


@pytest.mark.parametrize(
    ("command", "dialect", "shell"),
    [
        ("echo allowlisted $(touch canary)", "posix", "bash"),
        ('echo "allowlisted $(touch canary)"', "posix", "bash"),
        ("echo allowlisted `touch canary`", "posix", "bash"),
        ("echo allowlisted <(touch canary)", "posix", "bash"),
        ("echo allowlisted $(touch canary)", "powershell", "pwsh"),
    ],
)
def test_allow_patterns_fail_closed_for_nested_shell_execution(
    command: str,
    dialect: str,
    shell: str,
):
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    assert ExecTool._split_shell_segments(command, dialect=dialect) is None
    result = tool._guard_command(command, "/tmp", shell=shell)
    assert result is not None
    assert "allowlist" in result.lower()


@pytest.mark.parametrize(
    ("command", "pattern", "shell"),
    [
        ("echo '$(touch canary)'", r"echo\s+'\$\(touch\s+canary\)'", "bash"),
        ("echo '`touch canary`'", r"echo\s+'`touch\s+canary`'", "bash"),
        ("echo '$(touch canary)'", r"echo\s+'\$\(touch\s+canary\)'", "pwsh"),
    ],
)
def test_allow_patterns_keep_single_quoted_substitution_literal(
    command: str,
    pattern: str,
    shell: str,
):
    tool = ExecTool(allow_patterns=[pattern])

    assert tool._guard_command(command, "/tmp", shell=shell) is None


def test_powershell_backtick_escapes_operator_instead_of_starting_substitution():
    command = "echo allowlisted `& touch canary"
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    assert ExecTool._split_shell_segments(command, dialect="powershell") == [command]
    assert tool._guard_command(command, "/tmp", shell="pwsh") is None


def test_powershell_backslash_does_not_escape_command_operator():
    command = r"echo allowlisted \& touch canary"
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    assert ExecTool._split_shell_segments(command, dialect="powershell") == [
        r"echo allowlisted \&",
        "touch canary",
    ]
    result = tool._guard_command(command, "/tmp", shell="pwsh")
    assert result is not None
    assert "allowlist" in result.lower()


def test_cmd_caret_escapes_operator_and_absolute_path_selects_cmd_dialect():
    command = "echo allowlisted ^& touch canary"
    shell = r"C:\Windows\System32\cmd.exe"
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    assert ExecTool._shell_dialect(shell) == "cmd"
    assert ExecTool._split_shell_segments(command, dialect="cmd") == [command]
    assert tool._guard_command(command, "/tmp", shell=shell) is None


def test_cmd_backslash_does_not_escape_command_operator():
    command = r"echo allowlisted \& touch canary"
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted[\s\S]*"])

    assert ExecTool._split_shell_segments(command, dialect="cmd") == [
        r"echo allowlisted \&",
        "touch canary",
    ]
    result = tool._guard_command(command, "/tmp", shell="cmd.exe")
    assert result is not None
    assert "allowlist" in result.lower()


def test_cmd_caret_inside_quotes_does_not_hide_later_operator():
    command = 'echo "allowlisted^" & touch canary'
    tool = ExecTool(allow_patterns=[r"echo\s+[\s\S]*"])

    assert ExecTool._split_shell_segments(command, dialect="cmd") == [
        'echo "allowlisted^" &',
        "touch canary",
    ]
    result = tool._guard_command(command, "/tmp", shell="cmd")
    assert result is not None
    assert "allowlist" in result.lower()


def test_allow_patterns_preserve_trailing_background_operator():
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted"])

    result = tool._guard_command("echo allowlisted &", "/tmp")

    assert result is not None
    assert "allowlist" in result.lower()
    assert ExecTool._split_shell_segments("echo allowlisted &") == ["echo allowlisted &"]


def test_allow_patterns_can_explicitly_allow_trailing_background_operator():
    tool = ExecTool(allow_patterns=[r"echo\s+allowlisted\s+&"])

    assert tool._guard_command("echo allowlisted &", "/tmp") is None


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        ("echo allowlisted 2>&1", r"echo\s+allowlisted\s+2>&1"),
        ("echo allowlisted &>output.log", r"echo\s+allowlisted\s+&>output\.log"),
    ],
)
def test_allow_patterns_do_not_split_ampersand_redirections(command: str, pattern: str):
    tool = ExecTool(allow_patterns=[pattern])

    assert ExecTool._split_shell_segments(command) == [command]
    assert tool._guard_command(command, "/tmp") is None


@pytest.mark.parametrize(
    ("command", "pattern", "shell"),
    [
        ("cat /outside", r"cat\s+/outside", "bash"),
        ("Get-Content /outside", r"get-content\s+/outside", "pwsh"),
        ("type /outside", r"type\s+/outside", "cmd"),
    ],
)
def test_allow_patterns_do_not_bypass_workspace_paths_in_any_shell_dialect(
    tmp_path, command: str, pattern: str, shell: str
):
    tool = ExecTool(allow_patterns=[pattern], restrict_to_workspace=True)

    result = tool._guard_command(command, str(tmp_path), shell=shell)

    assert result is not None
    assert "path outside working dir" in result.lower()
