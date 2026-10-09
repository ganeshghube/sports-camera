from pathlib import Path


def test_launcher_does_not_force_selector_event_loop_on_windows():
    text = Path('run.py').read_text()
    assert 'WindowsSelectorEventLoopPolicy' not in text
    assert 'Proactor' in text


def test_broadcast_renderer_creates_proactor_loop_per_windows_thread():
    text = Path('backend/browser_compositor.py').read_text()
    assert 'ProactorEventLoop' in text
    assert 'asyncio.set_event_loop(thread_loop)' in text
    assert 'NotImplementedError' in text
