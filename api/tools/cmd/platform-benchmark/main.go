// platform-benchmark 使用可销毁的 PostgreSQL 合成数据测量真实 HTTP 路由。
// 它拒绝连接名为 drop_perf_fixture 以外的数据库，避免压测修改业务数据。
// Package main：使用可销毁合成数据对真实路由、分页、索引、限流和 SSE 做基准验证。
// 该工具硬性限制数据库名称，不能对生产数据运行；工具输出的性能指标只对应本次夹具负载。
package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/httpapi"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type report struct {
	StartedAt   string                                `json:"started_at"`
	Environment map[string]any                        `json:"environment"`
	Seed        map[string]any                        `json:"seed"`
	Plans       map[string]map[string]json.RawMessage `json:"explain_analyze"`
	HTTP        map[string]any                        `json:"http"`
	SSE         map[string]any                        `json:"sse"`
	Passed      bool                                  `json:"passed"`
	Error       string                                `json:"error,omitempty"`
}

type store struct{}

func (store) PutText(context.Context, string, string) (int64, string, error) {
	return 0, "", errors.New("not used in API persistence benchmark")
}
func (store) PutBytes(context.Context, string, []byte, string) (int64, string, error) {
	return 0, "", errors.New("not used in API persistence benchmark")
}
func (store) PresignPut(context.Context, string) (string, error) {
	return "https://benchmark.invalid/not-used", nil
}
func (store) PresignGet(context.Context, string) (string, error) { return "", errors.New("not used") }
func (store) Ready(context.Context) error                        { return nil }

func fail(err error) {
	if err != nil {
		panic(err)
	}
}
func exec(db *sql.DB, q string) { _, err := db.Exec(q); fail(err) }
func summary(values []float64, failures int) map[string]any {
	sort.Float64s(values)
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	p := func(n float64) float64 {
		return math.Round(values[int(math.Ceil(float64(len(values))*n))-1]*1000) / 1000
	}
	return map[string]any{"requests": len(values), "errors": failures, "mean_ms": sum / float64(len(values)), "p50_ms": p(.5), "p95_ms": p(.95), "p99_ms": p(.99), "max_ms": values[len(values)-1]}
}

func main() {
	r := report{StartedAt: time.Now().UTC().Format(time.RFC3339), Plans: map[string]map[string]json.RawMessage{}, HTTP: map[string]any{}, SSE: map[string]any{}, Environment: map[string]any{"go": runtime.Version(), "goarch": runtime.GOARCH, "goos": runtime.GOOS, "gomaxprocs": runtime.GOMAXPROCS(0), "isolation": "Fresh dedicated PostgreSQL database drop_perf_fixture; synthetic records; real HTTP router via loopback TCP. No production database, S3 transfer, profiler, TLS, Nginx or WAN throughput is measured."}}
	checkpoint := func() {
		out := os.Getenv("DROP_BENCH_OUTPUT")
		if out == "" {
			out = "/fixtures/benchmark.json"
		}
		b, err := json.MarshalIndent(r, "", "  ")
		fail(err)
		fail(os.WriteFile(out, append(b, '\n'), 0644))
	}
	defer func() {
		if e := recover(); e != nil {
			r.Error = fmt.Sprint(e)
		}
		out := os.Getenv("DROP_BENCH_OUTPUT")
		if out == "" {
			out = "/fixtures/benchmark.json"
		}
		b, _ := json.MarshalIndent(r, "", "  ")
		if err := os.WriteFile(out, append(b, '\n'), 0644); err != nil {
			fmt.Fprintln(os.Stderr, "report write failed")
			os.Exit(2)
		}
		if !r.Passed {
			fmt.Fprintln(os.Stderr, "benchmark failed:", r.Error)
			os.Exit(1)
		}
		fmt.Println("isolated benchmark passed; report:", out)
	}()
	if os.Getenv("DROP_BENCH_ALLOW_DISPOSABLE") != "1" {
		panic("Explicit disposable fixture opt-in required")
	}
	dsn := os.Getenv("DROP_BENCH_DATABASE_URL")
	if dsn == "" {
		panic("DROP_BENCH_DATABASE_URL required")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent), TranslateError: true})
	fail(err)
	sqlDB, err := db.DB()
	fail(err)
	defer sqlDB.Close()
	sqlDB.SetMaxOpenConns(12)
	sqlDB.SetMaxIdleConns(3)
	var name string
	fail(sqlDB.QueryRow("SELECT current_database()").Scan(&name))
	if name != "drop_perf_fixture" {
		panic("Refusing a non-fixture database")
	}
	var exists bool
	fail(sqlDB.QueryRow("SELECT to_regclass('public.tasks') IS NOT NULL").Scan(&exists))
	if exists {
		panic("Fixture must be a new empty database; never seed an existing database")
	}
	root := os.Getenv("DROP_BENCH_FIXTURES")
	if root == "" {
		root = "/fixtures"
	}
	files, err := filepath.Glob(filepath.Join(root, "db", "migrations", "*.sql"))
	fail(err)
	sort.Strings(files)
	if len(files) < 4 {
		panic("Expected current migrations")
	}
	var indexMigration []byte
	for _, f := range files {
		b, e := os.ReadFile(f)
		fail(e)
		if filepath.Base(f) == "004_task_list_index.sql" {
			indexMigration = b
			continue
		}
		exec(sqlDB, string(b))
	}
	if len(indexMigration) == 0 {
		panic("Expected the reviewed task-list index migration")
	}
	seedStart := time.Now()
	exec(sqlDB, `INSERT INTO users(id,username,display_name,role) SELECT 'u'||g,'fixture-'||g,'Synthetic benchmark '||g,'operator' FROM generate_series(0,999) g;
INSERT INTO groups(id,name) SELECT 'g'||g,'Synthetic benchmark group '||g FROM generate_series(0,99) g;
INSERT INTO group_members(group_id,user_id,role) SELECT 'g'||(g%100),'u'||g,'operator' FROM generate_series(0,999) g;
INSERT INTO agents(id,hostname,version,platform,capabilities,resources) VALUES('benchmark-agent','synthetic','benchmark','linux','["PROC_CPU"]','{"processes":[{"pid":7,"uid":10001,"start_time_ticks":123}],"target_policy":{"host_processes":true,"allowed_uids":[10001]}}');
INSERT INTO tasks(name,kind,target_agent_id,creator_id,request_id,group_id,visibility,collection_status,analysis_status,created_at,deleted_at)
SELECT 'Synthetic Task '||g,CASE WHEN g%4=0 THEN 'PERF_CPU' ELSE 'PROC_CPU' END,'benchmark-agent','u'||(g%1000),'synthetic-'||g,'g'||(g%100),CASE WHEN g%3=0 THEN 'group' ELSE 'private' END,'COLLECTED','SUCCESS',now()-g*interval '1 second',CASE WHEN g%100=0 THEN now() ELSE NULL END FROM generate_series(1,1000000) g;
INSERT INTO task_shares(task_id,user_id,permission,created_by) SELECT g,'u17','read','u0' FROM generate_series(1,990001,10000) g;
INSERT INTO task_shares(task_id,group_id,permission,created_by) SELECT g,'g17','read','u0' FROM generate_series(5001,995001,10000) g;
ANALYZE;`)
	var count int64
	fail(sqlDB.QueryRow("SELECT count(*) FROM tasks").Scan(&count))
	if count != 1000000 {
		panic("Million-row seed count differs")
	}
	var pgVersion string
	fail(sqlDB.QueryRow("SELECT version()").Scan(&pgVersion))
	r.Environment["postgres"] = pgVersion
	var disk int64
	fail(sqlDB.QueryRow("SELECT pg_database_size(current_database())").Scan(&disk))
	r.Seed = map[string]any{"tasks": count, "users": 1000, "groups": 100, "shares": 200, "soft_deleted_percent": 1, "seed_seconds": time.Since(seedStart).Seconds(), "database_bytes_before_candidate_indexes": disk, "fixture_notice": "Synthetic records, explicitly unrelated to real measured profiles."}
	checkpoint()
	fmt.Println("million synthetic tasks persisted; starting real EXPLAIN ANALYZE")
	scope := `tasks.deleted_at IS NULL AND (tasks.creator_id='u17' OR (tasks.visibility='group' AND EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=tasks.group_id AND gm.user_id='u17')) OR EXISTS(SELECT 1 FROM task_shares sh WHERE sh.task_id=tasks.id AND (sh.user_id='u17' OR sh.group_id IN (SELECT group_id FROM group_members WHERE user_id='u17'))))`
	queries := map[string]string{
		"platform_first_page":        `SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 20`,
		"platform_deep_offset_20000": `SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET 20000`,
		"owner_first_page":           `SELECT * FROM tasks WHERE deleted_at IS NULL AND creator_id='u17' ORDER BY created_at DESC,id DESC LIMIT 20`,
		"group_first_page":           `SELECT * FROM tasks WHERE deleted_at IS NULL AND visibility='group' AND group_id='g17' ORDER BY created_at DESC,id DESC LIMIT 20`,
		"published_scope_first_page": `SELECT * FROM tasks WHERE ` + scope + ` ORDER BY created_at DESC,id DESC LIMIT 20`,
		"published_scope_count":      `SELECT count(*) FROM tasks WHERE ` + scope,
		"platform_count":             `SELECT count(*) FROM tasks WHERE deleted_at IS NULL`,
		"kind_page":                  `SELECT * FROM tasks WHERE deleted_at IS NULL AND kind='PERF_CPU' ORDER BY created_at DESC,id DESC LIMIT 20`,
	}
	collectPlans := func(stage string) {
		r.Plans[stage] = map[string]json.RawMessage{}
		names := make([]string, 0, len(queries))
		for n := range queries {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			var raw []byte
			fail(sqlDB.QueryRow("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + queries[n]).Scan(&raw))
			r.Plans[stage][n] = raw
			checkpoint()
			fmt.Println("completed plan:", stage, n)
		}
	}
	collectPlans("before_candidate_indexes")
	// 仅向这份可销毁的百万行夹具应用已审阅的最小迁移。
	// 重复执行两次，以验证迁移可安全重放并保留已有数据。
	exec(sqlDB, string(indexMigration))
	exec(sqlDB, string(indexMigration))
	var afterCount, softDeleted, schemaVersion int64
	fail(sqlDB.QueryRow("SELECT count(*),count(*) FILTER(WHERE deleted_at IS NOT NULL) FROM tasks").Scan(&afterCount, &softDeleted))
	fail(sqlDB.QueryRow("SELECT max(version) FROM schema_migrations").Scan(&schemaVersion))
	var valid bool
	var definition string
	fail(sqlDB.QueryRow("SELECT indisvalid AND indisready,pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid='idx_tasks_active_created'::regclass").Scan(&valid, &definition))
	if afterCount != 1000000 || softDeleted != 10000 || schemaVersion != 4 || !valid {
		panic("Index migration replay did not preserve data or produce a valid index")
	}
	exec(sqlDB, "ANALYZE")
	collectPlans("after_candidate_indexes")
	r.Seed["index_migration"] = map[string]any{"file": "db/migrations/004_task_list_index.sql", "applied_twice": true, "schema_version": schemaVersion, "tasks_preserved": afterCount, "soft_deleted_preserved": softDeleted, "index_valid_and_ready": valid, "index_definition": definition}
	fail(sqlDB.QueryRow("SELECT pg_database_size(current_database())").Scan(&disk))
	r.Seed["database_bytes_after_candidate_indexes"] = disk
	kindBytes, err := os.ReadFile(filepath.Join(root, "contracts", "task-kinds.json"))
	fail(err)
	fail(domain.LoadKinds(kindBytes))
	var kinds []map[string]any
	fail(json.Unmarshal(kindBytes, &kinds))
	repo := repository.New(db)
	accounts := service.NewAccounts(repo)
	password := "Synthetic-benchmark-only-123!"
	fail(accounts.Bootstrap(context.Background(), "benchmark-admin", password))
	tasks := &service.Tasks{Repo: repo, Storage: store{}}
	s := &httpapi.Server{Tasks: tasks, Repo: repo, Accounts: accounts, Kinds: kinds, PublicURL: "http://127.0.0.1"}
	slog.SetDefault(slog.New(slog.NewTextHandler(io.Discard, nil)))
	server := httptest.NewServer(s.Router())
	defer server.Close()
	client := &http.Client{Timeout: 30 * time.Second}
	login, _ := json.Marshal(map[string]string{"username": "benchmark-admin", "password": password})
	resp, err := client.Post(server.URL+"/api/v1/auth/login", "application/json", bytes.NewReader(login))
	fail(err)
	if resp.StatusCode != 200 {
		panic("Fixture login failed")
	}
	var auth struct {
		Data struct {
			CSRF string `json:"csrf_token"`
		}
	}
	fail(json.NewDecoder(resp.Body).Decode(&auth))
	resp.Body.Close()
	cookies := resp.Cookies()
	if len(cookies) == 0 || auth.Data.CSRF == "" {
		panic("Missing fixture login state")
	}
	request := func(method, path string, body []byte, key string) (float64, int) {
		start := time.Now()
		req, e := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		fail(e)
		for _, c := range cookies {
			req.AddCookie(c)
		}
		if method != "GET" {
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("X-CSRF-Token", auth.Data.CSRF)
			req.Header.Set("Idempotency-Key", "synthetic-benchmark-"+key)
		}
		res, e := client.Do(req)
		if e != nil {
			return time.Since(start).Seconds() * 1000, 0
		}
		io.Copy(io.Discard, res.Body)
		res.Body.Close()
		return time.Since(start).Seconds() * 1000, res.StatusCode
	}
	runHTTP := func(name, method, path string, total, concurrency int, create bool) {
		var mu sync.Mutex
		values := []float64{}
		failures := 0
		statuses := map[int]int{}
		if create {
			exec(sqlDB, "UPDATE agents SET last_seen_at=now() WHERE id='benchmark-agent'")
		}
		work := make(chan int)
		var wg sync.WaitGroup
		for worker := 0; worker < concurrency; worker++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for i := range work {
					var body []byte
					if create {
						body, _ = json.Marshal(map[string]any{"name": fmt.Sprintf("Synthetic HTTP %d", i), "kind": "PROC_CPU", "target_agent_id": "benchmark-agent", "parameters": map[string]any{"pid": 7, "duration_seconds": 1, "frequency_hz": 2}})
					}
					v, status := request(method, path, body, fmt.Sprintf("%s-%d", name, i))
					if create && status == 201 {
						_, e := sqlDB.Exec("UPDATE tasks SET collection_status='COLLECTED',analysis_status='SUCCESS' WHERE id>1000000")
						fail(e)
					}
					mu.Lock()
					values = append(values, v)
					statuses[status]++
					if status < 200 || status >= 300 {
						failures++
					}
					mu.Unlock()
				}
			}()
		}
		start := time.Now()
		for i := 0; i < total; i++ {
			work <- i
		}
		close(work)
		wg.Wait()
		result := summary(values, failures)
		result["concurrency"] = concurrency
		result["elapsed_seconds"] = time.Since(start).Seconds()
		result["requests_per_second"] = float64(total) / time.Since(start).Seconds()
		result["status_counts"] = statuses
		unexpected := failures
		if create && concurrency > 1 {
			// 生产路由有意一次只准入一个大型创建请求；
			// 返回 429 是受控内存占用的准入约定，不能将其误算为服务崩溃。
			unexpected -= statuses[http.StatusTooManyRequests]
			result["rate_limited_requests"] = statuses[http.StatusTooManyRequests]
			result["admission_contract"] = "Concurrent creates may return 429; one create-body slot protects the bounded API process. Accepted-request latency is not a sampling or upload throughput measurement."
		}
		result["unexpected_errors"] = unexpected
		r.HTTP[name] = result
		checkpoint()
		if unexpected != 0 || (create && statuses[http.StatusCreated] == 0) {
			panic("HTTP benchmark had errors: " + name)
		}
	}
	runHTTP("create_task", "POST", "/api/v1/tasks", 16, 1, true)
	runHTTP("create_admission_burst", "POST", "/api/v1/tasks", 8, 4, true)
	runHTTP("list_first_page", "GET", "/api/v1/tasks?page=1&page_size=20", 40, 4, false)
	runHTTP("list_deep_page_1001", "GET", "/api/v1/tasks?page=1001&page_size=20", 20, 4, false)
	// 保持真实 TCP 流式响应连接，在一个数据库事件提交后，
	// 验证 20 个分别认证的 SSE 流都能收到该事件。
	const streamCount = 20
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	arrivals := make(chan float64, streamCount)
	ready := make(chan bool, streamCount)
	startConnect := time.Now()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	for i := 0; i < streamCount; i++ {
		go func() {
			req, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"/api/v1/tasks/17/events/stream", nil)
			for _, c := range cookies {
				req.AddCookie(c)
			}
			res, e := client.Do(req)
			if e != nil || res.StatusCode != 200 {
				ready <- false
				return
			}
			reader := bufio.NewReader(res.Body)
			line, e := reader.ReadString('\n')
			if e != nil || !strings.HasPrefix(line, ": connected") {
				ready <- false
				res.Body.Close()
				return
			}
			ready <- true
			for {
				line, e = reader.ReadString('\n')
				if e != nil {
					res.Body.Close()
					return
				}
				if strings.HasPrefix(line, "event: task_status") {
					arrivals <- float64(time.Now().UnixNano()) / 1e6
					res.Body.Close()
					return
				}
			}
		}()
	}
	for i := 0; i < streamCount; i++ {
		select {
		case ok := <-ready:
			if !ok {
				panic("SSE connection failed")
			}
		case <-time.After(30 * time.Second):
			panic("SSE connection timeout")
		}
	}
	connectMs := time.Since(startConnect).Seconds() * 1000
	runtime.ReadMemStats(&after)
	time.Sleep(1500 * time.Millisecond)
	inserted := float64(time.Now().UnixNano()) / 1e6
	exec(sqlDB, `INSERT INTO task_events(task_id,type,source,payload) VALUES(17,'BENCHMARK_EVENT','synthetic-fixture','{}')`)
	latencies := []float64{}
	for i := 0; i < streamCount; i++ {
		select {
		case v := <-arrivals:
			latencies = append(latencies, v-inserted)
		case <-time.After(30 * time.Second):
			panic("SSE event delivery timeout")
		}
	}
	r.SSE = summary(latencies, 0)
	r.SSE["connections"] = streamCount
	r.SSE["all_connected_ms"] = connectMs
	r.SSE["heap_alloc_delta_bytes"] = int64(after.HeapAlloc) - int64(before.HeapAlloc)
	r.SSE["idle_hold_seconds"] = 1.5
	r.SSE["delivery_measurement"] = "Actual committed PostgreSQL event to all streaming readers; endpoint polls at one-second intervals. Loopback only; not an Internet or production capacity claim."
	r.Passed = true
}
