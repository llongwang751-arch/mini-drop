// 验证临时测试证书的独立身份、SAN、短期 CA 归属与文件拒绝覆盖规则，以及负载参数的硬上限。
// 这里只使用临时目录中的合成证书，不读取生产凭据，也不向生产服务施加负载。
package main

import (
	"crypto/ecdsa"
	"crypto/x509"
	"os"
	"path/filepath"
	"testing"
)

func TestEphemeralDistinctAgentIdentitiesAndServerSAN(t *testing.T) {
	directory := t.TempDir()
	if err := prepareFixture(directory, "0123456789abcdef"); err != nil {
		t.Fatal(err)
	}
	ca, err := loadAuthority(directory, "0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	first, err := ca.client("bench-0123456789abcdef-00")
	if err != nil {
		t.Fatal(err)
	}
	second, err := ca.client("bench-0123456789abcdef-01")
	if err != nil {
		t.Fatal(err)
	}
	leaf, _ := x509.ParseCertificate(first.Certificate[0])
	other, _ := x509.ParseCertificate(second.Certificate[0])
	if leaf.Subject.CommonName != "drop-agent-bench-0123456789abcdef-00" ||
		leaf.SerialNumber.Cmp(other.SerialNumber) == 0 || leaf.PublicKey.(*ecdsa.PublicKey).Equal(other.PublicKey) {
		t.Fatal("Agents shared a certificate identity")
	}
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: ca.pool, DNSName: leaf.Subject.CommonName, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		t.Fatal(err)
	}
	encoded, err := readPEM(filepath.Join(directory, "server.crt"), "CERTIFICATE")
	if err != nil {
		t.Fatal(err)
	}
	server, _ := x509.ParseCertificate(encoded)
	if _, err := server.Verify(x509.VerifyOptions{Roots: ca.pool, DNSName: "drop-server", KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		t.Fatal(err)
	}
	if prepareFixture(directory, "0123456789abcdef") == nil {
		t.Fatal("Existing fixture certificate was overwritten")
	}
	if _, err := loadAuthority(directory, "ffffffffffffffff"); err == nil {
		t.Fatal("Another run's CA was accepted")
	}
	if err := os.WriteFile(filepath.Join(directory, "ca.key"), []byte("invalid-imported-fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadAuthority(directory, "0123456789abcdef"); err == nil {
		t.Fatal("Invalid private CA key was accepted")
	}
}

func TestBoundedOptionsRefuseEscalationAndMutableImages(t *testing.T) {
	for _, arguments := range [][]string{
		{"--mode", "prepare", "--run-id", "0123456789abcdef", "--rounds", "11"},
		{"--mode", "prepare", "--run-id", "0123456789abcdef", "--concurrency", "9"},
		{"--mode", "prepare", "--run-id", "invalid"},
		{"--mode", "run", "--run-id", "0123456789abcdef", "--core-image-id", "drop-core:latest"},
		{"--mode", "run", "--target", "production:50051"},
	} {
		if _, err := flags(arguments); err == nil {
			t.Fatal("Unbounded or unreviewed load options were accepted")
		}
	}
}
