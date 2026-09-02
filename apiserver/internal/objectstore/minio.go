package objectstore

import (
	"context"
	"io"
	"net/url"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type Object struct {
	Body        io.ReadCloser
	Size        int64
	ContentType string
}

type Store interface {
	Open(context.Context, string, string) (Object, error)
	PresignPut(context.Context, string, string, time.Duration) (*url.URL, error)
}

func (m *MinIO) PresignPut(
	ctx context.Context, bucket, key string, expiry time.Duration,
) (*url.URL, error) {
	return m.signer.PresignedPutObject(ctx, bucket, key, expiry)
}

type MinIO struct {
	client *minio.Client
	signer *minio.Client
}

func New(endpoint, accessKey, secretKey string, secure bool) (*MinIO, error) {
	return NewWithSignerEndpoint(
		endpoint, endpoint, accessKey, secretKey, secure, secure,
	)
}

func NewWithSignerEndpoint(
	endpoint, signerEndpoint, accessKey, secretKey string,
	secure, signerSecure bool,
) (*MinIO, error) {
	client, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: secure,
		// MinIO uses the S3 default region unless configured otherwise. Pinning
		// it keeps presigning local and avoids a bucket-location network call.
		Region: "us-east-1",
	})
	if err != nil {
		return nil, err
	}
	signer, err := minio.New(signerEndpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: signerSecure,
		Region: "us-east-1",
	})
	if err != nil {
		return nil, err
	}
	return &MinIO{client: client, signer: signer}, nil
}

func (m *MinIO) Open(ctx context.Context, bucket, key string) (Object, error) {
	info, err := m.client.StatObject(ctx, bucket, key, minio.StatObjectOptions{})
	if err != nil {
		return Object{}, err
	}
	body, err := m.client.GetObject(ctx, bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return Object{}, err
	}
	return Object{Body: body, Size: info.Size, ContentType: info.ContentType}, nil
}
