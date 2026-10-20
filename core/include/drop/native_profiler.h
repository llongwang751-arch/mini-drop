#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* 由显式接入协议的应用调用，成功返回0。
 * 控制目录必须归应用UID持有且权限为0700；调用者可以额外授权一个控制器UID，
 * 应用自己的UID始终被允许。Schema2把临时文件也限制在此目录，且可要求容量受限的
 * 文件系统，因此应与Agent的RAW接收目录放在同一个有界spool上。
 * 应用按需自行链接或预加载libprofiler、libtcmalloc，或启用profiling的libjemalloc；
 * 此辅助库不会给目标注入或替换分配器。 */
int drop_native_profiler_start(const char *directory, uint32_t controller_uid);
void drop_native_profiler_stop(void);
#ifdef __cplusplus
}
#endif
