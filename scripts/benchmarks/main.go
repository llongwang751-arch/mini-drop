// This bounded client exercises the real Core RPCs using generated contracts.
// It does not collect a profile, create tasks or stand in for a business Agent.
package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	dv "drop/api/gen/dropv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/status"
)

const cadence = 2 * time.Second

type options struct {
	mode, directory, runID, coreImage, postgresImage, dockerVersion string
	rounds, concurrency                                             int
}

type agent struct {
	id         string
	connection *grpc.ClientConn
	heartbeat  dv.HealthCheckClient
}

type stageReport struct {
	Agents             int            `json:"simulated_agents"`
	Rounds             int            `json:"rounds_per_agent"`
	Concurrency        int            `json:"maximum_in_flight_rpcs"`
	PlannedHeartbeats  int            `json:"planned_heartbeats"`
	SetupFailed        int            `json:"registration_or_warmup_failed"`
	SetupFailureCodes  map[string]int `json:"setup_failure_codes"`
	MeasurementStarted bool           `json:"measurement_started"`
	Statistics         statistics     `json:"measurement"`
}

func flags(arguments []string) (options, error) {
	var result options
	parser := flag.NewFlagSet("heartbeat-baseline", flag.ContinueOnError)
	parser.SetOutput(io.Discard)
	parser.StringVar(&result.mode, "mode", "run", "prepare, run or version")
	parser.StringVar(&result.directory, "state", "/fixture", "temporary fixture directory")
	parser.StringVar(&result.runID, "run-id", "", "16 hexadecimal fixture identity")
	parser.StringVar(&result.coreImage, "core-image-id", "", "immutable candidate image ID")
	parser.StringVar(&result.postgresImage, "postgres-image-id", "", "immutable private database image ID")
	parser.StringVar(&result.dockerVersion, "docker-version", "", "Docker Engine version")
	parser.IntVar(&result.rounds, "rounds", 10, "1..10 bounded heartbeat rounds")
	parser.IntVar(&result.concurrency, "concurrency", 8, "1..8 in-flight RPCs")
	if parser.Parse(arguments) != nil || parser.NArg() != 0 {
		return result, errors.New("ARGUMENTS_INVALID")
	}
	if result.mode == "version" {
		return result, nil
	}
	if (result.mode != "prepare" && result.mode != "run") || !filepath.IsAbs(result.directory) || len(result.directory) > 1024 ||
		!regexp.MustCompile(`^[0-9a-f]{16}$`).MatchString(result.runID) || result.rounds < 1 || result.rounds > 10 ||
		result.concurrency < 1 || result.concurrency > 8 {
		return result, errors.New("BOUNDED_OPTIONS_INVALID")
	}
	if result.mode == "run" {
		for _, image := range []string{result.coreImage, result.postgresImage} {
			if !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(image) {
				return result, errors.New("IMMUTABLE_IMAGE_ID_REQUIRED")
			}
		}
		if !regexp.MustCompile(`^[0-9][0-9A-Za-z.+_-]{0,63}$`).MatchString(result.dockerVersion) {
			return result, errors.New("DOCKER_VERSION_INVALID")
		}
	}
	return result, nil
}

func rpcCode(err error) string {
	if err == nil {
		return ""
	}
	return status.Code(err).String() // Never emit error text, TLS material or addresses.
}

func newAgent(ca *authority, id string) (*agent, error) {
	certificate, err := ca.client(id)
	if err != nil {
		return nil, err
	}
	connection, err := grpc.NewClient("drop-server:50051", grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{
		MinVersion: tls.VersionTLS12, RootCAs: ca.pool, Certificates: []tls.Certificate{certificate}, ServerName: "drop-server",
	})), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(1<<20), grpc.MaxCallSendMsgSize(1<<20)))
	if err != nil {
		return nil, errors.New("FIXTURE_CLIENT_FAILED")
	}
	return &agent{id: id, connection: connection, heartbeat: dv.NewHealthCheckClient(connection)}, nil
}

func (a *agent) beat(ctx context.Context) error {
	call, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	response, err := a.heartbeat.Do(call, &dv.HeartbeatRequest{AgentId: a.id,
		ResourcesJson: `{"benchmark":{"synthetic":true,"collection_enabled":false,"cadence_seconds":2}}`})
	if err == nil && (len(response.Tasks) != 0 || len(response.CancelAttemptIds) != 0) {
		return status.Error(codes.FailedPrecondition, "FIXTURE_HAS_UNEXPECTED_WORK")
	}
	return err
}

func (a *agent) register(ctx context.Context, runID string, expires time.Time) error {
	call, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	labels, _ := json.Marshal(map[string]any{"benchmark": true, "collection_enabled": false, "run_id": runID})
	response, err := dv.NewInitAgentClient(a.connection).Init(call, &dv.AgentInfo{AgentId: a.id,
		Hostname: "synthetic-heartbeat-client", Version: "benchmark-v1", Platform: "synthetic", LabelsJson: string(labels),
		ProtocolVersion: 2, CapabilityDetailsJson: "{}", BuildCommit: "benchmark-fixture", Os: "synthetic", Arch: runtime.GOARCH,
		Kernel: "not-a-collected-host", ToolVersionsJson: "{}", CertificateExpiresUnixMs: expires.UnixMilli()}, grpc.WaitForReady(true))
	if err == nil && (response.ProtocolVersion != 2 || response.HeartbeatSeconds != 2) {
		return status.Error(codes.FailedPrecondition, "FIXTURE_PROTOCOL_CHANGED")
	}
	return err
}

func parallel(count, concurrency int, action func(int)) {
	jobs := make(chan int)
	var workers sync.WaitGroup
	for range min(count, concurrency) {
		workers.Go(func() {
			for index := range jobs {
				action(index)
			}
		})
	}
	for index := range count {
		jobs <- index
	}
	close(jobs)
	workers.Wait()
}

func sleepUntil(ctx context.Context, deadline time.Time) bool {
	timer := time.NewTimer(max(0, time.Until(deadline)))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func runStage(ctx context.Context, ca *authority, config options, count int) stageReport {
	result := stageReport{Agents: count, Rounds: config.rounds, Concurrency: min(count, config.concurrency),
		PlannedHeartbeats: count * config.rounds, SetupFailureCodes: map[string]int{}}
	agents := make([]*agent, count)
	setupErrors := make([]string, count)
	parallel(count, config.concurrency, func(index int) {
		id := fmt.Sprintf("bench-%s-%02d", config.runID, index)
		client, err := newAgent(ca, id)
		if err != nil {
			setupErrors[index] = "FIXTURE_CLIENT_FAILED"
			return
		}
		agents[index] = client
		err = client.register(ctx, config.runID, ca.certificate.NotAfter)
		if err == nil {
			err = client.beat(ctx) // TLS/SQL/connection warmup, excluded from measurement.
		}
		setupErrors[index] = rpcCode(err)
	})
	defer func() {
		for _, client := range agents {
			if client != nil {
				_ = client.connection.Close()
			}
		}
	}()
	for _, code := range setupErrors {
		if code != "" {
			result.SetupFailed++
			result.SetupFailureCodes[code]++
		}
	}
	if result.SetupFailed != 0 || ctx.Err() != nil {
		result.Statistics = summarize(nil, 0)
		return result
	}
	result.MeasurementStarted = true
	started := time.Now()
	samples := make([]sample, count*config.rounds)
	completed := 0
	for round := range config.rounds {
		if !sleepUntil(ctx, started.Add(time.Duration(round)*cadence)) {
			break
		}
		due := started.Add(time.Duration(round) * cadence)
		parallel(count, config.concurrency, func(index int) {
			before := time.Now()
			err := agents[index].beat(ctx)
			samples[round*count+index] = sample{latency: time.Since(before), queued: max(0, before.Sub(due)), code: rpcCode(err)}
		})
		completed += count
	}
	// Include the last two-second cadence interval in throughput, even when RPCs
	// finish quickly. Scheduling stalls remain visible in elapsed and queue delay.
	_ = sleepUntil(ctx, started.Add(time.Duration(config.rounds)*cadence))
	result.Statistics = summarize(samples[:completed], time.Since(started))
	return result
}

func boundedText(path string, maximum int) string {
	file, err := os.Open(path)
	if err != nil {
		return "unavailable"
	}
	defer file.Close()
	value, err := io.ReadAll(io.LimitReader(file, int64(maximum+1)))
	if err != nil || len(value) > maximum {
		return "unavailable"
	}
	return strings.TrimSpace(string(value))
}

func environment(config options) map[string]any {
	return map[string]any{"go": runtime.Version(), "os": runtime.GOOS, "architecture": runtime.GOARCH,
		"logical_cpus_seen": runtime.NumCPU(), "gomaxprocs": runtime.GOMAXPROCS(0),
		"kernel":                         boundedText("/proc/sys/kernel/osrelease", 256),
		"meminfo_host_not_container_rss": boundedText("/proc/meminfo", 8192),
		"client_cgroup_memory_max":       boundedText("/sys/fs/cgroup/memory.max", 128),
		"client_cgroup_memory_swap_max":  boundedText("/sys/fs/cgroup/memory.swap.max", 128),
		"client_cgroup_cpu_max":          boundedText("/sys/fs/cgroup/cpu.max", 128),
		"core_image_id":                  config.coreImage, "postgres_image_id": config.postgresImage, "docker_engine": config.dockerVersion,
		"runner_requested_joint_caps": map[string]any{"memory_bytes": 536870912, "cpu_quota_cores": 1,
			"additional_swap_bytes": 0, "device_read_bytes_per_second": 8388608, "device_write_bytes_per_second": 4194304},
	}
}

func execute(arguments []string) int {
	config, err := flags(arguments)
	if err != nil {
		fmt.Fprintln(os.Stderr, `{"success":false,"error_code":"ARGUMENTS_INVALID"}`)
		return 2
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if config.mode == "version" {
		_ = encoder.Encode(map[string]any{"tool": "drop-heartbeat-baseline-v1", "go": runtime.Version(), "os": runtime.GOOS, "architecture": runtime.GOARCH})
		return 0
	}
	if config.mode == "prepare" {
		if prepareFixture(config.directory, config.runID) != nil {
			fmt.Fprintln(os.Stderr, `{"success":false,"error_code":"FIXTURE_PREPARATION_FAILED"}`)
			return 1
		}
		_ = encoder.Encode(map[string]any{"success": true, "ephemeral_ecdsa_fixture_prepared": true})
		return 0
	}
	ca, err := loadAuthority(config.directory, config.runID)
	if err != nil {
		fmt.Fprintln(os.Stderr, `{"success":false,"error_code":"FIXTURE_AUTHORITY_INVALID"}`)
		return 1
	}
	interrupted, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	ctx, cancel := context.WithTimeout(interrupted, 180*time.Second)
	defer cancel()
	stages := make([]stageReport, 0, 3)
	success := true
	for _, count := range []int{10, 25, 50} {
		stage := runStage(ctx, ca, config, count)
		stages = append(stages, stage)
		if stage.SetupFailed != 0 || stage.Statistics.Failed != 0 || stage.Statistics.Success != stage.PlannedHeartbeats || ctx.Err() != nil {
			success = false
			break // Stop on failed acceptance; do not raise load after an error.
		}
	}
	report := map[string]any{"schema_version": 1, "tool": "drop-heartbeat-baseline-v1", "success": success,
		"provenance":       "Actual real Core InitAgent/HealthCheck RPCs and isolated PostgreSQL; all Agent identities and heartbeat payloads are synthetic benchmark clients, not business collectors.",
		"protocol_version": 2, "run_id": config.runID, "fixed_heartbeat_interval_seconds": 2,
		"percentile_method": "nearest rank over successful RPC durations; scheduling delay separate; failures never invent zero-latency success",
		"warmup":            "One actual registration and one heartbeat per identity per stage; TLS/setup/warmup excluded from measured latency and throughput.",
		"identity_model":    "One independent ECDSA-P256 mTLS client identity per logical Agent; IDs reused across ascending stages; empty collector capabilities; no tasks/events/profiles generated.",
		"limitations": []string{"Small fixed-cadence baseline under shared-host resource caps; not maximum capacity, saturation, availability or production SLA proof.",
			"Percentiles describe only this finite run; client CPU throttling and scheduler queue time can affect observed RPC latency.",
			"Unset protobuf CPU/memory/load fields have zero defaults; they are placeholders, not observed business-host resource measurements."},
		"environment": environment(config), "stages": stages}
	if encoder.Encode(report) != nil {
		return 1
	}
	if !success {
		return 1
	}
	return 0
}

func main() { os.Exit(execute(os.Args[1:])) }
