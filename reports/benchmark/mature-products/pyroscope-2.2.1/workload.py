import hashlib, time
import pyroscope
pyroscope.configure(application_name="mini-drop.same-input.cpu",server_address="http://127.0.0.1:4040",tags={"suite":"mature-product-comparison","case":"CPU_HOTSPOT"})
def order_compute(seed):
    payload=b"mini-drop"+str(seed).encode()
    for _ in range(4000): payload=hashlib.sha256(payload).digest()
    return payload
end=time.time()+20; i=0
while time.time()<end:
    order_compute(i); i+=1
print({"iterations":i})
