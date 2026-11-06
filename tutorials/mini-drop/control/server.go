package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"strconv"
	"strings"
)

func validKey(key string) bool {
	if len(key) < 8 || len(key) > 128 {
		return false
	}
	for _, r := range key {
		if r < 33 || r > 126 {
			return false
		}
	}
	return true
}

// Strict JSON includes unknown-field, duplicate-key, depth and trailing checks.
func strictJSON(raw []byte, target any) error {
	scan := json.NewDecoder(bytes.NewReader(raw))
	scan.UseNumber()
	var walk func(int) error
	walk = func(depth int) error {
		if depth > 16 {
			return errors.New("JSON_DEPTH_LIMIT")
		}
		token, err := scan.Token()
		if err != nil {
			return err
		}
		delim, container := token.(json.Delim)
		if !container {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for scan.More() {
				key, err := scan.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok || seen[name] {
					return errors.New("DUPLICATE_JSON_KEY")
				}
				seen[name] = true
				if err = walk(depth + 1); err != nil {
					return err
				}
			}
		case '[':
			for scan.More() {
				if err := walk(depth + 1); err != nil {
					return err
				}
			}
		default:
			return errors.New("INVALID_JSON")
		}
		_, err = scan.Token()
		return err
	}
	if err := walk(0); err != nil {
		return err
	}
	if _, err := scan.Token(); err != io.EOF {
		return errors.New("TRAILING_JSON")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	return d.Decode(target)
}

type api struct{ store *store }

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func failHTTP(w http.ResponseWriter, status int, code string) {
	writeJSON(w, status, map[string]any{"data": nil, "error": map[string]string{"code": code, "message": code}})
}
func mutationResult(w http.ResponseWriter, t *task, replay bool, err error) {
	if err != nil {
		var known *apiError
		if errors.As(err, &known) {
			failHTTP(w, known.Status, known.Code)
		} else {
			failHTTP(w, 503, "JOURNAL_UNAVAILABLE")
		}
		return
	}
	status := 201
	if replay {
		status = 200
	}
	writeJSON(w, status, map[string]any{"data": t})
}

func loopbackHost(host string) bool {
	name, port, err := net.SplitHostPort(host)
	if err != nil {
		return false
	}
	ip := net.ParseIP(name)
	number, err := strconv.Atoi(port)
	return err == nil && number > 0 && number <= 65535 && ip != nil && ip.IsLoopback()
}

func (a *api) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Reject DNS-rebinding Host names even though the listener is loopback-only.
	if !loopbackHost(r.Host) {
		failHTTP(w, 403, "LOCAL_HOST_REQUIRED")
		return
	}
	origin := r.Header.Get("Origin")
	if origin != "" && origin != "http://"+r.Host && origin != "http://127.0.0.1:4322" && origin != "http://127.0.0.1:4193" {
		failHTTP(w, 403, "ORIGIN_REJECTED")
		return
	}
	if r.URL.Path == "/tasks" {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, 200, map[string]any{"data": a.store.list()})
		case http.MethodPost:
			var body struct {
				Iterations int `json:"iterations"`
			}
			if !readBody(w, r, &body, false) {
				return
			}
			if body.Iterations < 1000 || body.Iterations > 10000000 {
				failHTTP(w, 400, "INVALID_ITERATIONS")
				return
			}
			key := r.Header.Get("Idempotency-Key")
			if !validKey(key) {
				failHTTP(w, 400, "INVALID_IDEMPOTENCY_KEY")
				return
			}
			t, replay, err := a.store.create(body.Iterations, key)
			mutationResult(w, t, replay, err)
		default:
			failHTTP(w, 405, "METHOD_NOT_ALLOWED")
		}
		return
	}
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/"), "/")
	if len(parts) < 2 || len(parts) > 3 || parts[0] != "tasks" || len(parts[1]) > 20 {
		failHTTP(w, 404, "NOT_FOUND")
		return
	}
	id := parts[1]
	if number, err := strconv.ParseInt(id, 10, 64); err != nil || number <= 0 || strconv.FormatInt(number, 10) != id {
		failHTTP(w, 404, "NOT_FOUND")
		return
	}
	if len(parts) == 2 && r.Method == http.MethodGet {
		t := a.store.get(id)
		if t == nil {
			failHTTP(w, 404, "NOT_FOUND")
			return
		}
		writeJSON(w, 200, map[string]any{"data": t})
		return
	}
	if len(parts) == 3 && parts[2] == "retry" && r.Method == http.MethodPost {
		var body struct{}
		if !readBody(w, r, &body, true) {
			return
		}
		key := r.Header.Get("Idempotency-Key")
		if !validKey(key) {
			failHTTP(w, 400, "INVALID_IDEMPOTENCY_KEY")
			return
		}
		t, replay, err := a.store.retry(id, key)
		mutationResult(w, t, replay, err)
		return
	}
	failHTTP(w, 405, "METHOD_NOT_ALLOWED")
}

func readBody(w http.ResponseWriter, r *http.Request, target any, allowEmpty bool) bool {
	if r.Header.Get("Content-Type") != "" {
		kind, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || kind != "application/json" {
			failHTTP(w, 415, "JSON_REQUIRED")
			return false
		}
	} else if !allowEmpty {
		failHTTP(w, 415, "JSON_REQUIRED")
		return false
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 1024))
	if err != nil {
		failHTTP(w, 413, "INPUT_SIZE_LIMIT")
		return false
	}
	if allowEmpty && len(bytes.TrimSpace(raw)) == 0 {
		raw = []byte("{}")
	}
	if len(bytes.TrimSpace(raw)) == 0 || bytes.TrimSpace(raw)[0] != '{' || strictJSON(raw, target) != nil {
		failHTTP(w, 400, "INVALID_JSON")
		return false
	}
	return true
}
