from __future__ import annotations

from qitos.benchmark.cybergym.agent.agent_impl.observation.validation import (
    ValidationMixin,
)


def test_python_source_dump_is_still_blocked() -> None:
    command = (
        "python3 - <<'PY'\n"
        "for line in open('repo-vul/yara/libyara/pe.c'):\n"
        "    print(line)\n"
        "PY"
    )

    assert ValidationMixin._bash_is_python_source_browse_command(command) is True


def test_python_writing_pocs_candidate_is_allowed() -> None:
    command = (
        "python3 - <<'PY'\n"
        "data = open('repo-vul/seed.bin', 'rb').read()\n"
        "open('pocs/candidate.bin', 'wb').write(data)\n"
        "PY"
    )

    assert ValidationMixin._bash_is_python_source_browse_command(command) is False


def test_python_building_bytes_for_a_poc_is_allowed() -> None:
    command = (
        "python3 - <<'PY'\n"
        "import struct\n"
        "data = open('repo-vul/seed.bin', 'rb').read()\n"
        "patch = struct.pack('<I', 0xFFFFFFFF)\n"
        "print(len(data), len(patch))\n"
        "PY"
    )

    assert ValidationMixin._bash_is_python_source_browse_command(command) is False


def test_non_python_command_is_not_classified_as_python_browse() -> None:
    command = "grep -n 'pe_parse_exports' repo-vul/yara/libyara/pe.c"

    assert ValidationMixin._bash_is_python_source_browse_command(command) is False
