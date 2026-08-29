package taskkind

// Kind is the browser-facing and API-enforced collector contract. The C++
// Agent still reports runtime availability through capabilities; a task must
// satisfy both this policy catalog and the selected Agent's live capability.
type Kind struct {
	ProfilerType       uint32 `json:"-"`
	ID                 string `json:"id"`
	Label              string `json:"label"`
	ResultLabel        string `json:"result_label"`
	Description        string `json:"description"`
	Color              string `json:"color"`
	DefaultDurationSec int    `json:"default_duration_sec"`
	MaxDurationSec     int    `json:"max_duration_sec"`
	DefaultSampleRate  int    `json:"default_sample_rate"`
	MaxSampleRate      int    `json:"max_sample_rate"`
	Flamegraph         bool   `json:"flamegraph"`
}

var catalog = []Kind{
	{ProfilerType: 0, ID: "perf_cpu", Label: "CPU 火焰图", ResultLabel: "交互式 CPU 火焰图 + TopN 热点", Description: "使用 perf 采样目标进程调用栈，定位 CPU 热点、锁竞争和异常调用路径。", Color: "blue", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 99, MaxSampleRate: 999, Flamegraph: true},
	{ProfilerType: 3, ID: "pyspy", Label: "Python 火焰图", ResultLabel: "Python 调用栈火焰图", Description: "使用 py-spy 采样 Python 进程，无需修改应用代码。", Color: "purple", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 99, MaxSampleRate: 999, Flamegraph: true},
	{ProfilerType: 7, ID: "continuous_perf", Label: "持续火焰图", ResultLabel: "按窗口切分的火焰图 + 趋势", Description: "周期采集多个时间窗口，观察热点随时间变化。", Color: "cyan", DefaultDurationSec: 60, MaxDurationSec: 600, DefaultSampleRate: 49, MaxSampleRate: 999, Flamegraph: true},
	{ProfilerType: 1, ID: "java_async", Label: "Java 火焰图", ResultLabel: "async-profiler Java 火焰图", Description: "采集 JVM 进程 CPU 调用栈并生成可浏览火焰图。", Color: "magenta", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 99, MaxSampleRate: 999, Flamegraph: true},
	{ProfilerType: 2, ID: "go_pprof", Label: "Go pprof", ResultLabel: "Go pprof 数据与火焰图", Description: "抓取 Go pprof CPU Profile，并在分析工具链中生成结果。", Color: "geekblue", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 99, MaxSampleRate: 999, Flamegraph: true},
	{ProfilerType: 4, ID: "ebpf_io", Label: "I/O 延迟图", ResultLabel: "eBPF I/O 延迟直方图", Description: "使用 eBPF/bpftrace 观察块设备延迟分布。", Color: "green", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 11, MaxSampleRate: 999, Flamegraph: false},
	{ProfilerType: 5, ID: "memory_smaps", Label: "内存趋势", ResultLabel: "RSS / PSS / Swap 趋势图", Description: "采样进程 smaps，定位内存增长、Swap 和疑似泄漏。", Color: "orange", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 11, MaxSampleRate: 999, Flamegraph: false},
	{ProfilerType: 6, ID: "sys_metrics", Label: "系统指标", ResultLabel: "CPU / 负载 / 线程 / FD / 网络多维图", Description: "低开销采集主机和进程指标。", Color: "gold", DefaultDurationSec: 15, MaxDurationSec: 300, DefaultSampleRate: 11, MaxSampleRate: 999, Flamegraph: false},
}

func List() []Kind {
	items := make([]Kind, len(catalog))
	copy(items, catalog)
	return items
}

func Lookup(id string) (Kind, bool) {
	for _, item := range catalog {
		if item.ID == id {
			return item, true
		}
	}
	return Kind{}, false
}
