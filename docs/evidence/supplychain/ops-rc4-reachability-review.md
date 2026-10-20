# Ops rc4：指定高危项目的入口核查

2026-10-18，只读源码与已有实际镜像证据。没有启动容器、连接服务器、修改依赖、忽略扫描结果或调整严重性。以下覆盖指定的7个CVE，**不是61项High的完整处置**。当前安全门禁仍未通过。

实际镜像：`drop-ops:20261018.1-rc4`，ID `sha256:e8d95a64a714dba9e597dbf6ccae943ccb648a3610b9a45fbcdaa8445006c7d7`。

- 实际Grype报告仍为0 Critical / 61 High，SHA256 `f89e1616df4b9957c4ca72e51d912847b3b471faf865513f909b619a4c912f94`。
- 实际包/运行探针SHA256 `b7d36e3ab10b32d3fc4b0a1cd2885bdc3f7bd09df3f60e5078c131f8e8c7a5c5`：Python3.12.15、pure-Python psycopg3.3.6、系统libpq17.11、glibc2.41-12+deb13u4、libstdc++/libgcc14.2.0-19、krb5 1.21.3-5+deb13u1、Cyrus SASL2.1.28+dfsg1-9、zlib1.3.1。
- 实际UID/GID10001、CapEff0和NNP1已测。这些不是内存破坏免疫证明。包探针的network-none也不是生产Ops的网络条件；生产需要访问数据库/存储/告警传输。

## 实际证据与范围

本报告只审阅下文7个CVE的指定入口，不是全部61项High的完整可达性评估，也不声明这些High全部不可达。原始扫描和严重性保持不变，发布安全门禁仍失败。

- [实际Grype报告](security/drop-ops-20261018.1-rc4.cdx.grype.json)，SHA256 `f89e1616df4b9957c4ca72e51d912847b3b471faf865513f909b619a4c912f94`。
- [实际SBOM](drop-ops-20261018.1-rc4.cdx.json)，SHA256 `0c8de3208116d42912e8f2b1f4e7aafbf0973bb5d6e1fb1f04cd08982d11a4c2`。
- [实际包与运行探针](../full/ops-security-rc4-packages-2026-10-18.json)，SHA256 `b7d36e3ab10b32d3fc4b0a1cd2885bdc3f7bd09df3f60e5078c131f8e8c7a5c5`。
- [实际42项业务/SSE五阶段证据](../full/ops-security-rc4-runtime-2026-10-18.json)，SHA256 `0a4e3a15109365f8030c3e6a9464898243ae5e5e64dedb3a37801c2b2352bd0c`；该记录自身的scan-pending状态是历史时点，安全结论以另附实际Grype报告为准。
- 原始本地审阅文档保持原字节，SHA256 `ddaaca410aed0bbca02c61bdf4747a0b3208a7f9d631e8771a75c30a8b23b037`。

后续只读镜像入口探针目前仅准备，未执行；下文所有“待验证”仍保持待验证。未读取数据库凭据、未连接数据库，也未生成VEX、ignore或安全通过结论。

## glibc：CVE-2026-19499

必须到达`strfmon/strfmon_l`的右对齐货币格式化分支，并提供能完成padding、却容不下后续移动的输出缓冲。攻击者可控制格式/宽度是一种入口，但固定的易错调用模式也可能满足条件；不能简单以“未接受格式字符串”排除。Debian当前Trixie包仍标vulnerable，unstable fixed版为2.43-5，no-dsa不是未受影响结论。[Debian原始记录](https://security-tracker.debian.org/tracker/CVE-2026-19499)

上游2.41代码在padding后执行memmove，符合这一路径。Ops四个实际运行Python模块未发现该API调用；Python3.12.15的`locale.currency`用Python字符串和localeconv处理，也不等于调用strfmon。当前源码未找到从维护HTTP/SQL/S3入口到该函数的路径，但未完成所有动态库的调用图/符号导入审计。[glibc函数源码](https://raw.githubusercontent.com/bminor/glibc/glibc-2.41/stdlib/strfmon_l.c)、[CPython locale实现](https://raw.githubusercontent.com/python/cpython/v3.12.15/Lib/locale.py)

处置：等待Trixie正式修复或对该分支做经过验证的受控backport；在此之前保留扫描项。不要将sid的整套glibc直接混装到当前运行镜像。

## glibc：CVE-2026-5435

受影响的是打印DNS TSIG记录的废弃接口`ns_printrrf/ns_printrr/fp_nquery`。必须有调用者将相应记录送入这些格式化函数；单纯存在DNS查询不构成这条完整路径。Debian当前Trixie仍vulnerable，unstable fixed版2.43-3。[Debian记录](https://security-tracker.debian.org/tracker/CVE-2026-5435)

上游res_debug.c将fp_nquery定位为查询打印/调试函数；检查的2.41 res_send.c没有fp_nquery引用。Ops运行模块没有这些API调用。尚未对所有已装原生库/诊断工具完成可达性排除，不能把整个libc视作已修复。[上游查询打印代码](https://raw.githubusercontent.com/bminor/glibc/glibc-2.41/resolv/res_debug.c)、[上游查询传输代码](https://raw.githubusercontent.com/bminor/glibc/glibc-2.41/resolv/res_send.c)

## GCC：CVE-2026-95619

这是libstdc++ aligned operator-new在特定分配后端对极大size/alignment舍入溢出的缺陷。上游修复说明明确区分：采用posix_memalign的现代目标不进行该次舍入；C11 aligned_alloc/自带fallback和某些Windows路径需要修复。不能由`libgcc-s1`的同源包匹配直接推断到每个ABI实现。[GCC14官方修复](https://github.com/gcc-mirror/gcc/commit/200183d4ab5d57575f3dfebc6af3baf649910b9e)

当前镜像确实装有libstdc++6，Debian仍将14.2.0-19列为vulnerable，sid fixed14.4.0-3。Ops没有执行C++业务代码或提供C++ new接口；但已有运行探针只筛选libpq/SSL映射，没有测量所有C++依赖的加载状态，也未核实这个镜像实际operator-new调用的分配后端。因此“当前amd64分支不受影响”仍是待验证假设。[Debian记录](https://security-tracker.debian.org/tracker/CVE-2026-95619)

下一步应只读检查实际libstdc++动态符号/反汇编，确认所用分支，并记录服务真实imports/功能路径的库映射。不要通过ctypes直接调用可抛出C++异常的operator-new来探测，也不要在生产尝试巨大内存分配。

## GCC：CVE-2026-102010

必须有原生消费者实例化并调用`__gnu_pbds` binary-heap priority queue的`erase_if`。上游修复位于`include/ext/pb_ds/detail/binary_heap_/erase_fn_imps.hpp`：重分配后更新entry指针。这是模板/头文件代码，Python heapq或普通C分配并不自动触发它；升级共享libstdc++.so也不能保证修复过去已编进其他程序的模板实例。[GCC官方修复](https://github.com/gcc-mirror/gcc/commit/aaa8351f4d2e636f9680a1f0a8ebc2f0a60611e6)

Debian仍标当前14.2.0-19为vulnerable，sid14.4.0-3修复。Ops运行Python源码没有该C++模板用法，当前依赖中的潜在原生消费者尚未逐个审计；不能对整镜像做未受影响断言。若发现实例化消费者，应以修复后的头文件重建消费者，而非仅替换.so。[Debian记录](https://security-tracker.debian.org/tracker/CVE-2026-102010)

## Kerberos：CVE-2026-107778

上游修复检查KRB-CRED的tickets与ticket-info数组长度不匹配，避免`krb5_rd_cred`空指针解引用；对应常见入口是接收转发凭据的GSS acceptor。Ops数据库连接走libpq前端，其GSS路径调用`gss_init_sec_context`，不是GSS acceptor。当前维护HTTP服务也没有SPNEGO/GSS服务端接入。因此不能把“远端PG可回包”直接等同于该acceptor缺陷的入口。[MIT上游修复](https://github.com/krb5/krb5/commit/48afa9abb89ab2176bb20624d87d010b9984fc08)、[libpq17.11认证源码](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/interfaces/libpq/fe-auth.c)

实际镜像仍带受影响版本。已有源码/部署模板明确配置SCRAM初始化，但这不是对现有数据卷pg_hba生效状态的现场证明；没有读取实际DSN或凭据。建议由唯一队列做只读`pg_hba_file_rules`认证方式检查，再验证`require_auth=scram-sha-256`与`gssencmode=disable`的受控连接。它们是官方支持的收窄项，当前尚未替业务配置启用。[官方连接参数](https://www.postgresql.org/docs/17/libpq-connect.html)

## Cyrus SASL：CVE-2026-107161

需要加载Cyrus DIGEST-MD5插件并连到能够返回恶意challenge的IMAP/SMTP/LDAP/HTTP-Digest服务。问题在`plugins/digestmd5.c`对转义后的realm/nonce长度计算，不是所有名为SASL的协议都走该代码。PostgreSQL17只实现SCRAM-SHA-256及PLUS，libpq源码直接选择自己的pg_scram_mech，不能把它和Cyrus DIGEST-MD5混同。[Cyrus上游问题/修补提案](https://github.com/cyrusimap/cyrus-sasl/pull/892)、[PG17 SASL机制](https://www.postgresql.org/docs/17/sasl-authentication.html)、[libpq SCRAM源码](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/interfaces/libpq/fe-auth-scram.c)

当前实际dpkg inventory只含libsasl2-2和libsasl2-modules-db，缺libsasl2-modules；实际SBOM记录libsasldb.so而未发现digestmd5文件组件。Debian官方filelist将libdigestmd5.so归于未安装的libsasl2-modules。这个证据比“没有代码直接import sasl”更强，但SBOM的缺失仍不能替代一次对实际镜像插件目录/替代路径的只读核验。[Debian插件文件清单](https://packages.debian.org/trixie/amd64/libsasl2-modules/filelist)

Debian仍将2.1.28+dfsg1-9标vulnerable；sid2.1.28+dfsg1-12修复，Trixie记载拟通过point release修复。提案PR关闭也不能被当作当前Debian已修复证明。[Debian状态](https://security-tracker.debian.org/tracker/CVE-2026-107161)

## zlib：CVE-2026-85091

当前Debian状态未修复/有backport判断分歧，这里不另行认定false positive。指定触发链需要非阻塞gzwrite发生write-stall，随后调用gzprintf/gzvprintf并使用其外部缓冲。Python3.12.15的zlib模块采用z_stream/deflate/inflate；gzip写入采用zlib.compressobj配合Python fileobj；urllib3 2.8 gzip读取采用zlib.decompressobj。上述路径不调用gzprintf/gzvprintf，也不是C gzFile写API。Ops四个生产Python模块没有直接gzip/zlib或ctypes gzFile调用；测试中的gzip输入是显式导入夹具。[Debian状态](https://security-tracker.debian.org/tracker/CVE-2026-85091)、[CPython zlib绑定](https://raw.githubusercontent.com/python/cpython/v3.12.15/Modules/zlibmodule.c)、[CPython gzip](https://raw.githubusercontent.com/python/cpython/v3.12.15/Lib/gzip.py)、[urllib3 2.8 decoder](https://raw.githubusercontent.com/urllib3/urllib3/2.8.0/src/urllib3/response.py)

这些是当前Python入口的源码证据，不是证明整套zlib已修复，也没有排除其他原生消费者。需要对实际镜像zlib扩展的未定义符号与原生依赖补查，不能因能处理gzip文件就认定到达或排除gzprintf缺陷。

## 发布判断与下一步

扫描原始0/61保留；不生成ignore、改severity或把no-dsa改写成安全通过。余下util-linux/ncurses/ACL/Perl等High仍未在本报告中完成审阅。当前最多能说“指定路径有未观察到入口或强候选排除证据”，不能说“61项均无影响”。

最小后续只读验证应固定该镜像ID，UID10001、capless、只读、network-none、受限内存/CPU，只返回布尔、版本和文件SHA：确认DIGEST-MD5插件实际缺失、服务功能imports后的全部动态库集合、zlib扩展真实导入符号和libstdc++分配后端。数据库另在受控网络中只查认证方式，不输出DSN、密码或哈希。所有新image/配置/backport仍须重新42项业务、SSE五阶段和原始SBOM扫描。
