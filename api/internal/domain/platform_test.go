// 本文件验证时区与夏令时、组合 DAG 的环和缺失依赖、必选及可选节点的聚合语义。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package domain

import (
	"testing"
	"time"
)

func TestCronTimezoneAndDST(t *testing.T) {
	tests := []struct{ expr, zone, after, want string }{
		{"0 9 * * 1-5", "Asia/Shanghai", "2026-10-16T02:00:00Z", "2026-10-19T01:00:00Z"},
		{"30 2 * * *", "America/New_York", "2026-03-08T05:00:00Z", "2026-03-09T06:30:00Z"},
		{"30 1 * * *", "America/New_York", "2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"},
	}
	for _, tt := range tests {
		after, _ := time.Parse(time.RFC3339, tt.after)
		got, err := NextSchedule(tt.expr, tt.zone, 0, after)
		if err != nil || got.Format(time.RFC3339) != tt.want {
			t.Errorf("%s %s: got %v %v want %s", tt.expr, tt.zone, got, err, tt.want)
		}
	}
	if _, err := NextSchedule("* * * * *", "Mars/City", 0, time.Now()); err == nil {
		t.Fatal("unknown timezone accepted")
	}
	if _, err := NextSchedule("@every 1s", "UTC", 0, time.Now()); err == nil {
		t.Fatal("nonstandard cron accepted")
	}
}

func TestCompositeDAGRejectsCycleAndMissingDependency(t *testing.T) {
	c := CreateComposite{Name: "test", DeadlineSeconds: 60, Nodes: []CompositeNodeInput{{Key: "cpu", Required: true, Task: CreateTask{Kind: "PERF_CPU"}}, {Key: "heap", DependsOn: []string{"cpu"}, Task: CreateTask{Kind: "PPROF_HEAP"}}}}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	c.Nodes[0].DependsOn = []string{"heap"}
	if c.Validate() == nil {
		t.Fatal("cycle accepted")
	}
	c.Nodes[0].DependsOn = []string{"missing"}
	if c.Validate() == nil {
		t.Fatal("missing dependency accepted")
	}
}

func TestCompositeAggregateRequiredAndOptional(t *testing.T) {
	nodes := []CompositeNode{{Key: "a", Required: true, Status: "SUCCESS"}, {Key: "b", Required: false, Status: "FAILED"}}
	if status, _ := AggregateComposite(nodes, true); status != "PARTIAL_SUCCESS" {
		t.Fatal(status)
	}
	if status, _ := AggregateComposite(nodes, false); status != "FAILED" {
		t.Fatal(status)
	}
	nodes[1].Required = true
	if status, _ := AggregateComposite(nodes, true); status != "FAILED" {
		t.Fatal(status)
	}
	nodes[1].Status = "WAITING"
	if status, _ := AggregateComposite(nodes, true); status != "RUNNING" {
		t.Fatal(status)
	}
}
