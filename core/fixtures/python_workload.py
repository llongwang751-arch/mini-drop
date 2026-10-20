"""Explicit toy workload for py-spy and memray attach regression."""
import math
import time

def python_fixture_hotspot(value):
    for _ in range(100000):
        value=math.sin(value)+math.sqrt(value*value+1.01)
    return value

retained=[]
sink=1.0
while True:
    sink=python_fixture_hotspot(sink)
    retained.append(bytearray(65536))
    if len(retained)>128:
        del retained[:64]
    time.sleep(.001)
