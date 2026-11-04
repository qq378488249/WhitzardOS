from __future__ import annotations

from pathlib import Path

from qitos.render._hooks_impl import RenderStreamHook


def test_render_stream_hook_keeps_in_memory_buffer_bounded(tmp_path: Path) -> None:
    """The hook must not grow without bound; events still go to the JSONL file.

    Keeping every render event of a long run in memory made the agent process
    reach several gigabytes and get OOM-killed.
    """
    out = tmp_path / "render_events.jsonl"
    hook = RenderStreamHook(output_jsonl=str(out))
    total = RenderStreamHook.MAX_BUFFERED_EVENTS + 50

    for index in range(total):
        hook._emit("engine_event", f"node_{index}", step_id=index, payload={"i": index})

    assert len(hook.events) == RenderStreamHook.MAX_BUFFERED_EVENTS
    assert hook.events[-1].node == f"node_{total - 1}"
    written = out.read_text(encoding="utf-8").splitlines()
    assert len(written) == total
