"""供 py-spy 与 memray 附加回归使用的明确合成业务负载，不代表生产性能。"""
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
