"""Controlled CPU load through the actual CRM palette service, synthetic inputs.

The separate original CRM HTTP regression covers route authorization/rate limits.
This workload does not connect to a real tenant, database, model or messaging API.
"""
import time
from crm.services.brand_palette import derive


def main():
    index = 0
    while True:
        start = time.monotonic()
        for _ in range(24):
            # Novel valid seeds model first-time previews; no intentional defect.
            derive(f'#{((index + 1) * 2654435761) & 0xffffff:06x}')
            index += 1
        time.sleep(max(0., .2 - (time.monotonic() - start)))


if __name__ == '__main__':
    main()
