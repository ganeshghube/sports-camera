import time

import numpy as np

from ai.ball_tracker import CameraReader


def test_camera_reader_returns_monotonic_frame_id_without_reprocessing():
    reader = CameraReader({})
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    with reader.lock:
        reader.frame = frame
        reader.frame_time = time.time()
        reader.frame_id = 41

    ok1, out1, id1 = reader.read()
    ok2, out2, id2 = reader.read()

    assert ok1 and ok2
    assert id1 == id2 == 41
    assert out1.shape == out2.shape == frame.shape
