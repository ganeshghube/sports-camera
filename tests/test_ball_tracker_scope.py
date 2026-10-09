import ast
from pathlib import Path


def test_ball_tracker_main_declares_runtime_globals():
    path = Path(__file__).parents[1] / "ai" / "ball_tracker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    globals_declared = {name for node in main.body if isinstance(node, ast.Global) for name in node.names}
    assert {"DEVICE", "AUTO_CAMERA_TRACK", "MIN_TRACK_CONFIDENCE", "AI_MAX_FPS"} <= globals_declared
