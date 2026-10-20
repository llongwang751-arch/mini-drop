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
func (s *Store) PresignGetFor(ctx context.Context, key string, ttl time.Duration) (string, error) {
	// Integrity metadata describes exact stored bytes. A retained object
	// Content-Encoding must not make browser fetch/download decode those bytes.
	// Sign the response override so it cannot be removed from an authorized URL.
	u, err := s.Client.PresignedGetObject(ctx, s.Bucket, key, ttl, url.Values{"response-content-encoding": {"identity"}})
	if err != nil {
		return "", err
	}
	return BrowserURL(s.PublicURL, u), nil
}

// Nginx forwards /objects with the original internal Host used in the S3 signature.
func BrowserURL(public string, u *url.URL) string {
	return strings.TrimSuffix(public, "/") + "/objects" + u.EscapedPath() + "?" + u.RawQuery
}
