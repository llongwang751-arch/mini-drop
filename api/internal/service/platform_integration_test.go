// 本文件验证扩展任务生命周期、真实签名 OIDC 回调、模型请求同意与脱敏，以及管理恢复审计。
// 数据库使用隔离 schema；模拟对象存储或模型响应不等于生产服务实测。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
	jose "github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

type fixtureStore struct {
	mu        sync.Mutex
	objects   map[string][]byte
	downloads int
}

func (f *fixtureStore) PutText(ctx context.Context, key, text string) (int64, string, error) {
	return f.PutBytes(ctx, key, []byte(text), "text/plain")
}
func (f *fixtureStore) PutBytes(_ context.Context, key string, b []byte, _ string) (int64, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.objects == nil {
		f.objects = map[string][]byte{}
	}
	f.objects[key] = append([]byte(nil), b...)
	sum := sha256.Sum256(b)
	return int64(len(b)), hex.EncodeToString(sum[:]), nil
}
func (f *fixtureStore) PresignPut(context.Context, string) (string, error) {
	return "https://fixture.invalid/task-scoped-put", nil
}
func (f *fixtureStore) PresignGet(context.Context, string) (string, error) {
	f.downloads++
	return "https://fixture.invalid/task-scoped-get", nil
}
func (f *fixtureStore) Ready(context.Context) error { return nil }

func TestPostgresExtendedTaskLifecycle(t *testing.T) {
	db := testutil.Postgres(t)
	repo := repository.New(db)
	ctx := context.Background()
	owner := domain.User{ID: "owner", Username: "owner", DisplayName: "Owner", Role: "platform_admin"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	aid := "agent"
	if err := db.Create(&domain.Agent{ID: aid, Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`["PROC_CPU"]`), Labels: []byte(`{}`), Resources: []byte(`{"processes":[{"pid":7}],"target_policy":{"allow_host_resources":true}}`), LastSeenAt: time.Now()}).Error; err != nil {
		t.Fatal(err)
	}
	store := &fixtureStore{}
	tasks := &Tasks{Repo: repo, Storage: store}
	t.Run("canonical Core resource policy supports host container and off-snapshot PID", func(t *testing.T) {
		resources, _ := json.Marshal(map[string]any{"processes": []any{}, "target_policy": json.RawMessage(targetPolicyFixture(t))})
		policyAgent := domain.Agent{ID: "canonical-policy-agent", Hostname: "canonical fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`["PROC_CPU","RESOURCE_SNAPSHOT"]`), Labels: []byte(`{}`), Resources: resources, LastSeenAt: time.Now()}
		if err := db.Create(&policyAgent).Error; err != nil {
			t.Fatal(err)
		}
		cases := []domain.CreateTask{{Kind: "RESOURCE_SNAPSHOT", AgentID: policyAgent.ID, Parameters: domain.Parameters{Target: &domain.Target{Type: "host"}}}, {Kind: "PROC_CPU", AgentID: policyAgent.ID, Parameters: domain.Parameters{Target: &domain.Target{Type: "container", ContainerID: strings.Repeat("a", 64), Runtime: "docker", ContainerPID: 1}}}, {Kind: "PROC_CPU", AgentID: policyAgent.ID, Parameters: domain.Parameters{PID: 99999, Target: &domain.Target{Type: "process", PID: 99999}}}}
		for i, in := range cases {
			task, _, err := tasks.Create(ctx, owner, in, fmt.Sprintf("canonical-policy-%d", i), "fixture")
			if err != nil {
				t.Fatal(in.Kind, err)
			}
			if i == 2 {
				var p domain.Parameters
				json.Unmarshal(task.Parameters, &p)
				if p.Target == nil || !p.Target.CheckUID || p.Target.ExpectedUID != 10001 {
					t.Fatal("off-snapshot target not UID-fenced")
				}
			}
		}
		bad := cases[1]
		bad.Parameters.Target = &domain.Target{Type: "container", ContainerID: strings.Repeat("b", 64), Runtime: "docker", ContainerPID: 1}
		if _, _, err := tasks.Create(ctx, owner, bad, "canonical-policy-denied", "fixture"); err == nil {
			t.Fatal("container ID outside exact whitelist accepted")
		}
	})
	t.Run("binary import persists exact bytes format pipeline and replay", func(t *testing.T) {
		data := []byte{0x1f, 0x8b, 0, 0xff, 1}
		in := domain.CreateTask{Name: "Explicit imported API fixture", Kind: "GO_HEAP", Import: &domain.ImportInput{Filename: "heap.pprof", DataBase64: base64.StdEncoding.EncodeToString(data)}}
		task, replay, err := tasks.Create(ctx, owner, in, "binary-import-fixture", "fixture")
		if err != nil || replay {
			t.Fatal(task, replay, err)
		}
		task2, replay, err := tasks.Create(ctx, owner, in, "binary-import-fixture", "fixture")
		if err != nil || !replay || task.ID != task2.ID {
			t.Fatal(task2, replay, err)
		}
		artifacts, err := repo.Artifacts(ctx, task.ID)
		if err != nil || len(artifacts) != 1 {
			t.Fatal(artifacts, err)
		}
		a := artifacts[0]
		if a.Format != "pprof" || a.ExpiresAt == nil || !a.DownloadAllowed || !bytes.Equal(store.objects[a.ObjectKey], data) {
			t.Fatal("binary import corrupted metadata/content", a)
		}
		var pipeline string
		db.Raw("SELECT pipeline FROM analysis_jobs WHERE task_id=?", task.ID).Scan(&pipeline)
		if pipeline != "pprof_heap_v1" {
			t.Fatal(pipeline)
		}
		db.Model(&a).Update("expires_at", time.Now().Add(-time.Second))
		if _, err := tasks.Download(ctx, owner, task.ID, a.ID, "fixture"); err == nil || store.downloads != 0 {
			t.Fatal("expired artifact was signed")
		}
	})
	t.Run("composite survives fresh service and optional dependent failure", func(t *testing.T) {
		in := domain.CreateComposite{Name: "DAG lifecycle fixture", DeadlineSeconds: 120, AllowPartialSuccess: true, Nodes: []domain.CompositeNodeInput{{Key: "cpu", Required: true, Task: domain.CreateTask{Kind: "PROC_CPU", AgentID: aid, Parameters: domain.Parameters{PID: 7, DurationSeconds: 1, FrequencyHz: 1}}}, {Key: "optional", DependsOn: []string{"cpu"}, Task: domain.CreateTask{Kind: "RESOURCE_SNAPSHOT", AgentID: aid, Parameters: domain.Parameters{Target: &domain.Target{Type: "host"}}}}, {Key: "dependent", DependsOn: []string{"optional"}, Task: domain.CreateTask{Kind: "PROC_CPU", AgentID: aid, Parameters: domain.Parameters{PID: 7, DurationSeconds: 1, FrequencyHz: 1}}}}}
		run, _, err := tasks.CreateComposite(ctx, owner, in, "composite-fixture-1", "fixture")
		if err != nil {
			t.Fatal(err)
		}
		if err := tasks.TickComposite(ctx); err != nil {
			t.Fatal(err)
		}
		_, nodes, err := tasks.Composite(ctx, owner, run.ID)
		if err != nil || nodes[0].TaskID == nil || nodes[1].TaskID != nil {
			t.Fatal(nodes, err)
		}
		if err := db.Model(&domain.Task{}).Where("id=?", *nodes[0].TaskID).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
			t.Fatal(err)
		}
		restarted := &Tasks{Repo: repository.New(db), Storage: store}
		if err := restarted.TickComposite(ctx); err != nil {
			t.Fatal(err)
		}
		out, nodes, err := restarted.Composite(ctx, owner, run.ID)
		if err != nil || out.Status != "PARTIAL_SUCCESS" || nodes[1].Status != "FAILED" || nodes[2].Status != "SKIPPED" {
			t.Fatal(out, nodes, err)
		}
		if _, replay, err := tasks.CreateComposite(ctx, owner, in, "composite-fixture-1", "fixture"); err != nil || !replay {
			t.Fatal(replay, err)
		}
	})
	t.Run("composite overall deadline cancels exact child attempt", func(t *testing.T) {
		in := domain.CreateComposite{Name: "Deadline fixture", DeadlineSeconds: 60, Nodes: []domain.CompositeNodeInput{{Key: "cpu", Required: true, Task: domain.CreateTask{Kind: "PROC_CPU", AgentID: aid, Parameters: domain.Parameters{PID: 7, DurationSeconds: 1, FrequencyHz: 1}}}}}
		run, _, err := tasks.CreateComposite(ctx, owner, in, "composite-deadline", "fixture")
		if err != nil {
			t.Fatal(err)
		}
		if err := tasks.TickComposite(ctx); err != nil {
			t.Fatal(err)
		}
		db.Model(&run).Update("deadline_at", time.Now().Add(-time.Second))
		if err := tasks.TickComposite(ctx); err != nil {
			t.Fatal(err)
		}
		out, nodes, err := tasks.Composite(ctx, owner, run.ID)
		if err != nil || out.Status != "TIMED_OUT" || nodes[0].Task.CollectionStatus != "CANCELED" {
			t.Fatal(out, nodes, err)
		}
		var n int64
		db.Table("outbox").Where("task_id=? AND event_type='CANCEL'", *nodes[0].TaskID).Count(&n)
		if n != 1 {
			t.Fatal(n)
		}
	})
}

func TestPostgresOIDCRealSignedCallback(t *testing.T) {
	db := testutil.Postgres(t)
	repo := repository.New(db)
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	wrong, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var issuer string
	var authQuery url.Values
	var fixtureMu sync.Mutex
	mode := "success"
	calls := 0
	signer := func(k *rsa.PrivateKey) jose.Signer {
		s, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: jose.JSONWebKey{Key: k, KeyID: "fixture"}}, nil)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fixtureMu.Lock()
		defer fixtureMu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			_ = json.NewEncoder(w).Encode(map[string]any{"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token", "jwks_uri": issuer + "/keys", "response_types_supported": []string{"code"}, "subject_types_supported": []string{"public"}, "id_token_signing_alg_values_supported": []string{"RS256"}})
		case "/keys":
			_ = json.NewEncoder(w).Encode(jose.JSONWebKeySet{Keys: []jose.JSONWebKey{{Key: &key.PublicKey, KeyID: "fixture", Algorithm: "RS256", Use: "sig"}}})
		case "/token":
			calls++
			_ = r.ParseForm()
			sum := sha256.Sum256([]byte(r.Form.Get("code_verifier")))
			if base64.RawURLEncoding.EncodeToString(sum[:]) != authQuery.Get("code_challenge") {
				t.Error("PKCE proof missing")
				http.Error(w, "bad verifier", 400)
				return
			}
			nonce := authQuery.Get("nonce")
			audience := "client"
			signing := key
			if mode == "nonce" {
				nonce = "wrong"
			}
			if mode == "audience" {
				audience = "other"
			}
			if mode == "signature" {
				signing = wrong
			}
			token, err := jwt.Signed(signer(signing)).Claims(jwt.Claims{Issuer: issuer, Subject: "subject-123", Audience: jwt.Audience{audience}, Expiry: jwt.NewNumericDate(time.Now().Add(time.Minute)), IssuedAt: jwt.NewNumericDate(time.Now())}).Claims(map[string]any{"nonce": nonce, "name": "OIDC fixture user"}).Serialize()
			if err != nil {
				t.Error(err)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "fixture-access", "token_type": "Bearer", "id_token": token})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	fixtureMu.Lock()
	issuer = server.URL
	fixtureMu.Unlock()
	o, err := NewOIDC(context.Background(), repo, OIDCConfig{Issuer: issuer, ClientID: "client", ClientSecret: "fixture-secret", RedirectURL: issuer + "/callback", AllowHTTP: true})
	if err != nil {
		t.Fatal(err)
	}
	for _, current := range []string{"success", "nonce", "audience", "signature"} {
		t.Run(current, func(t *testing.T) {
			fixtureMu.Lock()
			mode = current
			fixtureMu.Unlock()
			redirect, browser, err := o.Start(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			u, _ := url.Parse(redirect)
			fixtureMu.Lock()
			authQuery = u.Query()
			fixtureMu.Unlock()
			if authQuery.Get("code_challenge_method") != "S256" || authQuery.Get("nonce") == "" {
				t.Fatal("missing OIDC protections")
			}
			user, token, _, err := o.Callback(context.Background(), authQuery.Get("state"), browser, "code", "fixture")
			if current == "success" {
				if err != nil || user.Role != "viewer" || len(token) != 64 {
					t.Fatal(user, err)
				}
				_, sessionUser, err := repo.Session(context.Background(), token)
				if err != nil || sessionUser.ID != user.ID {
					t.Fatal(err)
				}
				if _, _, _, err := o.Callback(context.Background(), authQuery.Get("state"), browser, "code", "fixture"); err == nil {
					t.Fatal("callback replay accepted")
				}
			} else if err == nil {
				t.Fatal("invalid identity proof accepted")
			}
		})
	}
	t.Run("browser binding state and expiry", func(t *testing.T) {
		redirect, browser, err := o.Start(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		u, _ := url.Parse(redirect)
		q := u.Query()
		fixtureMu.Lock()
		before := calls
		fixtureMu.Unlock()
		if _, _, _, err := o.Callback(context.Background(), q.Get("state"), RandomToken(), "code", "fixture"); err == nil {
			t.Fatal("foreign browser accepted")
		}
		db.Exec("UPDATE oidc_states SET expires_at=now()-interval '1 second'")
		if _, _, _, err := o.Callback(context.Background(), q.Get("state"), browser, "code", "fixture"); err == nil {
			t.Fatal("expired state accepted")
		}
		fixtureMu.Lock()
		after := calls
		fixtureMu.Unlock()
		if after != before {
			t.Fatal("invalid state contacted token endpoint")
		}
	})
}

func TestPostgresAIConsentRedactionFencingAndIdempotency(t *testing.T) {
	db := testutil.Postgres(t)
	r := repository.New(db)
	ctx := context.Background()
	u := domain.User{ID: "owner", Username: "owner", DisplayName: "Owner", Role: "operator"}
	db.Create(&u)
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported result fixture", Kind: "IMPORT_COLLAPSED", OwnerID: u.ID, Visibility: "private", Parameters: []byte(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", IdempotencyKey: "ai-fixture", RequestID: "fixture"}, false, domain.Audit{Action: "test", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	db.Model(&task).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"})
	result := `{"kind":"IMPORT_COLLAPSED","total_samples":150,"top_functions":[{"name":"/customer/private/token","self_percent":80,"total_percent":85}]}`
	db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,result) VALUES(?,?,'folded_v1','success',?)", task.ID, attempt.ID, result)
	var received atomic.Int32
	var mode atomic.Int32
	var streamJob atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		received.Add(1)
		var v map[string]any
		json.NewDecoder(req.Body).Decode(&v)
		b, _ := json.Marshal(v)
		if strings.Contains(string(b), "customer") || strings.Contains(string(b), "private") || strings.Contains(string(b), "token\"") {
			t.Error("identity/raw symbol leaked to AI")
		}
		if mode.Load() > 0 {
			if v["stream"] != true {
				t.Error("stream flag missing")
			}
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Observe the \"}}]}\n\n")
			w.(http.Flusher).Flush()
			if mode.Load() == 2 {
				until := time.Now().Add(3 * time.Second)
				for time.Now().Before(until) {
					var n int64
					db.Table("task_events").Where("type='SUGGESTION_DELTA' AND payload->>'ai_request_id'=?", fmt.Sprint(streamJob.Load())).Count(&n)
					if n > 0 {
						break
					}
					time.Sleep(10 * time.Millisecond)
				}
				db.Exec("UPDATE ai_requests SET lease_owner='revoked-fixture' WHERE id=?", streamJob.Load())
			}
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"symbolic hotspot.\"}}]}\n\ndata: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"choices":[{"message":{"content":"<script>bad()</script><b>Measure the top symbolic hotspot.</b>"}}]}`)
	}))
	defer server.Close()
	tasks := &Tasks{Repo: r}
	ai, err := NewSuggestions(r, tasks, AIConfig{Endpoint: server.URL, Model: "fixture-model", AllowHTTP: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ai.Request(ctx, u, task.ID, false, "fixture"); err == nil {
		t.Fatal("missing consent accepted")
	}
	job, err := ai.Request(ctx, u, task.ID, true, "fixture")
	if err != nil {
		t.Fatal(err)
	}
	same, err := ai.Request(ctx, u, task.ID, true, "fixture")
	if err != nil || same.ID != job.ID {
		t.Fatal(same, err)
	}
	if err := ai.process(ctx); err != nil {
		t.Fatal(err)
	}
	if err := ai.process(ctx); err != nil {
		t.Fatal(err)
	}
	if received.Load() != 1 {
		t.Fatal(received.Load())
	}
	suggestions, err := r.Suggestions(ctx, task.ID)
	if err != nil || len(suggestions) != 1 || suggestions[0]["engine"] != "ai" {
		t.Fatal(suggestions, err)
	}
	var updated domain.Task
	db.First(&updated, task.ID)
	if !updated.UpdatedAt.After(task.UpdatedAt) {
		t.Fatal("task change not observable")
	}
	t.Run("stream chunks persist before final and lease revocation fences", func(t *testing.T) {
		for i := 1; i <= 2; i++ {
			mode.Store(int32(i))
			changed := strings.Replace(result, ":150,", fmt.Sprintf(":%d,", 150+i), 1)
			if err := db.Exec("UPDATE analysis_jobs SET result=? WHERE task_id=?", changed, task.ID).Error; err != nil {
				t.Fatal(err)
			}
			job, err := ai.Request(ctx, u, task.ID, true, "fixture")
			if err != nil {
				t.Fatal(err)
			}
			streamJob.Store(job.ID)
			if err := ai.process(ctx); err != nil {
				t.Fatal(err)
			}
			var deltas, finals int64
			db.Table("task_events").Where("type='SUGGESTION_DELTA' AND payload->>'ai_request_id'=?", fmt.Sprint(job.ID)).Count(&deltas)
			db.Table("task_events").Where("type='SUGGESTION_CREATED' AND payload->>'ai_request_id'=?", fmt.Sprint(job.ID)).Count(&finals)
			if i == 1 && (deltas < 1 || finals != 1) {
				t.Fatal(deltas, finals)
			}
			if i == 2 && (deltas != 1 || finals != 0) {
				t.Fatal("stale stream committed", deltas, finals)
			}
		}
	})
}

func TestPostgresAdministrativeRecoveryAndAgentAudit(t *testing.T) {
	db := testutil.Postgres(t)
	r := repository.New(db)
	ctx := context.Background()
	admin := domain.User{ID: "admin", Username: "admin", DisplayName: "Admin", Role: "platform_admin"}
	operator := domain.User{ID: "operator", Username: "operator", DisplayName: "Operator", Role: "operator"}
	db.Create(&admin)
	db.Create(&operator)
	aid := "a"
	db.Create(&domain.Agent{ID: aid, Hostname: "a", Platform: "linux", Version: "test", Status: "online", LastSeenAt: time.Now(), Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)})
	tasks := &Tasks{Repo: r}
	if _, err := tasks.SetAgentEnabled(ctx, operator, aid, false, "fixture"); err == nil {
		t.Fatal("operator disabled agent")
	}
	if a, err := tasks.SetAgentEnabled(ctx, admin, aid, false, "fixture"); err != nil || a.Status != "disabled" {
		t.Fatal(a, err)
	}
	if a, err := tasks.SetAgentEnabled(ctx, admin, aid, true, "fixture"); err != nil || a.Status != "online" {
		t.Fatal(a, err)
	}
	var audits int64
	db.Table("audit_logs").Where("resource_type='agent'").Count(&audits)
	if audits != 2 {
		t.Fatal(audits)
	}
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Reanalysis imported fixture", Kind: "IMPORT_COLLAPSED", OwnerID: operator.ID, Parameters: []byte(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "fixture", IdempotencyKey: "reanalyze-fixture"}, false, domain.Audit{Action: "test", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	raw := domain.Artifact{TaskID: task.ID, AttemptID: attempt.ID, Kind: "RAW", Name: "profile.folded", ObjectKey: "fixture/reanalyze.folded", ContentType: "text/plain", Size: 8, SHA256: strings.Repeat("0", 64), Status: "ready"}
	if err := r.ImportComplete(ctx, task, raw); err != nil {
		t.Fatal(err)
	}
	db.Exec("UPDATE analysis_jobs SET status='success',attempt=2,result='{}' WHERE task_id=?", task.ID)
	db.Model(&task).Update("analysis_status", "SUCCESS")
	if err := tasks.Reanalyze(ctx, operator, task.ID, "fixture"); err == nil {
		t.Fatal("operator reanalyzed")
	}
	if err := tasks.Reanalyze(ctx, admin, task.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	var state struct {
		Status               string
		Attempt, MaxAttempts int
	}
	db.Table("analysis_jobs").Where("task_id=?", task.ID).Take(&state)
	if state.Status != "pending" || state.Attempt != 2 || state.MaxAttempts != 5 {
		t.Fatal(state)
	}
	var history int64
	db.Table("analysis_job_runs").Where("task_id=?", task.ID).Count(&history)
	if history != 1 {
		t.Fatal(history)
	}
	if _, err := r.Result(ctx, task.ID); err == nil {
		t.Fatal("old result returned during reanalysis")
	}
	if err := tasks.Reanalyze(ctx, admin, task.ID, "fixture"); err == nil {
		t.Fatal("pending job overwritten")
	}
	db.Exec("UPDATE analysis_jobs SET status='running',lease_owner='live',lease_expires_at=now()+interval '1 minute' WHERE task_id=?", task.ID)
	if err := tasks.Reanalyze(ctx, admin, task.ID, "fixture"); err == nil {
		t.Fatal("live lease overwritten")
	}
	db.Model(&task).Update("analysis_status", "SUCCESS")
	if err := tasks.Delete(ctx, operator, task.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Task(ctx, admin, task.ID); err == nil {
		t.Fatal("deleted task still exposed")
	}
	var artifacts int64
	db.Table("artifacts").Where("task_id=?", task.ID).Count(&artifacts)
	if artifacts != 1 {
		t.Fatal("metadata deletion removed stored objects")
	}
}
