"""以合成输入驱动真实 CRM 配色服务，形成受控 CPU 负载。

路由授权和限流由另一组原始 CRM HTTP 回归覆盖；本负载不连接真实租户、数据库、模型或消息接口。
"""
import time
from crm.services.brand_palette import derive


def main():
    index = 0
    while True:
        start = time.monotonic()
        for _ in range(24):
            # 使用合法的新 seed 模拟首次配色预览，不植入人为缺陷。
            derive(f'#{((index + 1) * 2654435761) & 0xffffff:06x}')
            index += 1
        time.sleep(max(0., .2 - (time.monotonic() - start)))


if __name__ == '__main__':
    main()
