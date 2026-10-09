"""Optional Raspberry Pi STEP/DIR pan/tilt controller.

Designed for A4988/DRV8825/TMC-style drivers. It is deliberately optional:
Windows/ESP32 serial deployments keep using the existing controller.
The worker thread pulses GPIO pins so AI inference never waits on a motor move.
"""
from __future__ import annotations
import os, threading, time

class GpioStepperPanTilt:
    def __init__(self, pan_step=17, pan_dir=27, tilt_step=22, tilt_dir=23,
                 max_pan_sps=700, max_tilt_sps=550, log=print):
        self.log=log
        self.pan_step,self.pan_dir=int(pan_step),int(pan_dir)
        self.tilt_step,self.tilt_dir=int(tilt_step),int(tilt_dir)
        self.max_pan_sps=int(max_pan_sps); self.max_tilt_sps=int(max_tilt_sps)
        self.pan=0; self.tilt=0; self.running=True
        self._lock=threading.Lock()
        self._gpio=None
        try:
            from gpiozero import DigitalOutputDevice
            self._gpio=DigitalOutputDevice
            self.ps=DigitalOutputDevice(self.pan_step)
            self.pd=DigitalOutputDevice(self.pan_dir)
            self.ts=DigitalOutputDevice(self.tilt_step)
            self.td=DigitalOutputDevice(self.tilt_dir)
            self.log("Raspberry Pi STEP/DIR pan-tilt controller connected")
        except Exception as exc:
            self.log(f"GPIO motor controller unavailable: {exc}")
            self._gpio=None
        self._thread=threading.Thread(target=self._loop,daemon=True,name="gpio-stepper")
        self._thread.start()

    def send(self, pan, tilt):
        with self._lock:
            self.pan=max(-self.max_pan_sps,min(self.max_pan_sps,int(pan)))
            self.tilt=max(-self.max_tilt_sps,min(self.max_tilt_sps,int(tilt)))

    def _pulse(self, step, direction, speed):
        if not self._gpio or speed <= 0: return
        direction.value = 1 if speed > 0 else 0
        step.on(); time.sleep(0.00035); step.off()

    def _loop(self):
        next_pan=next_tilt=time.perf_counter()
        while self.running:
            now=time.perf_counter()
            with self._lock: pan,tilt=self.pan,self.tilt
            if self._gpio and pan:
                interval=1.0/max(1,abs(pan))
                if now>=next_pan:
                    self.pd.value=1 if pan>0 else 0
                    self.ps.on(); time.sleep(min(0.00045,interval*0.35)); self.ps.off()
                    next_pan=now+interval
            if self._gpio and tilt:
                interval=1.0/max(1,abs(tilt))
                if now>=next_tilt:
                    self.td.value=1 if tilt>0 else 0
                    self.ts.on(); time.sleep(min(0.00045,interval*0.35)); self.ts.off()
                    next_tilt=now+interval
            time.sleep(0.0005)

    def stop(self):
        self.send(0,0)

    def close(self):
        self.running=False
        try:self.stop()
        except Exception:pass
        for name in ("ps","pd","ts","td"):
            try:getattr(self,name).close()
            except Exception:pass
