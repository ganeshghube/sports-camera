"""Low-cost broadcast score overlay for FFmpeg output.

The browser overlay remains the rich/public presentation. This service gives
FFmpeg a small, reloadable text file so recordings and RTMP streams can carry
an up-to-date score without running a browser renderer on Raspberry Pi.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path


class BroadcastScoreOverlay:
    def __init__(self, store, root: Path, log):
        self.store = store
        self.root = Path(root)
        self.log = log
        self.path = self.root / "data" / "stream_score.txt"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.RLock()

    @staticmethod
    def _score_text(d) -> str:
        if not isinstance(d, dict):
            return "CRICKET LIVE  |  SET UP MATCH"
        team = str(d.get("batTeam") or "TEAM").strip()
        score = f"{int(d.get('runs') or 0)}/{int(d.get('wkts') or 0)}"
        balls = int(d.get("balls") or 0)
        overs = f"{balls // 6}.{balls % 6}"
        target = int(d.get("target") or 0)
        target_part = f"  |  TARGET {target}  NEED {max(0, target - int(d.get('runs') or 0))}" if target else ""
        return f"{team}   {score}   ({overs} ov){target_part}"

    @staticmethod
    def _scorecard_text(d) -> str:
        if not isinstance(d, dict):
            return "LIVE SCORECARD"
        team = str(d.get("batTeam") or "TEAM").strip()
        p1 = str(d.get("p1n") or "—").strip()
        p2 = str(d.get("p2n") or "—").strip()
        p1v = f"{int(d.get('p1r') or 0)}({int(d.get('p1b') or 0)})"
        p2v = f"{int(d.get('p2r') or 0)}({int(d.get('p2b') or 0)})"
        star1, star2 = ("*", "") if int(d.get("str") or 1) == 1 else ("", "*")
        bowler = str(d.get("bwn") or "—").strip()
        bwl_ov = f"{int(d.get('bwb') or 0) // 6}.{int(d.get('bwb') or 0) % 6}"
        bwl = f"{int(d.get('bwr') or 0)}-{int(d.get('bww') or 0)}  {bwl_ov} ov"
        inn = "2ND INN" if int(d.get("innings") or 1) == 2 else "1ST INN"
        return f"{team}  {inn}\n{star1}{p1} {p1v}   {star2}{p2} {p2v}\nBWL {bowler}  {bwl}"

    def _ui(self):
        ui = self.store.get("ui")
        return ui if isinstance(ui, dict) else {}

    def write_once(self):
        data = self.store.get("cricket")
        ui = self._ui()
        scorebar = ui.get("scorebar", True) is not False
        scorecard = ui.get("scorecard", True) is True
        try:
            # Do not rename/replace a file which FFmpeg may currently have open.
            # Windows can reject os.replace with WinError 5 while drawtext reloads it.
            text = self._score_text(data) if scorebar else ""
            card_text = self._scorecard_text(data) if scorecard else ""
            card_path = self.root / "data" / "stream_scorecard.txt"
            for attempt in range(2):
                try:
                    self.path.write_text(text, encoding="utf-8")
                    card_path.write_text(card_text, encoding="utf-8")
                    break
                except PermissionError:
                    if attempt:
                        raise
                    time.sleep(0.03)
        except Exception as exc:
            self.log(f"Broadcast overlay write failed: {exc}")

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.write_once()
            self.thread = threading.Thread(target=self._run, name="score-overlay", daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stop_event.wait(0.5):
            self.write_once()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            t = self.thread
            self.thread = None
        if t and t.is_alive():
            t.join(timeout=1)

    def filter(self, width: int = 1280, height: int = 720) -> str:
        """Return a fixed FFmpeg filter; UI toggles only change the text files.
        This keeps a running YouTube/recording process stable when the scorer
        turns the score bar or scorecard on/off.
        """
        self.start()
        fontsize = 34 if height <= 720 else 48
        card_font = 22 if height <= 720 else 30
        bar_y = height - (74 if height <= 720 else 102)
        card_x = max(24, width - min(700, max(520, width // 2)) - 12)
        return ",".join([
            f"drawtext=textfile='data/stream_scorecard.txt':reload=1:fontcolor=white:fontsize={card_font}:x={card_x}:y=38:line_spacing=7:box=1:boxcolor=black@0.78:boxborderw=16",
            f"drawtext=textfile='data/stream_score.txt':reload=1:fontcolor=white:fontsize={fontsize}:x=24:y={bar_y}:box=1:boxcolor=black@0.78:boxborderw=18",
        ])
