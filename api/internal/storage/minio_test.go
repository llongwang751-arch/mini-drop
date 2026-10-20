package storage

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestBrowserPresignRetainsExactPathAndSignature(t *testing.T) {
	u, _ := url.Parse("http://minio:9000/drop-artifacts/raw/tasks/1/profile.folded?X-Amz-Signature=abc&X-Amz-Expires=300")
	got := BrowserURL("https://drop.example", u)
	if !strings.HasPrefix(got, "https://drop.example/objects/drop-artifacts/") || !strings.HasSuffix(got, "?"+u.RawQuery) {
		t.Fatal(got)
	}
}

func TestBrowserPresignSignsIdentityEncodingAndPreservesGrant(t *testing.T) {
	const access, secret = "imported-test-access", "imported-test-secret"
	store, err := New("minio:9000", access, secret, "drop-artifacts", "https://drop.example")
	if err != nil {
		t.Fatal("storage fixture setup failed")
	}
	const key = "raw/tasks/7/profile.json.gz"
	got, err := store.PresignGetFor(context.Background(), key, 37*time.Second)
	if err != nil {
		t.Fatal("presign fixture failed")
	}
	u, err := url.Parse(got)
	if err != nil {
		t.Fatal("invalid browser URL")
	}
	query := u.Query()
	if u.Scheme != "https" || u.Host != "drop.example" || u.Path != "/objects/drop-artifacts/"+key || query.Get("X-Amz-Expires") != "37" {
		t.Fatal("browser origin, exact object path or grant TTL changed")
	}
	if query.Get("response-content-encoding") != "identity" {
		t.Fatal("stored object encoding must be overridden for exact browser bytes")
	}
	// BrowserURL keeps the signed internal Host/path behind the same-origin
	// proxy. Verify SigV4 independently, then prove the override is covered by
	// that signature rather than appended as an unsigned browser parameter.
	u.Host, u.Path = "minio:9000", strings.TrimPrefix(u.Path, "/objects")
	if !validFixtureSignature(u, secret) {
		t.Fatal("exact-byte download override is not validly signed")
	}
	query.Del("response-content-encoding")
	u.RawQuery = query.Encode()
	if validFixtureSignature(u, secret) {
		t.Fatal("removing the identity override must invalidate the grant signature")
	}
}

func validFixtureSignature(u *url.URL, secret string) bool {
	query := u.Query()
	signature := query.Get("X-Amz-Signature")
	query.Del("X-Amz-Signature")
	credential := strings.Split(query.Get("X-Amz-Credential"), "/")
	if len(credential) != 5 || query.Get("X-Amz-SignedHeaders") != "host" {
		return false
	}
	canonical := "GET\n" + u.EscapedPath() + "\n" + strings.ReplaceAll(query.Encode(), "+", "%20") + "\nhost:" + u.Host + "\n\nhost\nUNSIGNED-PAYLOAD"
	sum := sha256.Sum256([]byte(canonical))
	scope := strings.Join(credential[1:], "/")
	toSign := "AWS4-HMAC-SHA256\n" + query.Get("X-Amz-Date") + "\n" + scope + "\n" + hex.EncodeToString(sum[:])
	key := []byte("AWS4" + secret)
	for _, part := range credential[1:] {
		mac := hmac.New(sha256.New, key)
		_, _ = mac.Write([]byte(part))
		key = mac.Sum(nil)
	}
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write([]byte(toSign))
	return hmac.Equal([]byte(signature), []byte(hex.EncodeToString(mac.Sum(nil))))
}
