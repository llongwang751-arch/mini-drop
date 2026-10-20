"""通过真实 Saber SQLite FTS5 源码执行有界的合成查询流量。

不使用外部引擎、用户语料、持久数据库或凭据，也不启动完整应用；
drop_agent 的命名空间可看到此进程，用于真实 py-spy/memray 采集。
"""
import argparse
import time

from saber_component import execute_case, load_component, query_cases, synthetic_database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/opt/drop-saber")
    parser.add_argument("--iterations", type=int, default=0, help="0 runs until container shutdown; positive is a local smoke")
    args = parser.parse_args()
    if args.iterations < 0 or args.iterations > 1000:
        raise SystemExit("INVALID_ITERATION_BUDGET")
    module = load_component(args.source)
    conn = synthetic_database(module)
    index = 0
    try:
        while not args.iterations or index < args.iterations:
            start = time.monotonic()
            for case in query_cases(1729 + index):
                execute_case(module, conn, case)
            index += 1
            time.sleep(max(0.0, 0.35 - (time.monotonic() - start)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
