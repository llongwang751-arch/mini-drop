package objectstore

import (
	"context"
	"io"

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
}

type MinIO struct {
	client *minio.Client
}

func New(endpoint, accessKey, secretKey string, secure bool) (*MinIO, error) {
	client, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: secure,
	})
	if err != nil {
		return nil, err
	}
	return &MinIO{client: client}, nil
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
