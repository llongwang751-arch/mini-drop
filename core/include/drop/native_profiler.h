#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Embed in an explicitly instrumented application. Returns 0 on success.
 * The directory must be owned by the application UID, mode 0700. The caller
 * selects one additional controller UID; its own UID is always permitted.
 * Schema2 keeps scratch in this directory and can require a bounded filesystem;
 * place it on the same bounded spool as the Agent RAW receive directory.
 * Link/preload libprofiler, libtcmalloc or profiling-enabled libjemalloc as
 * appropriate. No allocator is injected or replaced by this helper. */
int drop_native_profiler_start(const char *directory, uint32_t controller_uid);
void drop_native_profiler_stop(void);
#ifdef __cplusplus
}
#endif
