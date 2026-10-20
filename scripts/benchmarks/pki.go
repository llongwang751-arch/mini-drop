// 为隔离心跳基准生成短期测试 CA、服务端证书和各模拟 Agent 独立的客户端身份。
// 测试密钥只写入明确指定的临时夹具目录；不复用生产证书，也不允许覆盖其他运行的证书文件。
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"time"
)

const caPrefix = "drop-heartbeat-fixture-"

type authority struct {
	certificate *x509.Certificate
	key         *ecdsa.PrivateKey
	pool        *x509.CertPool
}

func serial() (*big.Int, error) { return rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128)) }

func writeExclusive(path, kind string, data []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("FIXTURE_FILE_CREATE_FAILED")
	}
	defer file.Close()
	if err = pem.Encode(file, &pem.Block{Type: kind, Bytes: data}); err != nil {
		return errors.New("FIXTURE_FILE_WRITE_FAILED")
	}
	return file.Sync()
}

// prepareFixture 在受控普通目录里生成当前 runID 专用的短期证书材料。
// 独占创建文件避免覆盖其他运行；后续 loadAuthority 还要核验 CA 名称与同一次运行绑定。
func prepareFixture(directory, runID string) error {
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("FIXTURE_DIRECTORY_INVALID")
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return errors.New("FIXTURE_KEY_FAILED")
	}
	number, err := serial()
	if err != nil {
		return errors.New("FIXTURE_SERIAL_FAILED")
	}
	now := time.Now()
	certificate := &x509.Certificate{SerialNumber: number, Subject: pkix.Name{CommonName: caPrefix + runID},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(2 * time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	encoded, err := x509.CreateCertificate(rand.Reader, certificate, certificate, &key.PublicKey, key)
	if err != nil {
		return errors.New("FIXTURE_CA_FAILED")
	}
	certificate, err = x509.ParseCertificate(encoded)
	if err != nil {
		return errors.New("FIXTURE_CA_FAILED")
	}
	pool := x509.NewCertPool()
	pool.AddCert(certificate)
	ca := &authority{certificate: certificate, key: key, pool: pool}
	serverCertificate, serverKey, err := ca.issue("drop-server", true)
	if err != nil {
		return err
	}
	private, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return errors.New("FIXTURE_KEY_FAILED")
	}
	for _, file := range []struct {
		name, kind string
		data       []byte
	}{{"ca.crt", "CERTIFICATE", encoded}, {"ca.key", "EC PRIVATE KEY", private},
		{"server.crt", "CERTIFICATE", serverCertificate}, {"server.key", "EC PRIVATE KEY", serverKey}} {
		if err := writeExclusive(filepath.Join(directory, file.name), file.kind, file.data); err != nil {
			return err
		}
	}
	return nil
}

func readPEM(path, kind string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() > 32768 {
		return nil, errors.New("FIXTURE_FILE_INVALID")
	}
	data, err := os.ReadFile(path)
	if err != nil || len(data) > 32768 {
		return nil, errors.New("FIXTURE_FILE_INVALID")
	}
	block, rest := pem.Decode(data)
	if block == nil || block.Type != kind || len(rest) != 0 {
		return nil, errors.New("FIXTURE_PEM_INVALID")
	}
	return block.Bytes, nil
}

func loadAuthority(directory, runID string) (*authority, error) {
	encoded, err := readPEM(filepath.Join(directory, "ca.crt"), "CERTIFICATE")
	if err != nil {
		return nil, err
	}
	certificate, err := x509.ParseCertificate(encoded)
	if err != nil || !certificate.IsCA || certificate.Subject.CommonName != caPrefix+runID ||
		time.Now().Before(certificate.NotBefore) || time.Now().After(certificate.NotAfter) ||
		certificate.NotAfter.Sub(certificate.NotBefore) > 3*time.Hour {
		return nil, errors.New("FIXTURE_CA_INVALID")
	}
	encoded, err = readPEM(filepath.Join(directory, "ca.key"), "EC PRIVATE KEY")
	if err != nil {
		return nil, err
	}
	key, err := x509.ParseECPrivateKey(encoded)
	if err != nil || key.Curve != elliptic.P256() || !key.PublicKey.Equal(certificate.PublicKey) {
		return nil, errors.New("FIXTURE_KEY_INVALID")
	}
	pool := x509.NewCertPool()
	pool.AddCert(certificate)
	return &authority{certificate: certificate, key: key, pool: pool}, nil
}

func (ca *authority) issue(name string, server bool) ([]byte, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, errors.New("FIXTURE_KEY_FAILED")
	}
	number, err := serial()
	if err != nil {
		return nil, nil, errors.New("FIXTURE_SERIAL_FAILED")
	}
	certificate := &x509.Certificate{SerialNumber: number, Subject: pkix.Name{CommonName: name},
		NotBefore: ca.certificate.NotBefore, NotAfter: ca.certificate.NotAfter, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	if server {
		certificate.DNSNames = []string{"drop-server"}
		certificate.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
	} else {
		certificate.DNSNames = []string{name}
	}
	encoded, err := x509.CreateCertificate(rand.Reader, certificate, ca.certificate, &key.PublicKey, ca.key)
	if err != nil {
		return nil, nil, errors.New("FIXTURE_CERTIFICATE_FAILED")
	}
	private, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return nil, nil, errors.New("FIXTURE_KEY_FAILED")
	}
	return encoded, private, nil
}

func (ca *authority) client(id string) (tls.Certificate, error) {
	certificate, key, err := ca.issue("drop-agent-"+id, false)
	if err != nil {
		return tls.Certificate{}, err
	}
	return tls.X509KeyPair(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate}),
		pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: key}))
}
