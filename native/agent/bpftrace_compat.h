// bpftrace 0.14 on Ubuntu 22.04 generates tracepoint declarations that use
// kernel typedef names without defining them when the host BTF cannot be read
// (for example Docker Desktop/WSL2).  Supplying these ABI-compatible aliases
// lets the tracepoint program compile while native Linux with valid BTF keeps
// using the same stable tracepoint fields.
typedef unsigned int dev_t;
typedef unsigned long long sector_t;
