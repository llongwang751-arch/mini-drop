// Package storage：封装对象写入、哈希与短期下载/上传授权；浏览器和采集 Agent 均不持有对象存储长期密钥。
// 下载通过同源代理保持签名 Host，并把 identity 编码覆盖纳入签名，保证浏览器取得的字节与制品哈希一致。
package storage

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type Store struct {
	Client    *minio.Client
	Bucket    string
	PublicURL string
}

func New(endpoint, access, secret, bucket, public string) (*Store, error) {
	secure := false
	if strings.HasPrefix(endpoint, "https://") {
		secure = true
	}
	endpoint = strings.TrimPrefix(strings.TrimPrefix(endpoint, "https://"), "http://")
	client, err := minio.New(endpoint, &minio.Options{Creds: credentials.NewStaticV4(access, secret, ""), Secure: secure, Region: "us-east-1"})
	if err != nil {
		return nil, err
	}
	return &Store{Client: client, Bucket: bucket, PublicURL: strings.TrimSuffix(public, "/")}, nil
}
func (s *Store) Ready(ctx context.Context) error {
	ok, err := s.Client.BucketExists(ctx, s.Bucket)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("artifact bucket does not exist")
	}
	return nil
}
func (s *Store) PutText(ctx context.Context, key, text string) (int64, string, error) {
	return s.PutBytes(ctx, key, []byte(text), "text/plain")
}
func (s *Store) PutBytes(ctx context.Context, key string, data []byte, contentType string) (int64, string, error) {
	sum := sha256.Sum256(data)
	hash := hex.EncodeToString(sum[:])
	result, err := s.Client.PutObject(ctx, s.Bucket, key, bytes.NewReader(data), int64(len(data)), minio.PutObjectOptions{ContentType: contentType, UserMetadata: map[string]string{"sha256": hash}})
	return result.Size, hash, err
}
func (s *Store) PresignPut(ctx context.Context, key string) (string, error) {
	u, err := s.Client.PresignedPutObject(ctx, s.Bucket, key, 10*time.Minute)
	if err != nil {
		return "", err
	}
	return u.String(), nil
}
func (s *Store) PresignGet(ctx context.Context, key string) (string, error) {
	return s.PresignGetFor(ctx, key, 5*time.Minute)
}

// PresignGetFor 根据调用方请求的有限 TTL 签名下载，同时把 identity 响应编码覆盖包含在签名内；大小和 SHA256 始终描述原始存储字节。
func (s *Store) PresignGetFor(ctx context.Context, key string, ttl time.Duration) (string, error) {
	// 完整性元数据描述实际存储的原始字节。对象原有的 Content-Encoding
	// 不能让浏览器下载时自动解压并改变这些字节；必须把响应覆盖项一起签名，
	// 防止调用方从已授权 URL 中删除该约束而绕过大小、哈希语义。
	u, err := s.Client.PresignedGetObject(ctx, s.Bucket, key, ttl, url.Values{"response-content-encoding": {"identity"}})
	if err != nil {
		return "", err
	}
	return BrowserURL(s.PublicURL, u), nil
}

// BrowserURL 通过同源 Nginx 转发 /objects，并保留 S3 签名使用的内部 Host，避免改写破坏授权。
func BrowserURL(public string, u *url.URL) string {
	return strings.TrimSuffix(public, "/") + "/objects" + u.EscapedPath() + "?" + u.RawQuery
}
