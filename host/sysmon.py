"""Samples the laptop's state for the monitor screen: CPU (total, per core,
history), memory, disk usage of /home, network rates, battery, temperature."""
import time

import psutil


class Monitor:
    def __init__(self):
        self.hist = [0.0] * 120
        self.net = psutil.net_io_counters()
        self.t = time.time()
        self.rate = (0.0, 0.0)
        psutil.cpu_percent(percpu=True)

    def sample(self):
        self.cores = psutil.cpu_percent(percpu=True)
        self.cpu = sum(self.cores) / len(self.cores)
        self.hist = self.hist[1:] + [self.cpu]
        self.mem = psutil.virtual_memory()
        self.disk = psutil.disk_usage("/home")
        now, net = time.time(), psutil.net_io_counters()
        dt = max(now - self.t, 1e-3)
        self.rate = ((net.bytes_recv - self.net.bytes_recv) / dt,
                     (net.bytes_sent - self.net.bytes_sent) / dt)
        self.net, self.t = net, now
        self.bat = psutil.sensors_battery()
        temps = psutil.sensors_temperatures()
        vals = [t.current for group in temps.values() for t in group if t.current]
        self.temp = max(vals) if vals else None
