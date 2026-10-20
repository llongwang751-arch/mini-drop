package domain

import (
	"encoding/base64"
	"os"
	"testing"
)

func TestSharedCatalogValidatesTypedOptionsAndBinaryImports(t *testing.T) {
	kindsMu.RLock()
	old := taskKinds
	kindsMu.RUnlock()
	defer func() { kindsMu.Lock(); taskKinds = old; kindsMu.Unlock() }()
	b, err := os.ReadFile("../../../contracts/task-kinds.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := LoadKinds(b); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"GO_HEAP", "JAVA_HEAP", "PYTHON_MEMORY", "BOLT_PROFILE"} {
		k, ok := Kind(kind)
		if !ok || k.Pipeline == "" || k.RawName == "" || !k.AgentRunner {
			t.Fatal(k)
		}
	}
	in := CreateTask{Kind: "GO_CPU", AgentID: "a", Parameters: Parameters{PID: 7, Options: map[string]any{"url": "https://untrusted.invalid"}}}
	in.Normalize()
	if in.Validate() == nil {
		t.Fatal("arbitrary URL accepted")
	}
	in.Parameters.Options = map[string]any{"argv": []any{"sh", "-c", "touch /tmp/unsafe"}}
	if in.Validate() == nil {
		t.Fatal("arbitrary command accepted")
	}
	in.Parameters.Options = nil
	in.Parameters.Source = "perf"
	if in.Validate() == nil {
		t.Fatal("client source accepted")
	}
	encoded := base64.StdEncoding.EncodeToString([]byte{0, 255, 1})
	k, _ := Kind("BOLT_PROFILE")
	imp := ImportInput{Filename: "profile.fdata", DataBase64: encoded}
	if ValidateImport(k, imp) == nil {
		t.Fatal("required ELF omitted")
	}
	imp.Auxiliary = []ImportFile{{Filename: "target.elf", DataBase64: encoded}}
	if err := ValidateImport(k, imp); err != nil {
		t.Fatal(err)
	}
	imp.Auxiliary[0].Filename = "../../target.elf"
	if ValidateImport(k, imp) == nil {
		t.Fatal("path traversal accepted")
	}
	k, _ = Kind("GO_HEAP")
	imp = ImportInput{Filename: "heap.pprof", DataBase64: encoded, Format: "folded"}
	if ValidateImport(k, imp) == nil {
		t.Fatal("binary profile misdeclared as folded")
	}
}
