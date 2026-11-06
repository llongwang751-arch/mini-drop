package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"time"
)

type programConfig struct{ Collector, Python, Analyzer string }
type boundedBuffer struct {
	data     []byte
	limit    int
	exceeded bool
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	left := b.limit - len(b.data)
	if len(p) > left {
		b.data = append(b.data, p[:left]...)
		b.exceeded = true
		return left, errors.New("OUTPUT_LIMIT")
	}
	b.data = append(b.data, p...)
	return len(p), nil
}

func execute(parent context.Context, program string, args []string) ([]byte, string) {
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	// Never inherit arbitrary application credentials or PYTHONPATH.
	cmd.Env = []string{"LC_ALL=C", "PYTHONNOUSERSITE=1", "PYTHONSAFEPATH=1"}
	for _, key := range []string{"SystemRoot", "WINDIR", "TEMP", "TMP"} {
		if value, ok := os.LookupEnv(key); ok {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
	}
	out := &boundedBuffer{limit: 32 * 1024}
	diagnostic := &boundedBuffer{limit: 8 * 1024}
	cmd.Stdout = out
	cmd.Stderr = diagnostic
	err := cmd.Run()
	if ctx.Err() != nil {
		return nil, "TOOL_TIMEOUT"
	}
	if out.exceeded || diagnostic.exceeded {
		return nil, "TOOL_OUTPUT_LIMIT"
	}
	if err != nil {
		return nil, "TOOL_FAILED"
	}
	return out.data, ""
}

type rawProfile struct {
	SchemaVersion       int    `json:"schema_version"`
	Source              string `json:"source"`
	Unit                string `json:"unit"`
	ClockTicksPerSecond int64  `json:"clock_ticks_per_second"`
	TotalTicks          int64  `json:"total_ticks"`
	Iterations          int    `json:"iterations"`
	Checksum            string `json:"checksum"`
}
type graphNode struct {
	Name     string      `json:"name"`
	Value    int64       `json:"value"`
	Self     int64       `json:"self"`
	Children []graphNode `json:"children"`
}
type analyzedProfile struct {
	SchemaVersion int `json:"schema_version"`
	Summary       struct {
		Source              string   `json:"source"`
		Unit                string   `json:"unit"`
		TotalTicks          int64    `json:"total_ticks"`
		ClockTicksPerSecond int64    `json:"clock_ticks_per_second"`
		Limitations         []string `json:"limitations"`
	} `json:"summary"`
	Flamegraph  graphNode `json:"flamegraph"`
	InputSHA256 string    `json:"input_sha256"`
}

func validateRaw(raw []byte, iterations int) (rawProfile, bool) {
	var profile rawProfile
	if len(raw) == 0 || len(raw) > 32768 || strictJSON(raw, &profile) != nil {
		return profile, false
	}
	valid := profile.SchemaVersion == 1 && profile.Source == "controlled_self" && profile.Unit == "cpu_ticks" && profile.Iterations == iterations && profile.ClockTicksPerSecond > 0 && profile.ClockTicksPerSecond <= 1000000000 && profile.TotalTicks >= 0 && profile.TotalTicks <= profile.ClockTicksPerSecond*60 && regexp.MustCompile(`^[0-9a-f]{16}$`).MatchString(profile.Checksum)
	return profile, valid
}

func validateResult(result, raw []byte, profile rawProfile) bool {
	var parsed analyzedProfile
	if len(result) == 0 || len(result) > 32768 || strictJSON(result, &parsed) != nil {
		return false
	}
	summary := parsed.Summary
	tree := parsed.Flamegraph
	if parsed.SchemaVersion != 1 || parsed.InputSHA256 != inputDigest(raw) || summary.Source != profile.Source || summary.Unit != profile.Unit || summary.TotalTicks != profile.TotalTicks || summary.ClockTicksPerSecond != profile.ClockTicksPerSecond || len(summary.Limitations) < 1 || len(summary.Limitations) > 8 || tree.Name != "all" || tree.Value != profile.TotalTicks || tree.Self != 0 || len(tree.Children) != 1 {
		return false
	}
	for _, text := range summary.Limitations {
		if len(text) == 0 || len(text) > 512 {
			return false
		}
	}
	leaf := tree.Children[0]
	return leaf.Name == "controlled_process" && leaf.Value == profile.TotalTicks && leaf.Self == profile.TotalTicks && len(leaf.Children) == 0
}

func readLimited(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 32769))
	if err != nil || len(raw) > 32768 {
		return nil, errors.New("FILE_LIMIT")
	}
	return raw, nil
}

func runAttempt(ctx context.Context, s *store, config programConfig, job work, t *task) (json.RawMessage, string) {
	directory := s.attemptDir(job)
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, "WORKDIR_ERROR"
	}
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, "WORKDIR_ERROR"
	}
	raw, code := execute(ctx, config.Collector, []string{"--iterations", strconv.Itoa(t.Iterations)})
	if code != "" {
		return nil, code
	}
	profile, valid := validateRaw(raw, t.Iterations)
	if !valid {
		return nil, "INVALID_COLLECTOR_OUTPUT"
	}
	rawPath := filepath.Join(directory, "raw.json")
	resultPath := filepath.Join(directory, "result.json")
	if err := atomicWrite(rawPath, raw); err != nil {
		return nil, "RAW_WRITE_FAILED"
	}
	_, code = execute(ctx, config.Python, []string{"-I", config.Analyzer, rawPath, "--output", resultPath})
	if code != "" {
		return nil, code
	}
	result, err := readLimited(resultPath)
	if err != nil || !validateResult(result, raw, profile) {
		return nil, "RESULT_INTEGRITY_FAILED"
	}
	return json.RawMessage(result), ""
}

func worker(ctx context.Context, s *store, config programConfig) {
	for {
		select {
		case <-ctx.Done():
			return
		case job := <-s.queue:
			t, err := s.begin(job)
			if err != nil {
				log.Print("mini-drop: journal transition refused; no collection started")
				continue
			}
			result, code := runAttempt(ctx, s, config, job, t)
			if err = s.finish(job, result, code); err != nil {
				log.Print("mini-drop: result publication refused; journal retained")
			}
		}
	}
}
